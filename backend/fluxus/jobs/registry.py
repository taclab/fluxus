from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Job

Handler = Callable[[AsyncSession, Job], Awaitable[None]]

_handlers: dict[str, Handler] = {}


def job_handler(job_type: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        return fn

    return decorator


def get_handler(job_type: str) -> Handler | None:
    return _handlers.get(job_type)
