from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job
from .queue import STALE_AFTER

Handler = Callable[[AsyncSession, Job], Awaitable[None]]

DEFAULT_TIMEOUT = timedelta(minutes=5)

_handlers: dict[str, Handler] = {}
_timeouts: dict[str, timedelta] = {}
_abandon_hooks: dict[str, Handler] = {}


def job_handler(
    job_type: str, *, timeout: timedelta = DEFAULT_TIMEOUT, on_abandoned: Handler | None = None
) -> Callable[[Handler], Handler]:
    """Enregistre le handler d'un type de job.

    `timeout` : durée maximale d'une exécution ; au-delà le job est interrompu et compté comme un
    échec. Toujours inférieure à `STALE_AFTER` : un job encore en cours n'est jamais repris.
    `on_abandoned` : appelé quand le job est abandonné après que le worker a été interrompu à chaque
    tentative, pour que ce qui l'a mis en file ne le remette pas aussitôt.
    """
    if timeout >= STALE_AFTER:
        raise ValueError(f"Le délai du job '{job_type}' doit rester sous {STALE_AFTER}")

    def decorator(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        _timeouts[job_type] = timeout
        if on_abandoned is not None:
            _abandon_hooks[job_type] = on_abandoned
        return fn

    return decorator


def get_handler(job_type: str) -> Handler | None:
    return _handlers.get(job_type)


def get_timeout(job_type: str) -> timedelta:
    return _timeouts.get(job_type, DEFAULT_TIMEOUT)


def get_abandon_hook(job_type: str) -> Handler | None:
    return _abandon_hooks.get(job_type)
