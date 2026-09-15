"""Job `summarize_entry` : résumé automatique déclenché par un flux intelligent, sous quota et budget."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..extract.clean import html_to_text
from ..jobs.registry import job_handler
from ..models import Entry, Job, LlmUsage, SmartFeed
from ..settings_store import get_setting
from ..summarize.service import summarize_entry
from .service import auto_count_today, entry_matches, estimate_input_tokens, reserve_auto_budget

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


async def _still_wanted(session: AsyncSession, job: Job, entry_id: int, sf: SmartFeed | None) -> bool:
    """Un job automatique n'a plus d'objet si la règle a disparu, est désactivée, ou ne couvre plus l'article.

    Les lots lancés à la main (« Résumer les non résumés ») exigent seulement que l'article corresponde encore.
    """
    if sf is None:
        log.info("résumé auto #%s abandonné : flux intelligent supprimé", entry_id)
        return False
    if not job.payload.get("manual_batch") and not sf.auto_summary:
        log.info("résumé auto #%s abandonné : résumé automatique désactivé sur « %s »", entry_id, sf.name)
        return False
    if not await entry_matches(session, sf.id, entry_id):
        log.info("résumé auto #%s abandonné : l'article ne correspond plus à « %s »", entry_id, sf.name)
        return False
    return True


@job_handler("summarize_entry")
async def summarize_entry_job(session: AsyncSession, job: Job) -> None:
    entry_id = int(job.payload["entry_id"])
    smart_feed_id = job.payload.get("smart_feed_id")
    sf: SmartFeed | None = None
    if smart_feed_id:
        # Job automatique : la règle qui l'a déclenché doit toujours le justifier.
        sf = await session.get(SmartFeed, int(smart_feed_id))
        if not await _still_wanted(session, job, entry_id, sf):
            return
        assert sf is not None
        if not job.payload.get("manual_batch"):
            # Le job courant est compté comme « running » : on tolère donc `cap` et non `cap - 1`.
            if await auto_count_today(session, sf.id) > sf.summary_daily_cap:
                log.info("résumé auto #%s ignoré : quota du flux intelligent « %s » atteint", entry_id, sf.name)
                return

    entry = await session.get(Entry, entry_id)
    if entry is None:
        log.info("résumé auto #%s abandonné : article introuvable", entry_id)
        return

    # Réservation du budget avant tout appel externe (plafond strict, sérialisé par verrou consultatif).
    source = entry.content_text or html_to_text(entry.summary_html) or ""
    reservation: LlmUsage | None = await reserve_auto_budget(
        session, entry_id=entry_id, smart_feed_id=sf.id if sf else None, input_tokens=estimate_input_tokens(source)
    )
    if reservation is None:
        log.info("résumé auto #%s ignoré : budget quotidien insuffisant", entry_id)
        return
    max_output = reservation.estimated_tokens - estimate_input_tokens(source)
    await session.commit()

    model = (await get_setting(session, "llm_auto_model") or "").strip() or None
    async for event in summarize_entry(
        session,
        entry_id,
        origin="auto",
        smart_feed_id=sf.id if sf else None,
        model_override=model,
        usage=reservation,
        max_output_tokens=max_output,
    ):
        if event.type == "done":
            return
        if event.type == "error":
            message = str(event.data.get("message", ""))
            if any(marker in message for marker in PERMANENT_MARKERS):
                log.info("résumé auto #%s abandonné : %s", entry_id, message)
                return
            raise ProviderError(message)
