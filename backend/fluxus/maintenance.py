"""Verrou de maintenance : exclusif pendant un import de sauvegarde, partagé par tout ce qui agit sur des
identifiants de la base (jobs du worker, planificateur, mises en file, générations à la demande).

Un import remplace la base en conservant les identifiants : un travail commencé avant et terminé après
viserait un autre article ou un autre flux. Hors du worker, on ne fait pas la queue derrière un
import : on refuse (`Busy`, 503) et l'utilisateur réessaie.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session

# Distincte de celle de l'enregistrement du tableau de bord (727003).
MAINTENANCE_LOCK_KEY = 727004

# Vrai dans une tâche qui tient déjà le verrou partagé (job du worker, génération à la demande) : les
# mises en file qu'elle fait n'ont pas à le reprendre. Les tâches asyncio copient le contexte.
_held: ContextVar[bool] = ContextVar("maintenance_lock_held", default=False)


class Busy(RuntimeError):
    """Import de sauvegarde en cours (ou, pour l'import, tâches de fond trop longues) : rien n'est modifié."""


IMPORT_RUNNING = "Import de sauvegarde en cours : réessayez dans une minute"


@asynccontextmanager
async def shared(*, wait: bool = False) -> AsyncIterator[None]:
    """Verrou partagé tenu pendant tout le bloc, sur une connexion dédiée (le bloc peut commit en cours
    de route). `wait=False` : `Busy` si un import est en cours ou en attente."""
    from .db import get_engine

    async with get_engine().connect() as conn:
        if wait:
            await conn.execute(select(func.pg_advisory_lock_shared(MAINTENANCE_LOCK_KEY)))
        elif not (await conn.execute(select(func.pg_try_advisory_lock_shared(MAINTENANCE_LOCK_KEY)))).scalar_one():
            raise Busy(IMPORT_RUNNING)
        await conn.commit()  # verrou de session : il survit au commit, pas de transaction ouverte
        token = _held.set(True)
        try:
            yield
        finally:
            _held.reset(token)
            await conn.execute(select(func.pg_advisory_unlock_shared(MAINTENANCE_LOCK_KEY)))
            await conn.commit()


async def guard(session: AsyncSession) -> None:
    """Verrou partagé jusqu'à la fin de la transaction de `session` : un import ne peut ni être en cours
    ni démarrer avant son commit. Sans effet dans une tâche qui tient déjà le verrou."""
    if _held.get():
        return
    if not (await session.execute(select(func.pg_try_advisory_xact_lock_shared(MAINTENANCE_LOCK_KEY)))).scalar_one():
        raise Busy(IMPORT_RUNNING)


EPOCH_HEADER = "X-Fluxus-Epoch"
STALE_EPOCH_CODE = "stale_epoch"


async def epoch_guard(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    """Dépendance des routeurs qui écrivent par identifiant (articles, flux, dossiers, flux intelligents).

    Toute écriture est refusée pendant un import (`Busy`, 503). Si le client annonce l'époque de son
    cache (en-tête `X-Fluxus-Epoch`, envoyé par le front), une époque périmée est refusée en 409 avec le
    code `stale_epoch` : depuis un import, ses identifiants désignent d'autres contenus. Sans en-tête
    (scripts, anciens clients), seule la garde d'import s'applique.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    await guard(session)
    announced = request.headers.get(EPOCH_HEADER)
    if announced is None:
        return
    from .backup.service import sync_epoch

    if unquote(announced) != await sync_epoch(session):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": STALE_EPOCH_CODE, "message": "La base du serveur a été remplacée : resynchronisation nécessaire"},
        )
