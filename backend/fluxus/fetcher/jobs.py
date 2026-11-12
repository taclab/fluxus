from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..jobs.registry import job_handler
from ..models import Feed, Job
from .service import _record_error, refresh_feed

log = logging.getLogger(__name__)


async def _record_internal_error(feed_id: int, message: str) -> None:
    async with session_scope() as fresh:
        feed = await fresh.get(Feed, feed_id)
        if feed is not None:
            await _record_error(fresh, feed, message, datetime.now(UTC))


async def fetch_feed_abandoned(session: AsyncSession, job: Job) -> None:
    """Le worker a été interrompu à chaque tentative : le flux recule, sinon il serait replanifié aussitôt."""
    feed = await session.get(Feed, int(job.payload["feed_id"]))
    if feed is not None:
        await _record_error(session, feed, "Récupération interrompue à chaque tentative (arrêt du worker)", datetime.now(UTC))


@job_handler("fetch_feed", on_abandoned=fetch_feed_abandoned)
async def fetch_feed_job(session: AsyncSession, job: Job) -> None:
    feed_id = int(job.payload["feed_id"])
    feed = await session.get(Feed, feed_id)
    if feed is None or feed.disabled:
        return
    try:
        result = await refresh_feed(session, feed)
    except asyncio.CancelledError:
        # Délai du job dépassé : même recul que pour une erreur, sinon le flux serait replanifié
        # toutes les 30 s et occuperait un créneau en permanence.
        await session.rollback()
        await _record_internal_error(feed_id, "Délai dépassé pendant la récupération du flux")
        raise
    except Exception as exc:
        # Erreur non prévue (contrainte SQL, parseur…) : la transaction courante est perdue, mais le
        # flux doit quand même reculer, sinon il serait re-planifié toutes les 30 s et frapperait le site.
        await session.rollback()
        await _record_internal_error(feed_id, f"Erreur interne: {type(exc).__name__}: {exc}")
        raise
    if result.new_entries:
        log.info("flux #%s '%s': %s nouvelles entrées", feed.id, feed.title, result.new_entries)
