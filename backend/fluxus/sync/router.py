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
from sqlalchemy import and_, exists, or_, select, text
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


class SyncConflict(BaseModel):
    """Changement refusé : l'état serveur courant, pour que le client réaligne son cache."""

    entry_id: int
    read: bool
    starred: bool


class SyncPushResult(BaseModel):
    applied: int
    ignored: int
    conflicts: list[SyncConflict] = []
    server_time: datetime


async def _visibility_horizon(session: AsyncSession, now: datetime) -> datetime:
    """Instant à partir duquel le client peut reprendre sans rien perdre.

    Une transaction encore ouverte (job d'extraction, évaluation de flux intelligent…) a pu fixer
    `updated_at` bien avant son commit : un `since = now` la rendrait invisible pour toujours. On
    renvoie donc le début de la plus ancienne transaction en cours sur cette base ; le client relira
    au pire quelques minutes de changements déjà vus (idempotent), jamais moins.
    """
    horizon = (
        await session.execute(
            text(
                "SELECT min(xact_start) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() AND xact_start IS NOT NULL"
            )
        )
    ).scalar_one()
    if horizon is None:
        return now
    return min(now, _aware(horizon))


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
    horizon = await _visibility_horizon(session, now)
    return SyncPull(
        server_time=horizon,
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
        next_since=horizon,
    )


@router.post("", response_model=SyncPushResult)
async def push(body: SyncPush, _: Auth, session: Session) -> SyncPushResult:
    """Dernière écriture gagne, **champ par champ** et à l'horloge des actions.

    Chaque champ (`read`, `starred`) a sa propre horloge (`read_changed_at`, `starred_changed_at`),
    fixée à la date de l'action côté client (bornée à now + 30 s). Un changement n'est refusé que si
    ce champ a été modifié plus récemment : un résumé, une extraction ou un recalcul de
    correspondances (qui avancent `updated_at`) n'invalident plus un favori posé hors ligne, et les
    actions successives d'un même lot s'appliquent dans l'ordre. Les refus sont renvoyés avec l'état
    serveur courant pour que le client réaligne son cache.
    """
    applied = ignored = 0
    conflicts: list[SyncConflict] = []
    ids = [c.entry_id for c in body.changes]
    if not ids:
        return SyncPushResult(applied=0, ignored=0, server_time=datetime.now(UTC))
    entries = {e.id: e for e in (await session.execute(select(Entry).where(Entry.id.in_(ids)))).scalars()}
    now = datetime.now(UTC)
    # Une horloge client en avance ne doit pas projeter les dates dans le futur : l'horloge du champ
    # serait alors postérieure à toute action normale et refuserait les changements suivants.
    normalized = [(min(_aware(c.at), now), c) for c in body.changes]
    for change_at, change in sorted(normalized, key=lambda x: x[0]):
        entry = entries.get(change.entry_id)
        if entry is None:
            ignored += 1
            continue
        touched = refused = False
        # Horloge NULL = champ jamais modifié par l'utilisateur (la migration a rempli l'existant) : tout passe.
        if change.read is not None:
            if entry.read != change.read and entry.read_changed_at and entry.read_changed_at > change_at:
                refused = True
            else:
                entry.read = change.read
                entry.read_changed_at = max(change_at, entry.read_changed_at or change_at)
                touched = True
        if change.starred is not None:
            if entry.starred != change.starred and entry.starred_changed_at and entry.starred_changed_at > change_at:
                refused = True
            else:
                entry.starred = change.starred
                entry.starred_changed_at = max(change_at, entry.starred_changed_at or change_at)
                touched = True
        if touched:
            entry.updated_at = now
            applied += 1
        if refused:
            ignored += 1
            conflicts.append(SyncConflict(entry_id=entry.id, read=entry.read, starred=entry.starred))
    await session.commit()
    return SyncPushResult(applied=applied, ignored=ignored, conflicts=conflicts, server_time=datetime.now(UTC))
