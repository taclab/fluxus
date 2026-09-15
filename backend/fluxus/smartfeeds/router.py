from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Auth
from ..db import get_session
from ..models import Entry, SmartFeed, SmartFeedMatch
from . import service
from .rules import InvalidDefinition, normalize_definition

router = APIRouter(prefix="/api/smart-feeds", tags=["smart-feeds"])
Session = Annotated[AsyncSession, Depends(get_session)]


class SmartFeedFields(BaseModel):
    title: bool = True
    excerpt: bool = True
    author: bool = True
    url: bool = True
    content: bool = False


class SmartFeedDefinitionIn(BaseModel):
    scope_kind: Literal["all", "category", "feed"] = "all"
    scope_id: int | None = None
    mode: Literal["keywords", "regex"] = "keywords"
    keywords_any: list[str] = Field(default_factory=list, max_length=50)
    keywords_all: list[str] = Field(default_factory=list, max_length=50)
    keywords_none: list[str] = Field(default_factory=list, max_length=50)
    regex: str | None = Field(default=None, max_length=300)
    fields: SmartFeedFields = Field(default_factory=SmartFeedFields)

    # Protocole SmartFeedDefinition : `fields` doit être un dict pour rules.py
    def as_definition(self) -> SmartFeedDefinitionIn:
        return self


class SmartFeedIn(SmartFeedDefinitionIn):
    name: str = Field(min_length=1, max_length=200)
    position: int | None = None
    auto_summary: bool = False
    summary_daily_cap: int = Field(default=10, ge=1, le=200)


class SmartFeedOut(BaseModel):
    id: int
    name: str
    position: int
    scope_kind: str
    scope_id: int | None
    mode: str
    keywords_any: list[str]
    keywords_all: list[str]
    keywords_none: list[str]
    regex: str | None
    fields: dict[str, bool]
    auto_summary: bool
    summary_daily_cap: int
    match_count: int = 0
    unread_count: int = 0
    auto_today: int = 0
    updated_at: datetime


class SmartFeedList(BaseModel):
    items: list[SmartFeedOut]
    tokens_today: int
    budget: int


class PreviewSample(BaseModel):
    id: int
    title: str
    feed_id: int
    published_at: datetime


class PreviewOut(BaseModel):
    count: int
    samples: list[PreviewSample]


class SummarizeAllIn(BaseModel):
    unread_only: bool = True


class _DefView:
    """Adapte le schéma Pydantic au protocole attendu par rules.py (fields en dict)."""

    def __init__(self, d: SmartFeedDefinitionIn) -> None:
        self.scope_kind = d.scope_kind
        self.scope_id = d.scope_id
        self.mode = d.mode
        self.keywords_any = d.keywords_any
        self.keywords_all = d.keywords_all
        self.keywords_none = d.keywords_none
        self.regex = d.regex
        self.fields = d.fields.model_dump()


def _apply_definition(sf: SmartFeed, normalized: dict) -> None:
    for key, value in normalized.items():
        setattr(sf, key, value)


async def _stats(session: AsyncSession, ids: list[int]) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    if not ids:
        return {}, {}, {}
    matches = dict(
        (await session.execute(select(SmartFeedMatch.smart_feed_id, func.count()).where(SmartFeedMatch.smart_feed_id.in_(ids)).group_by(SmartFeedMatch.smart_feed_id))).all()
    )
    unread = dict(
        (
            await session.execute(
                select(SmartFeedMatch.smart_feed_id, func.count())
                .join(Entry, Entry.id == SmartFeedMatch.entry_id)
                .where(SmartFeedMatch.smart_feed_id.in_(ids), Entry.read.is_(False))
                .group_by(SmartFeedMatch.smart_feed_id)
            )
        ).all()
    )
    auto = {sf_id: await service.auto_count_today(session, sf_id) for sf_id in ids}
    return matches, unread, auto


def _out(sf: SmartFeed, matches: dict, unread: dict, auto: dict) -> SmartFeedOut:
    data = SmartFeedOut.model_validate(sf, from_attributes=True)
    data.match_count = matches.get(sf.id, 0)
    data.unread_count = unread.get(sf.id, 0)
    data.auto_today = auto.get(sf.id, 0)
    return data


