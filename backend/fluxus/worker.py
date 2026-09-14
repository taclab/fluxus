"""Worker : exécute les jobs de la table `jobs` et planifie le rafraîchissement des flux."""

import asyncio
import logging
import signal
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

# L'import enregistre les handlers auprès du registre.
from . import handlers  # noqa: F401
from .config import get_settings
from .db import session_scope
from .jobs import queue
from .jobs.registry import get_handler
from .models import Feed, Job

log = logging.getLogger("fluxus.worker")

SCHEDULE_EVERY = timedelta(seconds=30)
PURGE_EVERY = timedelta(hours=6)


async def run_job(job: Job, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        handler = get_handler(job.type)
        async with session_scope() as session:
            if handler is None:
                await queue.mark_failed(session, job, f"Aucun handler pour le job '{job.type}'")
                return
            try:
                await handler(session, job)
                await session.commit()
                await queue.mark_done(session, job.id)
                log.info("job %s #%s ok", job.type, job.id)
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"
                log.warning("job %s #%s échoué (tentative %s): %s", job.type, job.id, job.attempts, exc)
                await queue.mark_failed(session, job, error)


async def schedule_due_feeds() -> int:
    """Met en file un job de rafraîchissement pour chaque flux arrivé à échéance."""
    now = datetime.now(UTC)
    count = 0
    async with session_scope() as session:
        result = await session.execute(
            select(Feed.id).where(Feed.disabled.is_(False), Feed.next_fetch_at <= now)
        )
        for (feed_id,) in result:
            job = await queue.enqueue(
                session, "fetch_feed", {"feed_id": feed_id}, dedupe_key=f"fetch_feed:{feed_id}"
            )
            if job is not None:
                count += 1
    return count


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log.info("worker démarré (concurrence=%s)", settings.worker_concurrency)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    semaphore = asyncio.Semaphore(settings.worker_concurrency)
    running: set[asyncio.Task] = set()
    last_schedule = datetime.min.replace(tzinfo=UTC)
    last_purge = datetime.now(UTC)

    while not stop.is_set():
        now = datetime.now(UTC)
        try:
            if now - last_schedule >= SCHEDULE_EVERY:
                scheduled = await schedule_due_feeds()
                if scheduled:
                    log.info("%s flux planifiés", scheduled)
                last_schedule = now
            if now - last_purge >= PURGE_EVERY:
                async with session_scope() as session:
                    purged = await queue.purge_finished(session)
                log.info("%s jobs terminés purgés", purged)
                last_purge = now

            free_slots = settings.worker_concurrency - len(running)
            if free_slots > 0:
                async with session_scope() as session:
                    jobs = await queue.claim(session, free_slots)
                for job in jobs:
                    task = asyncio.create_task(run_job(job, semaphore))
                    running.add(task)
                    task.add_done_callback(running.discard)
                if jobs:
                    continue  # d'autres jobs attendent peut-être, on repart sans dormir
        except Exception:  # noqa: BLE001
            log.exception("erreur dans la boucle du worker")
            await asyncio.sleep(5)
            continue

        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            pass

    log.info("arrêt demandé, attente des jobs en cours (%s)", len(running))
    if running:
        await asyncio.wait(running, timeout=60)


if __name__ == "__main__":
    asyncio.run(main())
