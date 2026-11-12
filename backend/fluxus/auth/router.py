from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import DeviceToken
from ..settings_store import get_setting, set_setting
from .deps import Auth
from .security import (
    create_access_token,
    generate_device_token,
    hash_device_token,
    hash_password,
    login_limiter,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

Session = Annotated[AsyncSession, Depends(get_session)]


class AuthStatus(BaseModel):
    setup_required: bool


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)
    # Révoque aussi les tokens de l'application mobile (à réappairer).
    revoke_devices: bool = False


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Le mot de passe est redemandé pour créer un token longue durée.
    password: str


class DeviceCreated(BaseModel):
    id: int
    name: str
    token: str


class DeviceOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_seen_at: datetime | None


def _client_key(request: Request) -> str:
    # Ne jamais lire X-Forwarded-For soi-même : c'est uvicorn (--proxy-headers, --forwarded-allow-ips)
    # qui remplace request.client par l'adresse transmise par le proxy de confiance.
    return request.client.host if request.client else "unknown"


@router.get("/status", response_model=AuthStatus)
async def auth_status(session: Session) -> AuthStatus:
    password_hash = await get_setting(session, "_password_hash", default="")
    return AuthStatus(setup_required=not password_hash)


@router.post("/setup", response_model=TokenResponse)
async def setup(body: PasswordBody, session: Session) -> TokenResponse:
    existing = await get_setting(session, "_password_hash", default="")
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Le mot de passe est déjà défini")
    await set_setting(session, "_password_hash", hash_password(body.password))
    version = datetime.now(UTC).isoformat()
    await set_setting(session, "_password_version", version)
    await session.commit()
    return TokenResponse(access_token=create_access_token(version))


async def password_matches(request: Request, session: AsyncSession, password: str) -> bool:
    """Vérifie le mot de passe sous limitation de tentatives (429 si un plafond est atteint)."""
    key = _client_key(request)
    stamp = login_limiter.acquire(key)
    if stamp is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives, réessayez plus tard")
    password_hash = await get_setting(session, "_password_hash", default="")
    if not password_hash or not verify_password(password, password_hash):
        return False
    login_limiter.release(key, stamp)
    return True


@router.post("/login", response_model=TokenResponse)
async def login(body: PasswordBody, request: Request, session: Session) -> TokenResponse:
    if not await password_matches(request, session, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe incorrect")
    version = await get_setting(session, "_password_version", default="")
    return TokenResponse(access_token=create_access_token(version or ""))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(principal: Auth, session: Session) -> TokenResponse:
    # Un token d'appareil ne peut pas se transformer en JWT : sa révocation resterait sans effet.
    if principal != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé aux sessions web")
    version = await get_setting(session, "_password_version", default="")
    return TokenResponse(access_token=create_access_token(version or ""))


@router.post("/password", response_model=TokenResponse)
async def change_password(body: ChangePasswordBody, _: Auth, session: Session) -> TokenResponse:
    """Change le mot de passe et invalide toutes les sessions web ; renvoie un nouveau JWT pour la session courante."""
    password_hash = await get_setting(session, "_password_hash", default="")
    if not password_hash or not verify_password(body.current_password, password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe actuel incorrect")
    await set_setting(session, "_password_hash", hash_password(body.new_password))
    version = datetime.now(UTC).isoformat()
    await set_setting(session, "_password_version", version)
    if body.revoke_devices:
        for device in (await session.execute(select(DeviceToken))).scalars():
            await session.delete(device)
    await session.commit()
    return TokenResponse(access_token=create_access_token(version))


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(_: Auth, session: Session) -> list[DeviceOut]:
    result = await session.execute(select(DeviceToken).order_by(DeviceToken.created_at.desc()))
    return [DeviceOut.model_validate(d, from_attributes=True) for d in result.scalars()]


@router.post("/devices", response_model=DeviceCreated, status_code=201)
async def create_device(body: DeviceCreate, request: Request, session: Session) -> DeviceCreated:
    """Crée un token d'appareil. Ne nécessite pas de session : le mot de passe suffit,
    ce qui permet à l'appli mobile de s'appairer directement."""
    if not await password_matches(request, session, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mot de passe incorrect")
    token = generate_device_token()
    device = DeviceToken(name=body.name, token_hash=hash_device_token(token))
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return DeviceCreated(id=device.id, name=device.name, token=token)


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(device_id: int, _: Auth, session: Session) -> None:
    device = await session.get(DeviceToken, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appareil introuvable")
    await session.delete(device)
    await session.commit()