@router.get("", response_model=SmartFeedList)
async def list_smart_feeds(_: Auth, session: Session) -> SmartFeedList:
    items = (await session.execute(select(SmartFeed).order_by(SmartFeed.position, SmartFeed.name))).scalars().all()
    matches, unread, auto = await _stats(session, [s.id for s in items])
    return SmartFeedList(
        items=[_out(s, matches, unread, auto) for s in items],
        tokens_today=await service.auto_tokens_today(session),
        budget=await service.auto_budget(session),
    )


@router.post("/preview", response_model=PreviewOut)
async def preview(body: SmartFeedDefinitionIn, _: Auth, session: Session) -> PreviewOut:
    try:
        result = await service.preview(session, _DefView(body))
    except InvalidDefinition as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except service.RegexRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return PreviewOut(**result)


async def _validate(session: AsyncSession, body: SmartFeedDefinitionIn) -> dict:
    try:
        normalized = normalize_definition(_DefView(body))
        if normalized["mode"] == "regex":
            await service.validate_regex(session, normalized["regex"])
    except (InvalidDefinition, service.RegexRejected) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return normalized


@router.post("", response_model=SmartFeedOut, status_code=201)
async def create_smart_feed(body: SmartFeedIn, _: Auth, session: Session) -> SmartFeedOut:
    normalized = await _validate(session, body)
    if (await session.execute(select(SmartFeed).where(func.lower(SmartFeed.name) == body.name.lower()))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Un flux intelligent porte déjà ce nom")
    position = body.position
    if position is None:
        position = ((await session.execute(select(func.coalesce(func.max(SmartFeed.position), 0)))).scalar_one() or 0) + 1
    sf = SmartFeed(name=body.name.strip(), position=position, auto_summary=body.auto_summary, summary_daily_cap=body.summary_daily_cap)
    _apply_definition(sf, normalized)
    session.add(sf)
    await session.flush()
    await service.apply(session, sf)
    await session.commit()
    await session.refresh(sf)
    matches, unread, auto = await _stats(session, [sf.id])
    return _out(sf, matches, unread, auto)


@router.get("/{smart_feed_id}", response_model=SmartFeedOut)
async def get_smart_feed(smart_feed_id: int, _: Auth, session: Session) -> SmartFeedOut:
    sf = await session.get(SmartFeed, smart_feed_id)
    if sf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux intelligent introuvable")
    matches, unread, auto = await _stats(session, [sf.id])
    return _out(sf, matches, unread, auto)


@router.put("/{smart_feed_id}", response_model=SmartFeedOut)
async def update_smart_feed(smart_feed_id: int, body: SmartFeedIn, _: Auth, session: Session) -> SmartFeedOut:
    sf = await session.get(SmartFeed, smart_feed_id)
    if sf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux intelligent introuvable")
    normalized = await _validate(session, body)
    duplicate = (
        await session.execute(select(SmartFeed).where(func.lower(SmartFeed.name) == body.name.lower(), SmartFeed.id != sf.id))
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Un flux intelligent porte déjà ce nom")
    definition_changed = any(getattr(sf, k) != v for k, v in normalized.items())
    sf.name = body.name.strip()
    if body.position is not None:
        sf.position = body.position
    sf.auto_summary = body.auto_summary
    sf.summary_daily_cap = body.summary_daily_cap
    _apply_definition(sf, normalized)
    await session.flush()
    if definition_changed:
        await service.apply(session, sf)
    await session.commit()
    await session.refresh(sf)
    matches, unread, auto = await _stats(session, [sf.id])
    return _out(sf, matches, unread, auto)


@router.delete("/{smart_feed_id}", status_code=204)
async def delete_smart_feed(smart_feed_id: int, _: Auth, session: Session) -> None:
    sf = await session.get(SmartFeed, smart_feed_id)
    if sf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux intelligent introuvable")
    await service.remove(session, sf)
    await session.commit()


@router.post("/{smart_feed_id}/summarize-all")
async def summarize_all(smart_feed_id: int, body: SummarizeAllIn, _: Auth, session: Session) -> dict:
    sf = await session.get(SmartFeed, smart_feed_id)
    if sf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux intelligent introuvable")
    result = await service.summarize_all(session, sf, unread_only=body.unread_only)
    await session.commit()
    return result
