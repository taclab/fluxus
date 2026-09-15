"""Évaluation des flux intelligents dans PostgreSQL, quotas des résumés automatiques."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs.queue import enqueue
from ..models import Entry, Feed, Job, SmartFeed, SmartFeedMatch, Summary
from ..settings_store import get_setting
from .rules import InvalidDefinition, SmartFeedDefinition, build_condition, scope_includes_feed, uses_content

log = logging.getLogger(__name__)

EVALUATE_TIMEOUT = "5s"
APPLY_TIMEOUT = "15s"
PREVIEW_LIMIT = 8
# Étalement des résumés automatiques : un job toutes les 20 s pour ne pas monopoliser le worker.
AUTO_SUMMARY_SPACING = timedelta(seconds=20)


class RegexRejected(ValueError):
    """Expression refusée par PostgreSQL (syntaxe ARE) ; le message vient du serveur."""


async def _set_timeout(session: AsyncSession, value: str) -> None:
    await session.execute(text(f"SET LOCAL statement_timeout = '{value}'"))


async def _reset_timeout(session: AsyncSession) -> None:
    # SET LOCAL survit à un RELEASE SAVEPOINT : on remet la valeur par défaut explicitement.
    await session.execute(text("SET LOCAL statement_timeout = DEFAULT"))


async def validate_regex(session: AsyncSession, pattern: str) -> None:
    """Fait valider la syntaxe par PostgreSQL : `re.compile` accepte des motifs que le moteur ARE refuse."""
    try:
        async with session.begin_nested():
            await session.execute(select(func.textregexeq("", pattern)))
    except DBAPIError as exc:
        message = str(getattr(exc.orig, "message", None) or exc.orig or exc)
        raise RegexRejected(f"Expression régulière refusée : {message.splitlines()[0]}") from exc


async def _retention_cutoff(session: AsyncSession) -> datetime:
    try:
        days = max(1, min(365, int(await get_setting(session, "sync_prefetch_days") or 30)))
    except ValueError:
        days = 30
    return datetime.now(UTC) - timedelta(days=days)


async def _bump(session: AsyncSession, entry_ids: set[int]) -> None:
    """Horloge Python (pas now() SQL, figé au début de transaction) pour que la sync delta voie le changement."""
    if entry_ids:
        await session.execute(update(Entry).where(Entry.id.in_(entry_ids)).values(updated_at=datetime.now(UTC)))


async def _insert_matches(session: AsyncSession, sf: SmartFeed, where, timeout: str) -> set[int]:
    """INSERT … SELECT des correspondances, sous savepoint et timeout ; renvoie les ids nouvellement liés."""
    try:
        async with session.begin_nested():
            await _set_timeout(session, timeout)
            stmt = (
                pg_insert(SmartFeedMatch)
                .from_select(
                    ["smart_feed_id", "entry_id", "matched_at"],
                    select(func.cast(sf.id, SmartFeedMatch.smart_feed_id.type), Entry.id, func.now()).where(where),
                )
                .on_conflict_do_nothing()
                .returning(SmartFeedMatch.entry_id)
            )
            ids = set((await session.execute(stmt)).scalars())
            await _reset_timeout(session)
            return ids
    except DBAPIError as exc:
        # Timeout ou regex invalide : on abandonne ce flux intelligent, jamais le lot d'articles.
        log.warning("flux intelligent #%s « %s » : évaluation ignorée (%s)", sf.id, sf.name, str(exc.orig).splitlines()[0])
        return set()


async def evaluate(session: AsyncSession, feed: Feed, entry_ids: list[int], *, content_only: bool = False) -> list[tuple[SmartFeed, set[int]]]:
    """Évalue les flux intelligents sur des entrées fraîchement insérées (ou fraîchement extraites)."""
    if not entry_ids:
        return []
    smart_feeds = (await session.execute(select(SmartFeed).order_by(SmartFeed.position))).scalars().all()
    results: list[tuple[SmartFeed, set[int]]] = []
    for sf in smart_feeds:
        if content_only and not uses_content(sf):
            continue
        if not scope_includes_feed(sf.scope_kind, sf.scope_id, feed):
            continue
        try:
            condition = build_condition(sf, include_scope=False)
        except InvalidDefinition:
            continue
        new_ids = await _insert_matches(session, sf, and_(Entry.id.in_(entry_ids), condition), EVALUATE_TIMEOUT)
        if new_ids:
            results.append((sf, new_ids))
    return results


async def apply(session: AsyncSession, sf: SmartFeed) -> dict:
    """Recalcule toutes les correspondances d'un flux intelligent sur la fenêtre de rétention (+ favoris)."""
    before = set((await session.execute(delete(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf.id).returning(SmartFeedMatch.entry_id))).scalars())
    cutoff = await _retention_cutoff(session)
    window = or_(Entry.published_at > cutoff, Entry.starred.is_(True))
    after = await _insert_matches(session, sf, and_(window, build_condition(sf)), APPLY_TIMEOUT)
    await _bump(session, before ^ after)
    return {"matches": len(after), "added": len(after - before), "removed": len(before - after)}


