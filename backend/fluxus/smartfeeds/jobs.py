"""Job `summarize_entry` : résumé automatique déclenché par un flux intelligent, sous quota et budget."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs.registry import job_handler
from ..models import Job, SmartFeed
from ..settings_store import get_setting
from ..summarize.service import summarize_entry
from .service import auto_budget, auto_count_today, auto_tokens_today

log = logging.getLogger(__name__)

# Messages d'erreur qui ne changeront pas en réessayant : le job se termine sans relance.
PERMANENT_MARKERS = (
    "introuvable",
    "trop court",
    "sous-titre",
    "transcription",
    "Aucune clé API",
    "refusé de résumer",
    "Fournisseur inconnu",
    "Modèle inconnu",
)


class ProviderError(RuntimeError):
    """Erreur transitoire du fournisseur : le job sera relancé."""


@job_handler("summarize_entry")
async def summarize_entry_job(session: AsyncSession, job: Job) -> None:
    entry_id = int(job.payload["entry_id"])
    smart_feed_id = job.payload.get("smart_feed_id")
    sf = await session.get(SmartFeed, int(smart_feed_id)) if smart_feed_id else None

    # Re-vérification au dernier moment : d'autres jobs ont pu consommer le budget entre-temps.
    budget = await auto_budget(session)
    if budget <= 0 or await auto_tokens_today(session) >= budget:
        log.info("résumé auto #%s ignoré : budget quotidien atteint", entry_id)
        return
    if sf is not None and not job.payload.get("manual_batch"):
        # Le job courant est compté comme « running » : on tolère donc `cap` et non `cap - 1`.
        if await auto_count_today(session, sf.id) > sf.summary_daily_cap:
            log.info("résumé auto #%s ignoré : quota du flux intelligent « %s » atteint", entry_id, sf.name)
            return

    model = (await get_setting(session, "llm_auto_model") or "").strip() or None
    async for event in summarize_entry(session, entry_id, origin="auto", smart_feed_id=sf.id if sf else None, model_override=model):
        if event.type == "done":
            return
        if event.type == "error":
            message = str(event.data.get("message", ""))
            if any(marker in message for marker in PERMANENT_MARKERS):
                log.info("résumé auto #%s abandonné : %s", entry_id, message)
                return
            raise ProviderError(message)
