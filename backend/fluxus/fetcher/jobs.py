from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs.registry import job_handler
from ..models import Feed, Job
from .service import refresh_feed

log = logging.getLogger(__name__)


@job_handler("fetch_feed")
async def fetch_feed_job(session: AsyncSession, job: Job) -> None:
    feed = await session.get(Feed, int(job.payload["feed_id"]))
    if feed is None or feed.disabled:
        return
    result = await refresh_feed(session, feed)
    if result.new_entries:
        log.info("flux #%s '%s': %s nouvelles entrées", feed.id, feed.title, result.new_entries)
