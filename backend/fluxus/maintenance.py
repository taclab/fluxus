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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