async def remove(session: AsyncSession, sf: SmartFeed) -> None:
    """Supprime un flux intelligent en prévenant les clients : collecte les entrées avant la cascade."""
    ids = set((await session.execute(select(SmartFeedMatch.entry_id).where(SmartFeedMatch.smart_feed_id == sf.id))).scalars())
    await session.delete(sf)
    await session.flush()
    await _bump(session, ids)


async def reevaluate_feed(session: AsyncSession, feed: Feed) -> None:
    """Après un changement de catégorie d'un flux : recalcule les flux intelligents à portée catégorie sur ses entrées."""
    entry_ids = select(Entry.id).where(Entry.feed_id == feed.id)
    smart_feeds = (await session.execute(select(SmartFeed).where(SmartFeed.scope_kind == "category"))).scalars().all()
    changed: set[int] = set()
    for sf in smart_feeds:
        before = set(
            (
                await session.execute(
                    delete(SmartFeedMatch)
                    .where(SmartFeedMatch.smart_feed_id == sf.id, SmartFeedMatch.entry_id.in_(entry_ids))
                    .returning(SmartFeedMatch.entry_id)
                )
            ).scalars()
        )
        after: set[int] = set()
        if scope_includes_feed(sf.scope_kind, sf.scope_id, feed):
            try:
                condition = build_condition(sf, include_scope=False)
            except InvalidDefinition:
                condition = None
            if condition is not None:
                after = await _insert_matches(session, sf, and_(Entry.feed_id == feed.id, condition), APPLY_TIMEOUT)
        changed |= before ^ after
    await _bump(session, changed)


async def preview(session: AsyncSession, definition: SmartFeedDefinition) -> dict:
    """Compte et exemples récents pour l'éditeur. Lève InvalidDefinition ou RegexRejected."""
    condition = build_condition(definition)  # valide aussi la définition
    if definition.mode == "regex" and definition.regex:
        await validate_regex(session, definition.regex)
    cutoff = await _retention_cutoff(session)
    window = and_(or_(Entry.published_at > cutoff, Entry.starred.is_(True)), condition)
    try:
        async with session.begin_nested():
            await _set_timeout(session, EVALUATE_TIMEOUT)
            count = (await session.execute(select(func.count()).select_from(Entry).where(window))).scalar_one()
            rows = (
                await session.execute(
                    select(Entry.id, Entry.title, Entry.feed_id, Entry.published_at).where(window).order_by(Entry.published_at.desc()).limit(PREVIEW_LIMIT)
                )
            ).all()
            await _reset_timeout(session)
    except DBAPIError as exc:
        raise RegexRejected(f"Évaluation interrompue : {str(exc.orig).splitlines()[0]}") from exc
    return {
        "count": count,
        "samples": [{"id": r.id, "title": r.title, "feed_id": r.feed_id, "published_at": r.published_at} for r in rows],
    }


async def smart_feed_ids_for(session: AsyncSession, entry_ids: list[int]) -> dict[int, list[int]]:
    """Une requête pour une page d'entrées, regroupée en Python (évite le N+1)."""
    if not entry_ids:
        return {}
    rows = (
        await session.execute(
            select(SmartFeedMatch.entry_id, SmartFeedMatch.smart_feed_id)
            .where(SmartFeedMatch.entry_id.in_(entry_ids))
            .order_by(SmartFeedMatch.smart_feed_id)
        )
    ).all()
    out: dict[int, list[int]] = {}
    for entry_id, sf_id in rows:
        out.setdefault(entry_id, []).append(sf_id)
    return out


# --- Résumés automatiques ------------------------------------------------------------------------


