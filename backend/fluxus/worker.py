"""Worker : exécute les jobs de la table `jobs` et planifie le rafraîchissement des flux."""

import asyncio
import logging
import signal
import traceback
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

# L'import enregistre les handlers auprès du registre.
from . import (
    handlers,  # noqa: F401
    maintenance,
)
from .config import get_settings, validate_runtime_config
from .db import session_factory, session_scope
from .jobs import queue
from .jobs.registry import get_abandon_hook, get_handler, get_timeout
from .models import Feed, Job

log = logging.getLogger("fluxus.worker")

SCHEDULE_EVERY = timedelta(seconds=30)
PURGE_EVERY = timedelta(hours=6)


async def run_job(job: Job, semaphore: asyncio.Semaphore) -> None:
    # Verrou de maintenance partagé tenu pendant tout le job. Un import de sauvegarde le prend en
    # exclusif : il attend la fin des jobs en cours, et un job qui attendait l'import constate ensuite
    # que sa ligne a disparu.
    async with semaphore, maintenance.shared(wait=True):
        await _run_locked(job)


async def _run_locked(job: Job) -> None:
    async with session_scope() as session:
        if not await queue.start(session, job):
            log.info("job %s #%s abandonné : repris ailleurs ou supprimé par un import de sauvegarde", job.type, job.id)
            return
    error = await _execute(job)
    async with session_scope() as session:
        if error is None:
            recorded = await queue.mark_done(session, job)
        else:
            recorded = await queue.mark_failed(session, job, error)
    if not recorded:
        log.warning("job %s #%s : résultat ignoré, le job a été repris par une autre exécution", job.type, job.id)
    elif error is None:
        log.info("job %s #%s ok", job.type, job.id)


async def _execute(job: Job) -> str | None:
    """Exécute le handler dans sa propre transaction, sous délai. Renvoie l'erreur, ou None."""
    handler = get_handler(job.type)
    if handler is None:
        return f"Aucun handler pour le job '{job.type}'"
    limit = get_timeout(job.type).total_seconds()
    async with session_factory()() as session:
        try:
            # Sans délai, un appel réseau figé garderait un créneau, sa transaction (donc l'horizon de
            # la sync) et le verrou de maintenance pour toujours.
            async with asyncio.timeout(limit) as deadline:
                await handler(session, job)
                await session.commit()
            return None
        except Exception as exc:  # noqa: BLE001
            with suppress(Exception):
                await session.rollback()
            if deadline.expired():
                log.warning("job %s #%s interrompu après %.0f s (tentative %s)", job.type, job.id, limit, job.attempts)
                return f"Délai dépassé : job interrompu après {limit:.0f} s"
            log.warning("job %s #%s échoué (tentative %s): %s", job.type, job.id, job.attempts, exc)
            return f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"


async def claim_jobs(limit: int) -> list[Job]:
    """Abandonne les jobs perdus qui ont épuisé leurs tentatives, puis réclame les suivants."""
    async with session_scope() as session:
        for job in await queue.abandon_exhausted(session):
            log.warning("job %s #%s abandonné après %s tentatives interrompues", job.type, job.id, job.attempts)
            hook = get_abandon_hook(job.type)
            if hook is not None:
                try:
                    async with session.begin_nested():
                        await hook(session, job)
                except Exception:  # noqa: BLE001
                    log.exception("suite de l'abandon du job %s #%s", job.type, job.id)
        return await queue.claim(session, limit)


async def schedule_due_feeds() -> int:
    """Met en file un job de rafraîchissement pour chaque flux arrivé à échéance."""
    now = datetime.now(UTC)
    count = 0
    async with session_scope() as session:
        # Avant de lire les flux : un import ne peut pas s'intercaler entre la lecture et la mise en file.
        await maintenance.guard(session)
        result = await session.execute(
            select(Feed.id).where(Feed.disabled.is_(False), Feed.source == "rss", Feed.next_fetch_at <= now)
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
    validate_runtime_config(settings)
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
                try:
                    scheduled = await schedule_due_feeds()
                except maintenance.Busy:
                    scheduled = 0  # import en cours : on replanifiera au tour suivant
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
                jobs = await claim_jobs(free_slots)
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
