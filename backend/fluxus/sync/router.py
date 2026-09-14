"""Synchronisation delta pour le mode hors-ligne.

GET  /api/sync?since=<ISO>  -> tout ce qui a changé depuis `since` (flux, catégories, entrées)
POST /api/sync              -> rejoue les changements d'état locaux (lu / favori) horodatés
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Auth
from ..db import get_session
from ..entries.router import to_entry_out
from ..models import Category, Entry, Feed, Summary
from ..schemas import CategoryOut, EntryOut, FeedOut, SummaryOut

router = APIRouter(prefix="/api/sync", tags=["sync"])
Session = Annotated[AsyncSession, Depends(get_session)]

MAX_ENTRIES = 500


class SyncPull(BaseModel):
    server_time: datetime
    categories: list[CategoryOut]
    feeds: list[FeedOut]
    feed_ids: list[int]
    entries: list[EntryOut]
    summaries: list[SummaryOut]
    deleted_entry_ids: list[int]
    has_more: bool
    next_since: datetime


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
    limit: int = Query(200, ge=1, le=MAX_ENTRIES),
    include_content: bool = Query(True),
) -> SyncPull:
    now = datetime.now(UTC)
    # Légère marge pour ne rien perdre entre deux transactions concurrentes.
    since_eff = (since - timedelta(seconds=2)) if since else None

    cats = (await session.execute(select(Category).order_by(Category.position))).scalars().all()
    feeds = (await session.execute(select(Feed))).scalars().all()

    stmt = select(Entry, exists().where(Summary.entry_id == Entry.id).correlate(Entry))
    if since_eff:
        stmt = stmt.where(Entry.updated_at > since_eff)
    else:
        # Première synchronisation : seulement les 30 derniers jours pour rester raisonnable.
        stmt = stmt.where(Entry.published_at > now - timedelta(days=30))
    stmt = stmt.order_by(Entry.updated_at.asc(), Entry.id.asc()).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    entries_out: list[EntryOut] = []
    for entry, has_summary in rows:
        out = to_entry_out(entry, bool(has_summary))
        if not include_content:
            out.content_html = None
        entries_out.append(out)

    entry_ids = [e.id for e in entries_out]
    summaries: list[SummaryOut] = []
    if entry_ids:
        srows = (await session.execute(select(Summary).where(Summary.entry_id.in_(entry_ids)))).scalars().all()
        summaries = [SummaryOut.model_validate(s, from_attributes=True) for s in srows]

    next_since = rows[-1][0].updated_at if has_more and rows else now
    return SyncPull(
        server_time=now,
        categories=[CategoryOut(id=c.id, title=c.title, position=c.position) for c in cats],
        feeds=[FeedOut.model_validate(f, from_attributes=True) for f in feeds],
        feed_ids=[f.id for f in feeds],
        entries=entries_out,
        summaries=summaries,
        deleted_entry_ids=[],
        has_more=has_more,
        next_since=next_since,
    )


@router.post("", response_model=SyncPushResult)
async def push(body: SyncPush, _: Auth, session: Session) -> SyncPushResult:
    """Dernière écriture gagne : un changement local plus ancien que la version serveur est ignoré."""
    applied = ignored = 0
    ids = [c.entry_id for c in body.changes]
    if not ids:
        return SyncPushResult(applied=0, ignored=0, server_time=datetime.now(UTC))
    entries = {e.id: e for e in (await session.execute(select(Entry).where(Entry.id.in_(ids)))).scalars()}
    for change in sorted(body.changes, key=lambda c: c.at):
        entry = entries.get(change.entry_id)
        if entry is None:
            ignored += 1
            continue
        change_at = change.at if change.at.tzinfo else change.at.replace(tzinfo=UTC)
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
        entry.updated_at = max(datetime.now(UTC), change_at)
        applied += 1
    await session.commit()
    return SyncPushResult(applied=applied, ignored=ignored, server_time=datetime.now(UTC))
