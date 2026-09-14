from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import DeviceToken
from .security import DEVICE_TOKEN_PREFIX, decode_access_token, hash_device_token

bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> str:
    """Accepte un JWT (web) ou un token d'appareil (mobile). Renvoie le type de principal."""
    token = creds.credentials if creds else request.query_params.get("token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")

    if token.startswith(DEVICE_TOKEN_PREFIX):
        result = await session.execute(
            select(DeviceToken).where(DeviceToken.token_hash == hash_device_token(token))
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token d'appareil invalide")
        # Mise à jour paresseuse de last_seen_at (au plus une fois par heure).
        now = datetime.now(UTC)
        if device.last_seen_at is None or (now - device.last_seen_at).total_seconds() > 3600:
            device.last_seen_at = now
            await session.commit()
        return f"device:{device.id}"

    payload = decode_access_token(token)
    if payload is None or payload.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée")
    return "owner"


Auth = Annotated[str, Depends(require_auth)]
