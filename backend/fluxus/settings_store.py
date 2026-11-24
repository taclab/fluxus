"""Paramètres persistés en base (table `settings`), avec chiffrement des secrets."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import Setting

SECRET_KEYS = {"llm_api_key_anthropic", "llm_api_key_openai"}

# Valeurs par défaut des réglages exposés dans l'interface.
DEFAULTS: dict[str, str] = {
    "llm_provider": "anthropic",
    "llm_model": "claude-opus-5",
    "llm_effort": "low",
    "llm_language": "fr",
    "llm_api_key_anthropic": "",
    "llm_api_key_openai": "",
    "openai_base_url": "",
    # Type de résumé par défaut (`summarize/prompts.STYLES`), aussi celui des résumés automatiques.
    "llm_default_style": "standard",
    # Consignes personnelles ajoutées à chaque résumé (1000 caractères au plus).
    "llm_instructions": "",
    # Modèle du mode « approfondi » (même fournisseur) ; vide = modèle courant à l'effort maximal.
    "llm_deep_model": "",
    # Horizon (jours) des articles conservés sur les appareils et envoyés à la première synchronisation.
    "sync_prefetch_days": "30",
    # Résumés automatiques des flux intelligents : modèle dédié (économique) et budget de tokens par jour UTC (0 = désactivé).
    "llm_auto_model": "claude-sonnet-5",
    "llm_auto_daily_token_budget": "200000",
    # Disposition du tableau de bord (JSON produit par le front ; vide = disposition par défaut).
    "dashboard_layout": "",
    # Présentation de liste des vues sans présentation propre (flux, dossier ou flux intelligent).
    "default_view_mode": "compact",
}

# Compteur d'écritures de la disposition (clé interne, non éditable directement) : un client doit
# annoncer la version sur laquelle il se base, sinon il écraserait la disposition d'un autre appareil.
LAYOUT_VERSION_KEY = "_dashboard_layout_version"


async def get_layout_version(session: AsyncSession) -> int:
    raw = await get_setting(session, LAYOUT_VERSION_KEY, default="0")
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = await session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default) if default is None else default
    return decrypt(row.value) if key in SECRET_KEYS else row.value


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    stored = encrypt(value) if key in SECRET_KEYS else value
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=stored))
    else:
        row.value = stored
    await session.flush()


async def get_all_settings(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(select(Setting))
    values = dict(DEFAULTS)
    for row in result.scalars():
        if row.key.startswith("_"):
            continue
        values[row.key] = decrypt(row.value) if row.key in SECRET_KEYS else row.value
    return values


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"••••…{value[-4:]}"