def _today_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def auto_tokens_today(session: AsyncSession) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Summary.input_tokens + Summary.output_tokens), 0)).where(
                Summary.origin == "auto", Summary.created_at >= _today_start()
            )
        )
    ).scalar_one()
    return int(total)


async def auto_budget(session: AsyncSession) -> int:
    try:
        return max(0, int(await get_setting(session, "llm_auto_daily_token_budget") or 0))
    except ValueError:
        return 0


async def auto_count_today(session: AsyncSession, smart_feed_id: int) -> int:
    """Résumés déjà produits aujourd'hui + jobs en attente ou en cours (sinon la concurrence dépasse le quota)."""
    done = (
        await session.execute(
            select(func.count()).where(Summary.origin == "auto", Summary.smart_feed_id == smart_feed_id, Summary.created_at >= _today_start())
        )
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count()).where(
                Job.type == "summarize_entry",
                Job.status.in_(["queued", "running"]),
                Job.payload["smart_feed_id"].as_integer() == smart_feed_id,
            )
        )
    ).scalar_one()
    return int(done) + int(pending)


async def _already_summarized(session: AsyncSession, entry_ids: set[int]) -> set[int]:
    if not entry_ids:
        return set()
    return set((await session.execute(select(Summary.entry_id).where(Summary.entry_id.in_(entry_ids)))).scalars())


async def schedule_auto_summaries(session: AsyncSession, new_pairs: list[tuple[SmartFeed, set[int]]]) -> int:
    """Met en file les résumés automatiques des nouvelles correspondances, dans les limites du quota et du budget."""
    budget = await auto_budget(session)
    if budget <= 0:
        return 0
    if await auto_tokens_today(session) >= budget:
        return 0
    queued = 0
    for sf, ids in new_pairs:
        if not sf.auto_summary or not ids:
            continue
        remaining = sf.summary_daily_cap - await auto_count_today(session, sf.id)
        if remaining <= 0:
            continue
        candidates = ids - await _already_summarized(session, ids)
        # Les plus récents d'abord.
        ordered = (await session.execute(select(Entry.id).where(Entry.id.in_(candidates)).order_by(Entry.published_at.desc()))).scalars().all()
        for i, entry_id in enumerate(ordered[:remaining]):
            job = await enqueue(
                session,
                "summarize_entry",
                {"entry_id": entry_id, "smart_feed_id": sf.id},
                dedupe_key=f"summarize:{entry_id}",
                run_at=datetime.now(UTC) + AUTO_SUMMARY_SPACING * i,
                max_attempts=2,
            )
            if job is not None:
                queued += 1
    return queued


async def summarize_all(session: AsyncSession, sf: SmartFeed, *, unread_only: bool) -> dict:
    """Met en file les articles du flux intelligent non encore résumés, dans la limite du budget restant."""
    budget = await auto_budget(session)
    used = await auto_tokens_today(session)
    q = select(Entry.id).join(SmartFeedMatch, SmartFeedMatch.entry_id == Entry.id).where(SmartFeedMatch.smart_feed_id == sf.id)
    if unread_only:
        q = q.where(Entry.read.is_(False))
    ids = set((await session.execute(q)).scalars())
    summarized = await _already_summarized(session, ids)
    candidates = ids - summarized
    if budget <= 0 or used >= budget:
        return {"queued": 0, "skipped_budget": len(candidates), "already_summarized": len(summarized), "budget_remaining": max(0, budget - used)}
    # Estimation prudente : ~3 000 tokens par résumé (article moyen + sortie).
    affordable = max(0, (budget - used) // 3000)
    ordered = (await session.execute(select(Entry.id).where(Entry.id.in_(candidates)).order_by(Entry.published_at.desc()))).scalars().all()
    queued = 0
    for i, entry_id in enumerate(ordered[:affordable]):
        job = await enqueue(
            session,
            "summarize_entry",
            {"entry_id": entry_id, "smart_feed_id": sf.id, "manual_batch": True},
            dedupe_key=f"summarize:{entry_id}",
            run_at=datetime.now(UTC) + AUTO_SUMMARY_SPACING * i,
            max_attempts=2,
        )
        if job is not None:
            queued += 1
    return {
        "queued": queued,
        "skipped_budget": max(0, len(ordered) - affordable),
        "already_summarized": len(summarized),
        "budget_remaining": max(0, budget - used),
    }
