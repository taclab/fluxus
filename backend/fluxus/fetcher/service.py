"""Rafraîchissement d'un flux : téléchargement conditionnel, parsing, insertion des nouveautés."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Entry, Feed
from .http import UnsafeURLError, assert_public_url, make_client
from .parse import ParsedFeed, parse_feed

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 10 * 1024 * 1024


class FetchResult:
    def __init__(self, new_entries: int = 0, not_modified: bool = False) -> None:
        self.new_entries = new_entries
        self.not_modified = not_modified


def _next_interval(feed: Feed, parsed: ParsedFeed | None, new_entries: int) -> int:
    """Intervalle adaptatif : dense si le flux publie beaucoup, plus lâche sinon."""
    settings = get_settings()
    lo, hi = settings.min_fetch_interval_min, settings.max_fetch_interval_min
    current = feed.fetch_interval_min or settings.default_fetch_interval_min
    if parsed is None:
        return current
    if new_entries >= 5:
        return max(lo, current // 2)
    if new_entries == 0:
        return min(hi, int(current * 1.5))
    return current


def _favicon_fallback(site_url: str | None) -> str | None:
    if not site_url:
        return None
    parts = urlsplit(site_url)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico"


async def refresh_feed(session: AsyncSession, feed: Feed) -> FetchResult:
    now = datetime.now(UTC)
    try:
        assert_public_url(feed.url)
    except UnsafeURLError as exc:
        return await _record_error(session, feed, str(exc), now)

    headers = {}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified

    try:
        async with make_client() as client:
            response = await client.get(feed.url, headers=headers)
    except httpx.HTTPError as exc:
        return await _record_error(session, feed, f"Erreur réseau: {exc}", now)

    if response.status_code == 304:
        feed.last_fetched_at = now
        feed.error_count = 0
        feed.last_error = None
        feed.fetch_interval_min = _next_interval(feed, None, 0)
        feed.next_fetch_at = now + timedelta(minutes=feed.fetch_interval_min)
        return FetchResult(not_modified=True)

    if response.status_code >= 400:
        return await _record_error(session, feed, f"HTTP {response.status_code}", now)

    body = response.content[:MAX_FEED_BYTES]
    parsed = parse_feed(body, str(response.url))
    if parsed.bozo_error and not parsed.entries:
        return await _record_error(session, feed, f"Flux illisible: {parsed.bozo_error}", now)

    if not feed.title and parsed.title:
        feed.title = parsed.title[:500]
    if parsed.site_url:
        feed.site_url = parsed.site_url
    if parsed.description and not feed.description:
        feed.description = parsed.description[:2000]
    if not feed.icon_url:
        feed.icon_url = parsed.icon_url or _favicon_fallback(feed.site_url)
    if parsed.is_youtube:
        feed.is_youtube = True

    new_count = await _insert_new_entries(session, feed, parsed)

    feed.etag = response.headers.get("ETag")
    feed.last_modified = response.headers.get("Last-Modified")
    feed.last_fetched_at = now
    feed.error_count = 0
    feed.last_error = None
    feed.fetch_interval_min = _next_interval(feed, parsed, new_count)
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
    for pe in parsed.entries:
        if pe.guid in known_guids or pe.hash in known_hashes or pe.guid in seen_in_batch:
            continue
        seen_in_batch.add(pe.guid)
        session.add(
            Entry(
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
        )
        new_count += 1
    await session.flush()
    return new_count


async def _record_error(session: AsyncSession, feed: Feed, message: str, now: datetime) -> FetchResult:
    settings = get_settings()
    feed.error_count += 1
    feed.last_error = message[:2000]
    feed.last_fetched_at = now
    # Recul exponentiel plafonné : 2^n × intervalle courant.
    backoff = min(
        settings.max_fetch_interval_min,
        (feed.fetch_interval_min or settings.default_fetch_interval_min) * (2 ** min(feed.error_count, 6)),
    )
    feed.next_fetch_at = now + timedelta(minutes=backoff)
    log.info("flux #%s en erreur (%s): %s", feed.id, feed.error_count, message)
    return FetchResult()


async def fetch_feed_preview(url: str) -> tuple[ParsedFeed, str]:
    """Télécharge et parse un flux sans rien écrire. Renvoie (flux parsé, URL finale)."""
    assert_public_url(url)
    async with make_client() as client:
        response = await client.get(url)
    response.raise_for_status()
    return parse_feed(response.content[:MAX_FEED_BYTES], str(response.url)), str(response.url)
