from __future__ import annotations

import json
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Auth
from .db import get_session
from .models import Entry, Feed, Job, Summary
from .schemas import VIEW_MODES
from .settings_store import (
    DEFAULTS,
    LAYOUT_VERSION_KEY,
    SECRET_KEYS,
    get_all_settings,
    get_layout_version,
    get_setting,
    mask_secret,
    set_setting,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
Session = Annotated[AsyncSession, Depends(get_session)]

EDITABLE_KEYS = set(DEFAULTS.keys())
MAX_LAYOUT_BYTES = 64 * 1024


def _validate_layout(value: str) -> None:
    """Garde-fou : un client bogué ne doit pas pouvoir écraser la disposition avec du texte corrompu."""
    if not value:
        return
    if len(value.encode()) > MAX_LAYOUT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Disposition invalide : trop volumineuse")
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Disposition invalide : JSON illisible") from exc
    if not isinstance(data, dict) or not isinstance(data.get("widgets"), list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Disposition invalide : structure inattendue")


class SettingsOut(BaseModel):
    values: dict[str, str]
    secrets_set: dict[str, bool]
    # Version courante de `dashboard_layout` (0 = jamais écrite) ; à renvoyer dans `dashboard_layout_base_version`.
    dashboard_layout_version: int = 0


class SettingsPatch(BaseModel):
    values: dict[str, str]
    # Version de la disposition sur laquelle le client s'appuie quand il envoie `dashboard_layout`.
    dashboard_layout_base_version: int | None = None


class StatsOut(BaseModel):
    feeds: int
    entries: int
    unread: int
    starred: int
    summaries: int
    jobs_queued: int
    jobs_failed: int
    summary_input_tokens: int
    summary_output_tokens: int
    # Disque du serveur, vu du conteneur de l'API : même système de fichiers que le volume Postgres
    # dans l'installation Compose par défaut. `None` si la mesure est impossible.
    disk_free_bytes: int | None = None
    disk_low: bool = False


DISK_LOW_BYTES = 2 * 1024**3


def disk_is_low(free: int, total: int) -> bool:
    """Moins de 2 Gio ou moins de 5 % : Postgres s'arrête quand le disque est plein (incident du 12/11/2026)."""
    return free < DISK_LOW_BYTES or free * 20 < total


def _disk_status() -> tuple[int | None, bool]:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return None, False
    return usage.free, disk_is_low(usage.free, usage.total)


@router.get("", response_model=SettingsOut)
async def get_settings_view(_: Auth, session: Session) -> SettingsOut:
    values = await get_all_settings(session)
    secrets_set = {k: bool(values.get(k)) for k in SECRET_KEYS}
    for k in SECRET_KEYS:
        values[k] = mask_secret(values.get(k, ""))
    return SettingsOut(values=values, secrets_set=secrets_set, dashboard_layout_version=await get_layout_version(session))


async def _check_layout_version(session: AsyncSession, base_version: int | None) -> int:
    """Sérialise les écritures de la disposition et refuse celles fondées sur une version dépassée.

    Renvoie la nouvelle version à enregistrer. Le verrou consultatif couvre le cas de deux appareils
    qui envoient en même temps : le second lit la version déjà incrémentée et reçoit 409.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(727003)"))
    current = await get_layout_version(session)
    if (base_version or 0) != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "La disposition a été modifiée depuis un autre appareil",
                "dashboard_layout": await get_setting(session, "dashboard_layout") or "",
                "dashboard_layout_version": current,
            },
        )
    return current + 1


@router.patch("", response_model=SettingsOut)
async def patch_settings(body: SettingsPatch, _: Auth, session: Session) -> SettingsOut:
    if "dashboard_layout" in body.values:
        next_version = await _check_layout_version(session, body.dashboard_layout_base_version)
        await set_setting(session, LAYOUT_VERSION_KEY, str(next_version))
    for key, value in body.values.items():
        if key not in EDITABLE_KEYS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Paramètre inconnu: {key}")
        if key in SECRET_KEYS and value and "…" in value:
            continue  # valeur masquée renvoyée telle quelle par le front : on ne touche pas
        if key == "dashboard_layout":
            _validate_layout(value)
        if key == "llm_model" and len(value.strip()) > 100:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Identifiant de modèle trop long (100 caractères maximum)")
        if key == "llm_auto_model" and len(value.strip()) > 100:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Identifiant de modèle trop long (100 caractères maximum)")
        if key == "llm_auto_daily_token_budget" and not (value.strip().isdigit() and int(value) <= 10_000_000):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Budget quotidien invalide (entier de 0 à 10 000 000 tokens)")
        if key == "default_view_mode" and value not in VIEW_MODES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Présentation inconnue")
        if key == "sync_prefetch_days" and not (value.strip().isdigit() and 1 <= int(value) <= 365):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sync_prefetch_days doit être un entier entre 1 et 365")
        await set_setting(session, key, value.strip())
    await session.commit()
    return await get_settings_view(_, session)


@router.get("/stats", response_model=StatsOut)
async def stats(_: Auth, session: Session) -> StatsOut:
    feeds = (await session.execute(select(func.count()).select_from(Feed))).scalar_one()
    entries = (await session.execute(select(func.count()).select_from(Entry))).scalar_one()
    unread = (await session.execute(select(func.count()).where(Entry.read.is_(False)))).scalar_one()
    starred = (await session.execute(select(func.count()).where(Entry.starred.is_(True)))).scalar_one()
    summaries = (await session.execute(select(func.count()).select_from(Summary))).scalar_one()
    tokens = (
        await session.execute(
            select(func.coalesce(func.sum(Summary.input_tokens), 0), func.coalesce(func.sum(Summary.output_tokens), 0))
        )
    ).one()
    queued = (await session.execute(select(func.count()).where(Job.status.in_(["queued", "running"])))).scalar_one()
    failed = (await session.execute(select(func.count()).where(Job.status == "failed"))).scalar_one()
    disk_free, disk_low = _disk_status()
    return StatsOut(
        feeds=feeds,
        entries=entries,
        unread=unread,
        starred=starred,
        summaries=summaries,
        jobs_queued=queued,
        jobs_failed=failed,
        summary_input_tokens=int(tokens[0]),
        summary_output_tokens=int(tokens[1]),
        disk_free_bytes=disk_free,
        disk_low=disk_low,
    )
