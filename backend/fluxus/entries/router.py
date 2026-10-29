from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import maintenance
from ..auth import Auth
from ..db import get_session
from ..extract.clean import html_to_text
from ..extract.jobs import run_extraction
from ..models import Entry, Feed, SmartFeedMatch, Summary
from ..schemas import EntryListItem, EntryOut, EntryPage, EntryPatch, FetchContentIn, MarkReadIn
from ..smartfeeds.service import smart_feed_ids_for

router = APIRouter(prefix="/api/entries", tags=["entries"])
Session = Annotated[AsyncSession, Depends(get_session)]

PAGE_SIZE = 50


def _encode_cursor(published_at: datetime, entry_id: int) -> str:
    raw = json.dumps([published_at.isoformat(), entry_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        published, entry_id = json.loads(base64.urlsafe_b64decode(padded))
        dt = datetime.fromisoformat(published)
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)), int(entry_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Curseur invalide") from exc


def _excerpt(entry: Entry, length: int = 220) -> str:
    text = html_to_text(entry.summary_html) if entry.summary_html else (entry.content_text or "")
    text = text.replace("\n", " ").strip()
    return text[:length] + ("…" if len(text) > length else "")


def to_list_item(entry: Entry, has_summary: bool, smart_feed_ids: list[int] | None = None) -> EntryListItem:
    meta = entry.meta or {}
    return EntryListItem(
        id=entry.id,
        feed_id=entry.feed_id,
        title=entry.title,
        url=entry.url,
        author=entry.author,
        published_at=entry.published_at,
        read=entry.read,
        starred=entry.starred,
        excerpt=_excerpt(entry),
        cover=meta.get("cover"),
        youtube_id=meta.get("youtube_id"),
        has_content=entry.extraction_status == "done",
        has_summary=has_summary,
        smart_feed_ids=smart_feed_ids or [],
        updated_at=entry.updated_at,
    )


def to_entry_out(entry: Entry, has_summary: bool, smart_feed_ids: list[int] | None = None) -> EntryOut:
    base = to_list_item(entry, has_summary, smart_feed_ids)
    return EntryOut(
        **base.model_dump(),
        summary_html=entry.summary_html,
        content_html=entry.content_html,
        extraction_status=entry.extraction_status,
        extraction_error=entry.extraction_error,
        meta=entry.meta or {},
    )


def _has_summary_expr():
    return exists().where(Summary.entry_id == Entry.id).correlate(Entry)


@router.get("", response_model=EntryPage)
async def list_entries(
    _: Auth,
    session: Session,
    feed: int | None = None,
    category: int | None = None,
    status_filter: Literal["all", "unread", "read"] = Query("all", alias="status"),
    starred: bool | None = None,
    q: str | None = Query(None, max_length=200),
    cursor: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=200),
    order: Literal["desc", "asc"] = "desc",
) -> EntryPage:
    stmt = select(Entry, _has_summary_expr().label("has_summary"))
    if feed is not None:
        stmt = stmt.where(Entry.feed_id == feed)
    if category is not None:
        stmt = stmt.join(Feed, Feed.id == Entry.feed_id).where(Feed.category_id == category)
    if status_filter == "unread":
        stmt = stmt.where(Entry.read.is_(False))
    elif status_filter == "read":
        stmt = stmt.where(Entry.read.is_(True))
    if starred is not None:
        stmt = stmt.where(Entry.starred.is_(starred))
    if q:
        tsquery = func.websearch_to_tsquery("simple", q)
        stmt = stmt.where(Entry.search_vector.op("@@")(tsquery))
    if cursor:
        published, entry_id = _decode_cursor(cursor)
        if order == "desc":
            stmt = stmt.where(
                or_(Entry.published_at < published, and_(Entry.published_at == published, Entry.id < entry_id))
            )
        else:
            stmt = stmt.where(
                or_(Entry.published_at > published, and_(Entry.published_at == published, Entry.id > entry_id))
            )
    if order == "desc":
        stmt = stmt.order_by(Entry.published_at.desc(), Entry.id.desc())
    else:
        stmt = stmt.order_by(Entry.published_at.asc(), Entry.id.asc())
    stmt = stmt.limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    sf_ids = await smart_feed_ids_for(session, [entry.id for entry, _ in rows])
    items = [to_list_item(entry, bool(has_summary), sf_ids.get(entry.id)) for entry, has_summary in rows]
    next_cursor = _encode_cursor(rows[-1][0].published_at, rows[-1][0].id) if has_more and rows else None
    return EntryPage(items=items, next_cursor=next_cursor)


