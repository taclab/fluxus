"""Rafraîchissement d'un flux : téléchargement conditionnel, parsing, insertion des nouveautés."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..jobs.queue import enqueue
from ..models import Entry, Feed
from .http import UnsafeURLError, assert_public_url_async, make_client, safe_get
from .parse import ParsedFeed, parse_feed

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 10 * 1024 * 1024


class FetchResult:
    def __init__(self, new_entries: int = 0, not_modified: bool = False) -> None:
        self.new_entries = new_entries
        self.not_modified = not_modified


CADENCE_WINDOW = timedelta(days=14)


def _next_interval(feed: Feed, recent_entries: int) -> int:
    """Intervalle adaptatif fondé sur le rythme de publication observé : deux passages par écart moyen
    entre les articles des 14 derniers jours, borné (10 min – 4 h par défaut). Un flux muet est relu au
    plafond, jamais au-delà : un rythme qui reprend est repéré en quelques heures. L'intervalle fixe
    choisi par l'utilisateur prime."""
    if feed.fetch_interval_fixed_min:
        return feed.fetch_interval_fixed_min
    settings = get_settings()
    lo, hi = settings.min_fetch_interval_min, settings.max_fetch_interval_min
    if recent_entries <= 0:
        return hi
    gap_min = CADENCE_WINDOW.total_seconds() / 60 / recent_entries
    return max(lo, min(hi, int(gap_min / 2)))


async def _recent_entry_count(session: AsyncSession, feed: Feed, now: datetime) -> int:
    """Articles publiés dans la fenêtre d'observation (les dates futures sont ignorées)."""
    return (
        await session.scalar(
            select(func.count()).where(
                Entry.feed_id == feed.id,
                Entry.published_at > now - CADENCE_WINDOW,
                Entry.published_at <= now,
            )
        )
    ) or 0


def _favicon_fallback(site_url: str | None) -> str | None:
    if not site_url:
        return None
    parts = urlsplit(site_url)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico"


async def refresh_feed(session: AsyncSession, feed: Feed) -> FetchResult:
    now = datetime.now(UTC)
    try:
        await assert_public_url_async(feed.url)
    except UnsafeURLError as exc:
        return await _record_error(session, feed, str(exc), now)

    headers = {}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified

    try:
        async with make_client() as client:
            response = await safe_get(client, feed.url, headers=headers, max_bytes=MAX_FEED_BYTES)
    except (httpx.HTTPError, UnsafeURLError) as exc:
        return await _record_error(session, feed, f"Erreur réseau: {exc}", now)

    if response.status_code == 304:
        feed.last_fetched_at = now
        feed.error_count = 0
        feed.last_error = None
        feed.fetch_interval_min = _next_interval(feed, await _recent_entry_count(session, feed, now))
        feed.next_fetch_at = now + timedelta(minutes=feed.fetch_interval_min)
        return FetchResult(not_modified=True)

    if response.status_code >= 400:
        return await _record_error(session, feed, f"HTTP {response.status_code}", now)

    # feedparser est synchrone et coûteux sur un gros flux : hors de la boucle d'événements.
    parsed = await asyncio.to_thread(parse_feed, response.content, str(response.url))
    if parsed.bozo_error and not parsed.entries:
        return await _record_error(session, feed, f"Flux illisible: {parsed.bozo_error}", now)

    if not feed.title and parsed.title:
        feed.title = parsed.title[:500]
    if parsed.site_url:
        feed.site_url = parsed.site_url[:2048]
    if parsed.description and not feed.description:
        feed.description = parsed.description[:2000]
    if not feed.icon_url:
        icon = parsed.icon_url or _favicon_fallback(feed.site_url)
        feed.icon_url = icon[:2048] if icon else None
    if parsed.is_youtube:
        feed.is_youtube = True

    new_count = await _insert_new_entries(session, feed, parsed)

    feed.etag = response.headers.get("ETag")
    feed.last_modified = response.headers.get("Last-Modified")
    feed.last_fetched_at = now
    feed.error_count = 0
    feed.last_error = None
    feed.fetch_interval_min = _next_interval(feed, await _recent_entry_count(session, feed, now))
    feed.next_fetch_at = now + timedelta(minutes=feed.fetch_interval_min)
    return FetchResult(new_entries=new_count)


