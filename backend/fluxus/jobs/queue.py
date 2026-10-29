"""File de jobs adossée à PostgreSQL.

Un job est une ligne de la table `jobs`. Le worker en réclame par lot avec
`SELECT ... FOR UPDATE SKIP LOCKED`, ce qui autorise plusieurs workers sans Redis.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..maintenance import guard
from ..models import Job

log = logging.getLogger(__name__)

# Un job "running" plus vieux que ce délai est considéré perdu (worker tué) et relancé.
STALE_AFTER = timedelta(minutes=15)


async def enqueue(
    session: AsyncSession,
    job_type: str,
    payload: dict | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> Job | None:
    """Ajoute un job. Avec `dedupe_key`, ne crée rien si un job identique est déjà en attente.

    Hors d'un job du worker, refusé (`maintenance.Busy`) pendant un import de sauvegarde : le job
    viserait un identifiant qui désigne désormais un autre contenu."""
    await guard(session)
    job = Job(
        type=job_type,
        payload=payload or {},
        run_at=run_at or datetime.now(UTC),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    # SAVEPOINT : une collision de dedupe_key n'annule que cette insertion, pas les jobs déjà ajoutés
    # dans la même transaction (sinon un seul flux en attente faisait perdre tout le lot).
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        log.debug("job dédupliqué: %s", dedupe_key)
        return None
    return job


async def claim(session: AsyncSession, limit: int) -> list[Job]:
    now = datetime.now(UTC)
    stmt = (
        select(Job)
        .where(
            or_(
                (Job.status == "queued") & (Job.run_at <= now),
                (Job.status == "running") & (Job.locked_at < now - STALE_AFTER),
            )
        )
        .order_by(Job.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.execute(stmt)).scalars())
    for job in jobs:
        job.status = "running"
        job.locked_at = now
        job.attempts += 1
    await session.commit()
    return jobs


async def mark_done(session: AsyncSession, job_id: int) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status="done", finished_at=datetime.now(UTC), dedupe_key=None, last_error=None)
    )


async def mark_failed(session: AsyncSession, job: Job, error: str) -> None:
    now = datetime.now(UTC)
    if job.attempts >= job.max_attempts:
        values = dict(status="failed", finished_at=now, dedupe_key=None, last_error=error[:4000])
    else:
        # Nouvelle tentative avec attente exponentielle : 1, 4, 9 minutes...
        delay = timedelta(minutes=job.attempts**2)
        values = dict(status="queued", run_at=now + delay, last_error=error[:4000])
    await session.execute(update(Job).where(Job.id == job.id).values(**values))


async def purge_finished(session: AsyncSession, older_than: timedelta = timedelta(days=3)) -> int:
    from sqlalchemy import delete

    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        delete(Job).where(Job.status.in_(["done", "failed"]), Job.finished_at < cutoff)
    )
    return result.rowcount or 0
