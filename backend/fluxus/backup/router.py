from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ..auth import Auth
from ..auth.router import _client_key, login_limiter
from ..auth.security import hash_device_token, verify_password
from ..db import get_session, session_factory
from ..models import Setting
from ..settings_store import get_setting, set_setting
from . import service

router = APIRouter(prefix="/api/backup", tags=["backup"])
Session = Annotated[AsyncSession, Depends(get_session)]

MAX_IMPORT_BYTES = 1024 * 1024 * 1024
CONFIRM_WORD = "REMPLACER"
# Lien d'export à usage unique : le navigateur télécharge directement sur le disque (pas de copie en
# mémoire dans l'onglet). Un seul lien valide à la fois ; seule son empreinte est stockée.
EXPORT_TICKET_KEY = "_backup_export_ticket"
EXPORT_TICKET_TTL = timedelta(seconds=60)


class ExportLink(BaseModel):
    # Chemin relatif à l'URL du serveur, valable une fois et pendant une minute.
    url: str


class ImportResult(BaseModel):
    counts: dict[str, int]
    exported_at: str | None = None


def _header_or_none(file: UploadFile) -> dict | None:
    try:
        return service.read_header(file.file)
    except service.InvalidArchive:
        return None


@router.post("/export-link", response_model=ExportLink)
async def export_link(_: Auth, session: Session) -> ExportLink:
    """Lien de téléchargement de la sauvegarde, à usage unique (remplace tout lien précédent)."""
    ticket = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + EXPORT_TICKET_TTL
    await set_setting(session, EXPORT_TICKET_KEY, json.dumps({"hash": hash_device_token(ticket), "expires": expires.isoformat()}))
    await session.commit()
    return ExportLink(url=f"/api/backup/export?ticket={ticket}")


async def _consume_ticket(session: AsyncSession, ticket: str) -> bool:
    """Vrai si `ticket` est le lien en cours et n'a pas expiré ; il est alors effacé (usage unique).
    Un ticket faux n'efface rien : un tiers ne peut pas invalider le lien de l'utilisateur."""
    row = (await session.execute(select(Setting).where(Setting.key == EXPORT_TICKET_KEY).with_for_update())).scalar_one_or_none()
    try:
        stored = json.loads(row.value) if row is not None else None
    except ValueError:
        stored = None
    if stored is None or not hmac.compare_digest(stored.get("hash", ""), hash_device_token(ticket)):
        await session.rollback()
        return False
    await session.delete(row)
    await session.commit()
    return datetime.fromisoformat(stored["expires"]) > datetime.now(UTC)


@router.get("/export", response_class=StreamingResponse)
async def export_backup(session: Session, ticket: str = Query(..., max_length=128)) -> StreamingResponse:
    """Toute la base en un fichier .jsonl.gz (sans mot de passe, jetons d'appareils ni clés API).

    Sans en-tête d'authentification : le ticket vient de `POST /api/backup/export-link`."""
    if not await _consume_ticket(session, ticket):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Lien de sauvegarde invalide ou expiré : relancez l'export")

    async def body():
        # Session propre au flux : celle de la requête est fermée avant la fin de l'envoi.
        async with session_factory()() as session:
            async for chunk in service.export_stream(session):
                yield chunk

    name = f"fluxus-export-{datetime.now(UTC):%Y%m%d-%H%M}.jsonl.gz"
    return StreamingResponse(body(), media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post(
    "/import",
    response_model=ImportResult,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file", "password", "confirm"],
                        "properties": {"file": {"type": "string", "format": "binary"}, "password": {"type": "string"}, "confirm": {"type": "string"}},
                    }
                }
            },
        }
    },
)
async def import_backup(request: Request, _: Auth, session: Session) -> ImportResult:
    """Remplace tout le contenu de l'instance par la sauvegarde (multipart `file`, `password`,
    `confirm`) : mot de passe actuel et mot de confirmation exigés ; tout ou rien.

    Le formulaire est lu ici, après l'authentification et le contrôle de `Content-Length` : un
    envoi trop gros est refusé avant d'être écrit sur disque. Sans cet en-tête (envoi « chunked »),
    la taille ne se connaîtrait qu'une fois tout écrit : refusé (un navigateur l'envoie toujours)."""
    try:
        length = int(request.headers["content-length"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_411_LENGTH_REQUIRED, "Taille de l'envoi inconnue (en-tête Content-Length requis)") from None
    if length > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Sauvegarde trop volumineuse (1 Go au plus)")
    form = await request.form(max_files=1, max_fields=5)
    file, password, confirm = form.get("file"), form.get("password"), form.get("confirm")
    if not isinstance(file, UploadFile) or not isinstance(password, str) or not isinstance(confirm, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Formulaire incomplet : fichier, mot de passe et confirmation attendus")
    if file.size is not None and file.size > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Sauvegarde trop volumineuse (1 Go au plus)")
    if confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Confirmation manquante : saisissez « {CONFIRM_WORD} »")
    key = _client_key(request)
    if not login_limiter.check(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives, réessayez plus tard")
    password_hash = await get_setting(session, "_password_hash", default="")
    if not password_hash or not verify_password(password, password_hash):
        login_limiter.record_failure(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    login_limiter.reset(key)
    try:
        header, counts = await service.import_replace(session, file.file)
        await session.commit()
    except service.InvalidArchive as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except service.Busy as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except (IntegrityError, DBAPIError, ValueError, KeyError) as exc:
        await session.rollback()
        detail = f"Sauvegarde incohérente, rien n'a été modifié : {str(exc).splitlines()[0][:300]}"
        # Cause la plus probable d'un refus de la base : sauvegarde d'un autre schéma.
        header = await asyncio.to_thread(_header_or_none, file)
        current = await service.schema_revision(session)
        if header and header.get("schema") and current and header["schema"] != current:
            detail += f" (sauvegarde du schéma {header['schema']}, cette instance est en {current} : mettez les deux instances à la même version)"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from exc
    return ImportResult(counts=counts, exported_at=header.get("exported_at"))
