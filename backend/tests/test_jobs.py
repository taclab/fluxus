"""File de jobs et worker (constats 5 et 6 de l'audit du 12/11/2026). Nécessite la base PostgreSQL de test."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest.fixture(scope="module", autouse=True)
async def database():
    from fluxus.db import Base, get_engine
    from fluxus.models import EXTENSION_STATEMENTS, SEARCH_VECTOR_STATEMENTS

    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in EXTENSION_STATEMENTS:
            await conn.execute(text(stmt))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in SEARCH_VECTOR_STATEMENTS:
            await conn.execute(text(stmt))
    yield
    await engine.dispose()


async def _job(job_type: str, **values) -> int:
    from fluxus.db import session_scope
    from fluxus.models import Job

    async with session_scope() as session:
        job = Job(type=job_type, payload=values.pop("payload", {}), run_at=datetime.now(UTC), **values)
        session.add(job)
        await session.flush()
        return job.id


async def _reload(job_id: int):
    from fluxus.db import session_scope
    from fluxus.models import Job

    async with session_scope() as session:
        return await session.get(Job, job_id)


async def _claim_one(job_id: int):
    from fluxus.worker import claim_jobs

    jobs = [j for j in await claim_jobs(50) if j.id == job_id]
    return jobs[0] if jobs else None


def _long_ago() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=20)


async def test_stale_job_is_reclaimed_while_attempts_remain():
    job_id = await _job("test_noop", status="running", locked_at=_long_ago(), attempts=1, max_attempts=3)
    job = await _claim_one(job_id)
    assert job is not None and job.attempts == 2


async def test_stale_job_out_of_attempts_is_failed_not_rerun():
    """Constat 5 : un job qui interrompt le worker à chaque tentative n'est plus repris sans fin."""
    job_id = await _job("test_noop", status="running", locked_at=_long_ago(), attempts=2, max_attempts=2, dedupe_key="k-stale")
    assert await _claim_one(job_id) is None
    job = await _reload(job_id)
    assert (job.status, job.attempts, job.dedupe_key) == ("failed", 2, None)
    assert "interrompu" in job.last_error


async def test_abandoned_fetch_backs_the_feed_off():
    """Sans recul du flux, le planificateur remettrait aussitôt en file le job qui tue le worker."""
    from fluxus import handlers  # noqa: F401  enregistre les handlers
    from fluxus.db import session_scope
    from fluxus.models import Feed

    async with session_scope() as session:
        feed = Feed(url="https://jobs.example/rss", title="Jobs", next_fetch_at=datetime.now(UTC))
        session.add(feed)
        await session.flush()
        feed_id = feed.id
    job_id = await _job("fetch_feed", payload={"feed_id": feed_id}, status="running", locked_at=_long_ago(), attempts=3, max_attempts=3)
    assert await _claim_one(job_id) is None
    async with session_scope() as session:
        feed = await session.get(Feed, feed_id)
        assert feed.error_count == 1 and feed.next_fetch_at > datetime.now(UTC) + timedelta(minutes=5)


async def test_job_over_its_timeout_is_interrupted_and_rolled_back():
    """Constat 6 : un appel figé rend son créneau et sa transaction."""
    from fluxus import worker
    from fluxus.jobs.registry import job_handler
    from fluxus.models import Feed

    @job_handler("test_hang", timeout=timedelta(seconds=0.3))
    async def hang(session, job):
        session.add(Feed(url="https://jobs.example/jamais-validé", title="Fantôme"))
        await session.flush()
        await asyncio.sleep(30)

    job_id = await _job("test_hang", max_attempts=2)
    job = await _claim_one(job_id)
    started = datetime.now(UTC)
    await worker._run_locked(job)
    assert datetime.now(UTC) - started < timedelta(seconds=5)

    stored = await _reload(job_id)
    assert stored.status == "queued" and stored.run_at > datetime.now(UTC)  # nouvelle tentative, avec attente
    assert stored.last_error.startswith("Délai dépassé")
    from fluxus.db import session_scope

    async with session_scope() as session:
        ghosts = (await session.execute(text("SELECT count(*) FROM feeds WHERE title = 'Fantôme'"))).scalar_one()
        open_tx = (
            await session.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND xact_start IS NOT NULL")
            )
        ).scalar_one()
    assert (ghosts, open_tx) == (0, 0)

    # Seconde tentative : le plafond est atteint, le job passe en échec.
    await _set(job_id, run_at=datetime.now(UTC))
    job = await _claim_one(job_id)
    await worker._run_locked(job)
    assert (await _reload(job_id)).status == "failed"


async def _set(job_id: int, **values) -> None:
    from sqlalchemy import update

    from fluxus.db import session_scope
    from fluxus.models import Job

    async with session_scope() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(**values))


async def test_result_of_a_superseded_run_is_ignored():
    """Une exécution dont le job a été repris ne peut ni le terminer ni le remettre en échec."""
    from fluxus.db import session_scope
    from fluxus.jobs import queue

    job_id = await _job("test_noop", max_attempts=3)
    first = await _claim_one(job_id)
    # Le job est repris par une autre exécution (bail dépassé).
    await _set(job_id, locked_at=_long_ago())
    second = await _claim_one(job_id)
    assert second is not None and second.attempts == 2

    async with session_scope() as session:
        assert not await queue.mark_failed(session, first, "erreur de l'exécution périmée")
        assert not await queue.mark_done(session, first)
    assert (await _reload(job_id)).status == "running"
    async with session_scope() as session:
        assert await queue.mark_done(session, second)
    assert (await _reload(job_id)).status == "done"


async def test_job_removed_before_start_is_skipped():
    from fluxus import worker
    from fluxus.jobs.registry import job_handler

    ran = []

    @job_handler("test_record")
    async def record(session, job):
        ran.append(job.id)

    job_id = await _job("test_record")
    job = await _claim_one(job_id)
    await _set(job_id, locked_at=datetime.now(UTC) + timedelta(seconds=1))  # repris ailleurs
    await worker._run_locked(job)
    assert ran == []

    job_id = await _job("test_record")
    job = await _claim_one(job_id)
    await worker._run_locked(job)
    assert ran == [job_id] and (await _reload(job_id)).status == "done"


async def test_handler_timeout_must_stay_under_the_stale_delay():
    from fluxus.jobs.queue import STALE_AFTER
    from fluxus.jobs.registry import job_handler

    with pytest.raises(ValueError):
        job_handler("test_too_long", timeout=STALE_AFTER)
