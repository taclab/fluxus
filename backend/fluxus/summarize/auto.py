"""Résumés automatiques : budget quotidien partagé (journal `llm_usage`), réservation, mise en file.

Utilisé par les flux intelligents (`smartfeeds`) et par les flux dont l'option « résumer tous les
nouveaux articles » est active. Aucun résumé n'est déclenché ailleurs sans action de l'utilisateur.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs.queue import enqueue
from ..models import Entry, Feed, Job, LlmUsage, Summary
from ..settings_store import get_setting

# Étalement des résumés automatiques : un job toutes les 20 s pour ne pas monopoliser le worker.
AUTO_SUMMARY_SPACING = timedelta(seconds=20)
# Verrou consultatif de la réservation de budget LLM (sérialise le contrôle « solde + réservation »).
BUDGET_LOCK_KEY = 727002
# Une réservation jamais soldée (processus tué) cesse de peser sur le budget après ce délai.
RESERVATION_TTL = timedelta(hours=1)
# Plancher de sortie sous lequel un résumé n'a plus de sens : on abandonne plutôt que de tronquer.
MIN_OUTPUT_TOKENS = 256
MAX_OUTPUT_TOKENS = 4000
# Estimation prudente d'un résumé pour les lots manuels (article moyen + sortie) ; le job réserve
# ensuite le montant exact sous verrou, ce qui rend le budget contraignant même en concurrence.
BATCH_TOKENS_PER_SUMMARY = 3000


def today_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def auto_budget(session: AsyncSession) -> int:
    try:
        return max(0, int(await get_setting(session, "llm_auto_daily_token_budget") or 0))
    except ValueError:
        return 0


async def auto_tokens_today(session: AsyncSession) -> int:
    """Consommation automatique du jour (UTC) d'après le journal `llm_usage` : tokens réels des appels
    soldés (réussis ou échoués) + estimation des réservations en cours non expirées. Indépendant des
    résumés conservés : un résumé supprimé ou un appel échoué a coûté quand même."""
    settled = (
        await session.execute(
            select(func.coalesce(func.sum(LlmUsage.tokens), 0)).where(
                LlmUsage.origin == "auto", LlmUsage.status.in_(["done", "failed"]), LlmUsage.created_at >= today_start()
            )
        )
    ).scalar_one()
    reserved = (
        await session.execute(
            select(func.coalesce(func.sum(LlmUsage.estimated_tokens), 0)).where(
                LlmUsage.origin == "auto",
                LlmUsage.status == "reserved",
                LlmUsage.created_at >= today_start(),
                LlmUsage.created_at >= datetime.now(UTC) - RESERVATION_TTL,
            )
        )
    ).scalar_one()
    return int(settled) + int(reserved)


def estimate_input_tokens(text: str) -> int:
    """Estimation prudente (≈ 3 caractères par token en français) + enveloppe des consignes."""
    return len(text) // 3 + 300


async def reserve_auto_budget(session: AsyncSession, *, entry_id: int, smart_feed_id: int | None, input_tokens: int) -> LlmUsage | None:
    """Réserve, sous verrou consultatif, la part de budget d'un résumé automatique.

    Renvoie la ligne `reserved` (avec `estimated_tokens` = entrée + sortie plafonnée) ou None si le solde
    ne permet pas au moins MIN_OUTPUT_TOKENS de sortie. La réservation doit être committée par l'appelant
    avant l'appel externe. Le plafond de sortie à appliquer est `estimated_tokens - input_tokens`.
    """
    budget = await auto_budget(session)
    if budget <= 0:
        return None
    await session.execute(select(func.pg_advisory_xact_lock(BUDGET_LOCK_KEY)))
    used = await auto_tokens_today(session)
    output_cap = min(MAX_OUTPUT_TOKENS, budget - used - input_tokens)
    if output_cap < MIN_OUTPUT_TOKENS:
        return None
    row = LlmUsage(entry_id=entry_id, smart_feed_id=smart_feed_id, origin="auto", status="reserved", estimated_tokens=input_tokens + output_cap)
    session.add(row)
    await session.flush()
    return row


async def already_summarized(session: AsyncSession, entry_ids: Iterable[int]) -> set[int]:
    ids = set(entry_ids)
    if not ids:
        return set()
    return set((await session.execute(select(Summary.entry_id).where(Summary.entry_id.in_(ids)))).scalars())


async def pending_jobs(session: AsyncSession, key: str, value: int) -> int:
    """Jobs `summarize_entry` en attente ou en cours dont `payload[key] == value`."""
    return int(
        (
            await session.execute(
                select(func.count()).where(
                    Job.type == "summarize_entry",
                    Job.status.in_(["queued", "running"]),
                    Job.payload[key].as_integer() == value,
                )
            )
        ).scalar_one()
    )


async def queue_auto(session: AsyncSession, entry_ids: Iterable[int], limit: int, payload: dict) -> int:
    """Met en file jusqu'à `limit` résumés, les plus récents d'abord, étalés dans le temps.

    Le `dedupe_key` est commun à toutes les origines : un article couvert à la fois par un flux
    intelligent et par un flux « tout résumer » n'est résumé qu'une fois.
    """
    ids = set(entry_ids)
    if limit <= 0 or not ids:
        return 0
    ordered = (await session.execute(select(Entry.id).where(Entry.id.in_(ids)).order_by(Entry.published_at.desc()))).scalars().all()
    queued = 0
    for i, entry_id in enumerate(ordered[:limit]):
        job = await enqueue(
            session,
            "summarize_entry",
            {"entry_id": entry_id, **payload},
            dedupe_key=f"summarize:{entry_id}",
            run_at=datetime.now(UTC) + AUTO_SUMMARY_SPACING * i,
            max_attempts=2,
        )
        if job is not None:
            queued += 1
    return queued


async def queue_batch(session: AsyncSession, entry_ids: Iterable[int], payload: dict) -> dict:
    """Lot lancé par l'utilisateur (« Résumer les non résumés ») : borné par le budget restant du jour."""
    ids = set(entry_ids)
    budget = await auto_budget(session)
    used = await auto_tokens_today(session)
    summarized = await already_summarized(session, ids)
    candidates = ids - summarized
    if budget <= 0 or used >= budget:
        return {"queued": 0, "skipped_budget": len(candidates), "already_summarized": len(summarized), "budget_remaining": max(0, budget - used)}
    affordable = max(0, (budget - used) // BATCH_TOKENS_PER_SUMMARY)
    queued = await queue_auto(session, candidates, affordable, {**payload, "manual_batch": True})
    return {
        "queued": queued,
        "skipped_budget": max(0, len(candidates) - affordable),
        "already_summarized": len(summarized),
        "budget_remaining": max(0, budget - used),
    }


# --- Flux « résumer tous les nouveaux articles » ---------------------------------------------------


async def feed_auto_count_today(session: AsyncSession, feed_id: int) -> int:
    """Résumés automatiques du jour produits pour le flux (pas ceux des flux intelligents, qui ont leur
    propre quota) + jobs en attente déclenchés par ce flux."""
    done = (
        await session.execute(
            select(func.count())
            .select_from(Summary)
            .join(Entry, Entry.id == Summary.entry_id)
            .where(Summary.origin == "auto", Summary.smart_feed_id.is_(None), Entry.feed_id == feed_id, Summary.created_at >= today_start())
        )
    ).scalar_one()
    return int(done) + await pending_jobs(session, "feed_id", feed_id)


async def feed_auto_counts_today(session: AsyncSession) -> dict[int, int]:
    """Résumés automatiques du jour par flux (écran Abonnements), en une requête."""
    rows = (
        await session.execute(
            select(Entry.feed_id, func.count())
            .select_from(Summary)
            .join(Entry, Entry.id == Summary.entry_id)
            .where(Summary.origin == "auto", Summary.smart_feed_id.is_(None), Summary.created_at >= today_start())
            .group_by(Entry.feed_id)
        )
    ).all()
    return {int(feed_id): int(n) for feed_id, n in rows}


async def schedule_feed_auto_summaries(session: AsyncSession, feed: Feed, entry_ids: Iterable[int]) -> int:
    """Met en file les résumés des nouveaux articles d'un flux dont l'option est active, sous quota et budget."""
    ids = set(entry_ids)
    if not feed.auto_summary or not ids:
        return 0
    budget = await auto_budget(session)
    if budget <= 0 or await auto_tokens_today(session) >= budget:
        return 0
    remaining = feed.summary_daily_cap - await feed_auto_count_today(session, feed.id)
    if remaining <= 0:
        return 0
    candidates = ids - await already_summarized(session, ids)
    return await queue_auto(session, candidates, remaining, {"feed_id": feed.id})


async def summarize_feed_batch(session: AsyncSession, feed: Feed, *, unread_only: bool) -> dict:
    q = select(Entry.id).where(Entry.feed_id == feed.id)
    if unread_only:
        q = q.where(Entry.read.is_(False))
    ids = set((await session.execute(q)).scalars())
    return await queue_batch(session, ids, {"feed_id": feed.id})