@router.get("/{entry_id}", response_model=EntryOut)
async def get_entry(entry_id: int, _: Auth, session: Session) -> EntryOut:
    row = (
        await session.execute(
            select(Entry, _has_summary_expr()).where(Entry.id == entry_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    sf_ids = await smart_feed_ids_for(session, [entry_id])
    return to_entry_out(row[0], bool(row[1]), sf_ids.get(entry_id))


@router.patch("/{entry_id}", response_model=EntryOut)
async def patch_entry(entry_id: int, body: EntryPatch, _: Auth, session: Session) -> EntryOut:
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    now = datetime.now(UTC)
    if body.read is not None:
        entry.read = body.read
        entry.read_changed_at = now  # horloge du champ : la sync compare les actions utilisateur à elle
    if body.starred is not None:
        entry.starred = body.starred
        entry.starred_changed_at = now
    entry.updated_at = now
    await session.commit()
    await session.refresh(entry)
    has_summary = (await session.execute(select(_has_summary_expr()).where(Entry.id == entry_id))).scalar()
    sf_ids = await smart_feed_ids_for(session, [entry_id])
    return to_entry_out(entry, bool(has_summary), sf_ids.get(entry_id))


@router.post("/mark-read")
async def mark_read(body: MarkReadIn, _: Auth, session: Session) -> dict:
    now = datetime.now(UTC)
    stmt = update(Entry).values(read=body.read, read_changed_at=now, updated_at=now)
    if body.entry_ids:
        stmt = stmt.where(Entry.id.in_(body.entry_ids))
    elif body.feed_id is not None:
        stmt = stmt.where(Entry.feed_id == body.feed_id)
    elif body.category_id is not None:
        feed_ids = select(Feed.id).where(Feed.category_id == body.category_id)
        stmt = stmt.where(Entry.feed_id.in_(feed_ids))
    elif body.smart_feed_id is not None:
        matched = select(SmartFeedMatch.entry_id).where(SmartFeedMatch.smart_feed_id == body.smart_feed_id)
        stmt = stmt.where(Entry.id.in_(matched))
    elif body.starred_only:
        stmt = stmt.where(Entry.starred.is_(True))
    elif not body.all:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Préciser une cible (entry_ids, feed_id, category_id, smart_feed_id ou all)")
    if body.before is not None:
        stmt = stmt.where(Entry.published_at <= body.before)
    stmt = stmt.where(Entry.read.is_(not body.read))
    result = await session.execute(stmt)
    await session.commit()
    return {"updated": result.rowcount or 0}


@router.post("/{entry_id}/fetch-content", response_model=EntryOut)
async def fetch_content(entry_id: int, body: FetchContentIn, _: Auth, session: Session) -> EntryOut:
    """Extraction synchrone de l'article complet (l'utilisateur attend le résultat)."""
    async with maintenance.shared():
        return await _fetch_content(entry_id, body, session)


async def _fetch_content(entry_id: int, body: FetchContentIn, session: AsyncSession) -> EntryOut:
    result = await session.execute(
        select(Entry).options(selectinload(Entry.feed)).where(Entry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    await run_extraction(session, entry, body.mode)
    entry.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(entry)
    has_summary = (await session.execute(select(_has_summary_expr()).where(Entry.id == entry_id))).scalar()
    sf_ids = await smart_feed_ids_for(session, [entry_id])
    return to_entry_out(entry, bool(has_summary), sf_ids.get(entry_id))


@router.get("/{entry_id}/summaries")
async def list_summaries(entry_id: int, _: Auth, session: Session) -> list[dict]:
    rows = (
        await session.execute(
            select(Summary).where(Summary.entry_id == entry_id).order_by(Summary.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": s.id,
            "entry_id": s.entry_id,
            "kind": s.kind,
            "provider": s.provider,
            "model": s.model,
            "text_md": s.text_md,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "created_at": s.created_at,
        }
        for s in rows
    ]
