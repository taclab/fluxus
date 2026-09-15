"""Synchronisation delta pour le mode hors-ligne.

GET  /api/sync?since=<ISO>  -> tout ce qui a changé depuis `since` (flux, catégories, entrées)
POST /api/sync              -> rejoue les changements d'état locaux (lu / favori) horodatés
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Auth
from ..db import get_session
from ..entries.router import to_entry_out
from ..models import Category, Entry, Feed, SmartFeed, Summary
from ..schemas import CategoryOut, EntryOut, FeedOut, SummaryOut
from ..settings_store import get_setting
from ..smartfeeds.router import SmartFeedOut
from ..smartfeeds.service import smart_feed_ids_for

router = APIRouter(prefix="/api/sync", tags=["sync"])
Session = Annotated[AsyncSession, Depends(get_session)]

MAX_ENTRIES = 500


class SyncPull(BaseModel):
    server_time: datetime
    # Horizon de conservation locale (jours), réglage `sync_prefetch_days`.
    retention_days: int
    categories: list[CategoryOut]
    feeds: list[FeedOut]
    feed_ids: list[int]
    smart_feeds: list[SmartFeedOut]
    entries: list[EntryOut]
    summaries: list[SummaryOut]
    deleted_entry_ids: list[int]
    has_more: bool
    # Curseur opaque (updated_at, id) pour la page suivante ; None quand has_more est faux.
    next_cursor: str | None
    # Conservé pour compatibilité : à stocker comme `since` une fois la passe terminée.
    next_since: datetime


def _encode_cursor(updated_at: datetime, entry_id: int, min_published: datetime | None) -> str:
    payload = [updated_at.isoformat(), entry_id, min_published.isoformat() if min_published else None]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _aware(dt: datetime) -> datetime:
    """Une date sans fuseau est interprétée en UTC (asyncpg refuse de comparer naïf et timestamptz)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _decode_cursor(cursor: str) -> tuple[datetime, int, datetime | None]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, entry_id, min_pub = json.loads(base64.urlsafe_b64decode(padded))
        return _aware(datetime.fromisoformat(ts)), int(entry_id), _aware(datetime.fromisoformat(min_pub)) if min_pub else None
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Curseur de synchronisation invalide") from exc


class StateChange(BaseModel):
    entry_id: int
    read: bool | None = None
    starred: bool | None = None
    at: datetime


class SyncPush(BaseModel):
    changes: list[StateChange]


class SyncPushResult(BaseModel):
    applied: int
    ignored: int
    server_time: datetime


@router.get("", response_model=SyncPull)
async def pull(
    _: Auth,
    session: Session,
    since: datetime | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(200, ge=1, le=MAX_ENTRIES),
    include_content: bool = Query(True),
) -> SyncPull:
    """Première page : `since` (dernière passe réussie). Pages suivantes : `cursor` renvoyé par la page précédente."""
    now = datetime.now(UTC)
    try:
        retention_days = max(1, min(365, int(await get_setting(session, "sync_prefetch_days") or 30)))
    except ValueError:
        retention_days = 30

    cats = (await session.execute(select(Category).order_by(Category.position))).scalars().all()
    feeds = (await session.execute(select(Feed))).scalars().all()
    smart_feeds = (await session.execute(select(SmartFeed).order_by(SmartFeed.position, SmartFeed.name))).scalars().all()

    stmt = select(Entry, exists().where(Summary.entry_id == Entry.id).correlate(Entry))
    min_published: datetime | None = None
    if cursor:
        cur_ts, cur_id, min_published = _decode_cursor(cursor)
        stmt = stmt.where(or_(Entry.updated_at > cur_ts, and_(Entry.updated_at == cur_ts, Entry.id > cur_id)))
    elif since:
        # Légère marge pour ne rien perdre entre deux transactions concurrentes.
        stmt = stmt.where(Entry.updated_at > _aware(since) - timedelta(seconds=2))
    else:
        # Première synchronisation : seulement les N derniers jours (réglage sync_prefetch_days).
        min_published = now - timedelta(days=retention_days)
    if min_published is not None:
        # Les favoris sont toujours envoyés, même anciens : ils sont peu nombreux et voulus.
        stmt = stmt.where(or_(Entry.published_at > min_published, Entry.starred.is_(True)))
    stmt = stmt.order_by(Entry.updated_at.asc(), Entry.id.asc()).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    sf_ids = await smart_feed_ids_for(session, [entry.id for entry, _ in rows])
    entries_out: list[EntryOut] = []
    for entry, has_summary in rows:
        out = to_entry_out(entry, bool(has_summary), sf_ids.get(entry.id))
        if not include_content:
            out.content_html = None
        entries_out.append(out)

    entry_ids = [e.id for e in entries_out]
    summaries: list[SummaryOut] = []
    if entry_ids:
        srows = (await session.execute(select(Summary).where(Summary.entry_id.in_(entry_ids)))).scalars().all()
        summaries = [SummaryOut.model_validate(s, from_attributes=True) for s in srows]

    last = rows[-1][0] if rows else None
    next_cursor = _encode_cursor(last.updated_at, last.id, min_published) if has_more and last else None
    return SyncPull(
        server_time=now,
        retention_days=retention_days,
        categories=[CategoryOut(id=c.id, title=c.title, position=c.position) for c in cats],
        feeds=[FeedOut.model_validate(f, from_attributes=True) for f in feeds],
        feed_ids=[f.id for f in feeds],
        smart_feeds=[SmartFeedOut.model_validate(s, from_attributes=True) for s in smart_feeds],
        entries=entries_out,
        summaries=summaries,
        deleted_entry_ids=[],
        has_more=has_more,
        next_cursor=next_cursor,
        next_since=now,
    )


@router.post("", response_model=SyncPushResult)
async def push(body: SyncPush, _: Auth, session: Session) -> SyncPushResult:
    """Dernière écriture gagne : un changement local plus ancien que la version serveur est ignoré."""
    applied = ignored = 0
    ids = [c.entry_id for c in body.changes]
    if not ids:
        return SyncPushResult(applied=0, ignored=0, server_time=datetime.now(UTC))
    entries = {e.id: e for e in (await session.execute(select(Entry).where(Entry.id.in_(ids)))).scalars()}
    now = datetime.now(UTC)
    # Une horloge client en avance ne doit pas projeter updated_at dans le futur (l'entrée serait
    # renvoyée à chaque pull et tout changement ultérieur ignoré comme « périmé »).
    normalized = [(min(_aware(c.at), now + timedelta(seconds=30)), c) for c in body.changes]
    for change_at, change in sorted(normalized, key=lambda x: x[0]):
        entry = entries.get(change.entry_id)
        if entry is None:
            ignored += 1
            continue
        if entry.updated_at and entry.updated_at > change_at and (
            (change.read is not None and entry.read != change.read)
            or (change.starred is not None and entry.starred != change.starred)
        ):
            # Le serveur a une version plus récente qui contredit le changement local.
            ignored += 1
            continue
        if change.read is not None:
            entry.read = change.read
        if change.starred is not None:
            entry.starred = change.starred
        entry.updated_at = now
        applied += 1
    await session.commit()
    return SyncPushResult(applied=applied, ignored=ignored, server_time=datetime.now(UTC))
