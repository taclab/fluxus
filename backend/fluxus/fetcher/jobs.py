from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..jobs.registry import job_handler
from ..models import Feed, Job
from .service import _record_error, refresh_feed

log = logging.getLogger(__name__)


@job_handler("fetch_feed")
async def fetch_feed_job(session: AsyncSession, job: Job) -> None:
    feed_id = int(job.payload["feed_id"])
    feed = await session.get(Feed, feed_id)
    if feed is None or feed.disabled:
        return
    try:
        result = await refresh_feed(session, feed)
    except Exception as exc:
        # Erreur non prévue (contrainte SQL, parseur…) : la transaction courante est perdue, mais le
        # flux doit quand même reculer, sinon il serait re-planifié toutes les 30 s et frapperait le site.
        await session.rollback()
        async with session_scope() as fresh:
            fresh_feed = await fresh.get(Feed, feed_id)
            if fresh_feed is not None:
                await _record_error(fresh, fresh_feed, f"Erreur interne: {type(exc).__name__}: {exc}", datetime.now(UTC))
        raise
    if result.new_entries:
        log.info("flux #%s '%s': %s nouvelles entrées", feed.id, feed.title, result.new_entries)
