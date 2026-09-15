from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Auth
from ..db import get_session
from ..fetcher.discover import discover_feeds
from ..fetcher.http import UnsafeURLError
from ..fetcher.parse import normalize_url
from ..fetcher.service import fetch_feed_preview
from ..jobs import enqueue
from ..models import Category, Entry, Feed, SmartFeed
from ..schemas import (
    CategoryIn,
    CategoryOut,
    DiscoverIn,
    DiscoverOut,
    FeedCreate,
    FeedOut,
    FeedUpdate,
    OpmlImportResult,
    ReorderIn,
)
from .opml import build_opml, parse_opml

router = APIRouter(prefix="/api", tags=["feeds"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def _unread_by_feed(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(
        select(Entry.feed_id, func.count()).where(Entry.read.is_(False)).group_by(Entry.feed_id)
    )
    return {feed_id: count for feed_id, count in result}


async def _first_position(session: AsyncSession, category_id: int | None, *, exclude_id: int | None = None) -> int:
    """Position qui place un flux en tête de son dossier (les valeurs négatives sont acceptées)."""
    cond = Feed.category_id.is_(None) if category_id is None else Feed.category_id == category_id
    query = select(func.min(Feed.position)).where(cond)
    if exclude_id is not None:
        query = query.where(Feed.id != exclude_id)
    lowest = (await session.execute(query)).scalar_one()
    return (lowest if lowest is not None else 1) - 1


def _feed_out(feed: Feed, unread: dict[int, int]) -> FeedOut:
    data = FeedOut.model_validate(feed, from_attributes=True)
    data.unread_count = unread.get(feed.id, 0)
    return data


# --- Catégories --------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(_: Auth, session: Session) -> list[CategoryOut]:
    cats = (await session.execute(select(Category).order_by(Category.position, Category.title))).scalars().all()
    unread = await _unread_by_feed(session)
    feeds = (await session.execute(select(Feed.id, Feed.category_id))).all()
    per_cat: dict[int | None, int] = {}
    for feed_id, cat_id in feeds:
        per_cat[cat_id] = per_cat.get(cat_id, 0) + unread.get(feed_id, 0)
    return [
        CategoryOut(id=c.id, title=c.title, position=c.position, unread_count=per_cat.get(c.id, 0))
        for c in cats
    ]


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, _: Auth, session: Session) -> CategoryOut:
    existing = await session.execute(select(Category).where(func.lower(Category.title) == body.title.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette catégorie existe déjà")
    if body.position is None:
        max_pos = (await session.execute(select(func.coalesce(func.max(Category.position), 0)))).scalar_one()
        body.position = max_pos + 1
    cat = Category(title=body.title, position=body.position)
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return CategoryOut(id=cat.id, title=cat.title, position=cat.position)


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def update_category(category_id: int, body: CategoryIn, _: Auth, session: Session) -> CategoryOut:
    cat = await session.get(Category, category_id)
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
    cat.title = body.title
    if body.position is not None:
        cat.position = body.position
    await session.commit()
    return CategoryOut(id=cat.id, title=cat.title, position=cat.position)


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: int, _: Auth, session: Session) -> None:
    cat = await session.get(Category, category_id)
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
    await session.delete(cat)  # les flux passent en category_id NULL (ON DELETE SET NULL)
    await session.commit()


# --- Flux --------------------------------------------------------------------


@router.get("/feeds", response_model=list[FeedOut])
async def list_feeds(_: Auth, session: Session) -> list[FeedOut]:
    feeds = (await session.execute(select(Feed).order_by(Feed.category_id, Feed.position, Feed.title))).scalars().all()
    unread = await _unread_by_feed(session)
    return [_feed_out(f, unread) for f in feeds]


@router.post("/feeds/discover", response_model=list[DiscoverOut])
async def discover(body: DiscoverIn, _: Auth) -> list[DiscoverOut]:
    try:
        found = await discover_feeds(body.url.strip())
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Site injoignable: {exc}") from exc
    return [DiscoverOut(url=f.url, title=f.title) for f in found]


@router.post("/feeds", response_model=FeedOut, status_code=201)
async def create_feed(body: FeedCreate, _: Auth, session: Session) -> FeedOut:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed, final_url = await fetch_feed_preview(url)
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flux injoignable: {exc}") from exc
    if parsed.bozo_error and not parsed.entries and not parsed.title:
        # Ce n'est peut-être pas un flux mais un site : on tente la découverte.
        try:
            found = await discover_feeds(url)
        except Exception:  # noqa: BLE001
            found = []
        if len(found) == 1:
            try:
                parsed, final_url = await fetch_feed_preview(found[0].url)
            except UnsafeURLError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flux injoignable: {exc}") from exc
        elif found:
            raise HTTPException(
                status.HTTP_300_MULTIPLE_CHOICES,
                {"message": "Plusieurs flux trouvés", "choices": [{"url": f.url, "title": f.title} for f in found]},
            )
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Flux illisible: {parsed.bozo_error}")

    canonical = normalize_url(final_url) or final_url
    duplicate = await session.execute(select(Feed).where(Feed.url.in_([canonical, final_url, url])))
    if duplicate.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce flux est déjà abonné")

    if body.category_id is not None and await session.get(Category, body.category_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")

    feed = Feed(
        url=final_url,
        site_url=parsed.site_url,
        title=(body.title or parsed.title or final_url)[:500],
        description=(parsed.description or None),
        icon_url=parsed.icon_url,
        category_id=body.category_id,
        position=await _first_position(session, body.category_id),
        extraction_mode=body.extraction_mode,
        is_youtube=parsed.is_youtube,
        next_fetch_at=datetime.now(UTC),
    )
    session.add(feed)
    await session.flush()
    await enqueue(session, "fetch_feed", {"feed_id": feed.id}, dedupe_key=f"fetch_feed:{feed.id}")
    await session.commit()
    await session.refresh(feed)
    return _feed_out(feed, {})


@router.post("/subscriptions/reorder", status_code=204)
async def reorder_subscriptions(body: ReorderIn, _: Auth, session: Session) -> None:
    """Applique en une transaction le nouvel ordre de la barre latérale (dossiers, flux, flux intelligents)."""
    if body.categories:
        rows = (await session.execute(select(Category).where(Category.id.in_([c.id for c in body.categories])))).scalars().all()
        by_id = {c.id: c for c in rows}
        for item in body.categories:
            cat = by_id.get(item.id)
            if cat is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
            cat.position = item.position
    moved: list[Feed] = []
    if body.feeds:
        target_ids = {f.category_id for f in body.feeds if f.category_id is not None}
        if target_ids:
            known = set((await session.execute(select(Category.id).where(Category.id.in_(target_ids)))).scalars().all())
            if known != target_ids:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
        rows = (await session.execute(select(Feed).where(Feed.id.in_([f.id for f in body.feeds])))).scalars().all()
        by_feed = {f.id: f for f in rows}
        for item in body.feeds:
            feed = by_feed.get(item.id)
            if feed is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux introuvable")
            feed.position = item.position
            if feed.category_id != item.category_id:
                feed.category_id = item.category_id
                moved.append(feed)
    if body.smart_feeds:
        rows = (await session.execute(select(SmartFeed).where(SmartFeed.id.in_([s.id for s in body.smart_feeds])))).scalars().all()
        by_sf = {s.id: s for s in rows}
        for item in body.smart_feeds:
            sf = by_sf.get(item.id)
            if sf is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux intelligent introuvable")
            sf.position = item.position
    if moved:
        from ..smartfeeds.service import reevaluate_feed

        await session.flush()
        for feed in moved:
            await reevaluate_feed(session, feed)
    await session.commit()


@router.get("/feeds/{feed_id}", response_model=FeedOut)
async def get_feed(feed_id: int, _: Auth, session: Session) -> FeedOut:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux introuvable")
    return _feed_out(feed, await _unread_by_feed(session))


@router.put("/feeds/{feed_id}", response_model=FeedOut)
async def update_feed(feed_id: int, body: FeedUpdate, _: Auth, session: Session) -> FeedOut:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux introuvable")
    if body.title is not None:
        feed.title = body.title
    if body.url is not None and body.url != feed.url:
        other = await session.execute(select(Feed.id).where(Feed.url == body.url, Feed.id != feed.id))
        if other.scalar_one_or_none() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Un autre flux utilise déjà cette adresse")
        feed.url = body.url
        feed.etag = None
        feed.last_modified = None
        feed.next_fetch_at = datetime.now(UTC)
    previous_category = feed.category_id
    if body.clear_category:
        feed.category_id = None
    elif body.category_id is not None:
        if await session.get(Category, body.category_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
        feed.category_id = body.category_id
    if feed.category_id != previous_category:
        from ..smartfeeds.service import reevaluate_feed

        feed.position = await _first_position(session, feed.category_id, exclude_id=feed.id)
        await session.flush()
        await reevaluate_feed(session, feed)
    if body.extraction_mode is not None:
        feed.extraction_mode = body.extraction_mode
    if body.disabled is not None:
        feed.disabled = body.disabled
    if body.fetch_interval_min is not None:
        feed.fetch_interval_min = body.fetch_interval_min
    await session.commit()
    await session.refresh(feed)
    return _feed_out(feed, await _unread_by_feed(session))


@router.delete("/feeds/{feed_id}", status_code=204)
async def delete_feed(feed_id: int, _: Auth, session: Session) -> None:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux introuvable")
    await session.delete(feed)
    await session.commit()


@router.post("/feeds/{feed_id}/refresh", status_code=202)
async def refresh_feed_now(feed_id: int, _: Auth, session: Session) -> dict:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flux introuvable")
    feed.next_fetch_at = datetime.now(UTC)
    await enqueue(session, "fetch_feed", {"feed_id": feed.id}, dedupe_key=f"fetch_feed:{feed.id}")
    await session.commit()
    return {"queued": True}


@router.post("/feeds/refresh-all", status_code=202)
async def refresh_all(_: Auth, session: Session) -> dict:
    feed_ids = (await session.execute(select(Feed.id).where(Feed.disabled.is_(False)))).scalars().all()
    queued = 0
    for fid in feed_ids:
        if await enqueue(session, "fetch_feed", {"feed_id": fid}, dedupe_key=f"fetch_feed:{fid}"):
            queued += 1
    await session.commit()
    return {"queued": queued}


# --- OPML --------------------------------------------------------------------


MAX_OPML_BYTES = 5 * 1024 * 1024


@router.post("/opml/import", response_model=OpmlImportResult)
async def opml_import(file: UploadFile, _: Auth, session: Session) -> OpmlImportResult:
    data = await file.read(MAX_OPML_BYTES + 1)
    if len(data) > MAX_OPML_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Fichier OPML trop volumineux (5 Mo maximum)")
    try:
        items = parse_opml(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"OPML invalide: {exc}") from exc

    existing_urls = set((await session.execute(select(Feed.url))).scalars().all())
    cats = {c.title.lower(): c for c in (await session.execute(select(Category))).scalars().all()}
    imported = skipped = created_cats = 0
    # Le lot importé passe en tête de chaque dossier, dans l'ordre du fichier : on réserve autant de
    # positions au-dessus du minimum actuel qu'il y a de flux à importer dans ce dossier.
    to_import = [i for i in items if i.url.startswith(("http://", "https://")) and len(i.url) <= 2048 and i.url not in existing_urls]
    per_category: dict[str | None, int] = {}
    for i in to_import:
        key = i.category[:200].lower() if i.category else None
        per_category[key] = per_category.get(key, 0) + 1
    next_position: dict[int | None, int] = {}
    for item in items:
        if not item.url.startswith(("http://", "https://")) or len(item.url) > 2048 or item.url in existing_urls:
            skipped += 1
            continue
        cat = None
        if item.category:
            item.category = item.category[:200]
            cat = cats.get(item.category.lower())
            if cat is None:
                cat = Category(title=item.category, position=len(cats) + 1)
                session.add(cat)
                await session.flush()
                cats[item.category.lower()] = cat
                created_cats += 1
        cat_id = cat.id if cat else None
        if cat_id not in next_position:
            count = per_category.get(item.category.lower() if item.category else None, 1)
            next_position[cat_id] = await _first_position(session, cat_id) - (count - 1)
        feed = Feed(
            url=item.url,
            site_url=(item.site_url or None) and item.site_url[:2048],
            title=(item.title or item.url)[:500],
            category_id=cat_id,
            position=next_position[cat_id],
            is_youtube="youtube.com/feeds" in item.url,
            next_fetch_at=datetime.now(UTC),
        )
        next_position[cat_id] += 1
        session.add(feed)
        await session.flush()
        await enqueue(session, "fetch_feed", {"feed_id": feed.id}, dedupe_key=f"fetch_feed:{feed.id}")
        existing_urls.add(item.url)
        imported += 1
    await session.commit()
    return OpmlImportResult(imported=imported, skipped=skipped, categories_created=created_cats)


@router.get("/opml/export")
async def opml_export(_: Auth, session: Session) -> Response:
    cats = (await session.execute(select(Category))).scalars().all()
    feeds = (await session.execute(select(Feed))).scalars().all()
    xml = build_opml(list(cats), list(feeds))
    return Response(
        content=xml,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="fluxus.opml"'},
    )