async def _insert_new_entries(session: AsyncSession, feed: Feed, parsed: ParsedFeed) -> int:
    if not parsed.entries:
        return 0
    guids = [e.guid for e in parsed.entries]
    hashes = [e.hash for e in parsed.entries]
    existing = await session.execute(
        select(Entry.guid, Entry.hash).where(
            Entry.feed_id == feed.id, (Entry.guid.in_(guids)) | (Entry.hash.in_(hashes))
        )
    )
    known_guids: set[str] = set()
    known_hashes: set[str] = set()
    for guid, h in existing:
        known_guids.add(guid)
        known_hashes.add(h)

    new_count = 0
    seen_in_batch: set[str] = set()
    new_entries: list[Entry] = []
    for pe in parsed.entries:
        if pe.guid in known_guids or pe.hash in known_hashes or pe.guid in seen_in_batch or pe.hash in seen_in_batch:
            continue
        seen_in_batch.add(pe.guid)
        seen_in_batch.add(pe.hash)
        entry = Entry(
            feed_id=feed.id,
            guid=pe.guid,
            url=pe.url,
            title=pe.title,
            author=pe.author,
            published_at=pe.published_at,
            summary_html=pe.summary_html,
            hash=pe.hash,
            meta=pe.meta,
        )
        session.add(entry)
        new_entries.append(entry)
        new_count += 1
    await session.flush()
    # Flux intelligents : correspondances des nouveaux articles, puis résumés automatiques sous quotas.
    if new_entries:
        from ..smartfeeds.service import evaluate, schedule_auto_summaries
        from ..summarize.auto import schedule_feed_auto_summaries

        # Flux « tout résumer » d'abord : sa promesse porte sur chaque nouvel article, alors qu'un job de
        # flux intelligent peut être abandonné (règle modifiée). Clé de dédoublonnage commune : un seul résumé.
        await schedule_feed_auto_summaries(session, feed, [e.id for e in new_entries])
        pairs = await evaluate(session, feed, [e.id for e in new_entries])
        if pairs:
            await schedule_auto_summaries(session, pairs)
    # Mode d'extraction explicite (http ou browser) = « toujours récupérer l'article complet » :
    # on planifie l'extraction en arrière-plan pour que l'article soit lisible hors-ligne.
    if feed.extraction_mode in ("http", "browser"):
        for entry in new_entries:
            if entry.url:
                await enqueue(
                    session,
                    "extract_entry",
                    {"entry_id": entry.id, "mode": feed.extraction_mode},
                    dedupe_key=f"extract_entry:{entry.id}",
                    max_attempts=2,
                )
    return new_count


async def _record_error(session: AsyncSession, feed: Feed, message: str, now: datetime) -> FetchResult:
    settings = get_settings()
    feed.error_count += 1
    feed.last_error = message[:2000]
    feed.last_fetched_at = now
    # Recul exponentiel plafonné à 24 h : 2^n × intervalle courant (jamais moins que l'intervalle fixe).
    backoff = max(
        feed.fetch_interval_fixed_min or 0,
        min(
            settings.max_error_backoff_min,
            (feed.fetch_interval_min or settings.default_fetch_interval_min) * (2 ** min(feed.error_count, 6)),
        ),
    )
    feed.next_fetch_at = now + timedelta(minutes=backoff)
    log.info("flux #%s en erreur (%s): %s", feed.id, feed.error_count, message)
    return FetchResult()


async def fetch_feed_preview(url: str) -> tuple[ParsedFeed, str]:
    """Télécharge et parse un flux sans rien écrire. Renvoie (flux parsé, URL finale)."""
    async with make_client() as client:
        response = await safe_get(client, url, max_bytes=MAX_FEED_BYTES)
    response.raise_for_status()
    parsed = await asyncio.to_thread(parse_feed, response.content, str(response.url))
    return parsed, str(response.url)
