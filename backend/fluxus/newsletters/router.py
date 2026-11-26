from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Auth
from ..db import get_session
from ..maintenance import epoch_guard
from ..models import Category, NewsletterAddress
from ..settings_store import get_setting
from .service import (
    MAX_EMAIL_BYTES,
    UnknownAddress,
    address_of,
    create_email_feed,
    get_secret,
    ingest_message,
    new_token,
    rotate_secret,
    verify_signature,
)

Session = Annotated[AsyncSession, Depends(get_session)]

# --- Réception (appelée par l'Email Worker, sans session : la signature fait foi) ---

inbound_router = APIRouter(prefix="/api/inbound", tags=["newsletters"])


class InboundOut(BaseModel):
    status: str
    feed_id: int
    entry_id: int | None = None


@inbound_router.post("/email", status_code=202, response_model=InboundOut)
async def receive_email(request: Request, session: Session) -> InboundOut:
    """Mail brut (RFC 5322) signé par le Worker : en-têtes `X-Fluxus-Timestamp` (ms),
    `X-Fluxus-To` (destinataire) et `X-Fluxus-Signature` (voir `service.sign`)."""
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise HTTPException(status.HTTP_411_LENGTH_REQUIRED, "Taille du message requise")
    if int(length) > MAX_EMAIL_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Message trop volumineux")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EMAIL_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Message trop volumineux")

    secret = await get_secret(session)
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Réception des newsletters non configurée")
    recipient = request.headers.get("x-fluxus-to", "")
    if not verify_signature(
        secret,
        request.headers.get("x-fluxus-timestamp", ""),
        recipient,
        bytes(body),
        request.headers.get("x-fluxus-signature", ""),
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature invalide ou périmée")
    try:
        result = await ingest_message(session, bytes(body), recipient)
    except UnknownAddress as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Adresse inconnue") from exc
    await session.commit()
    return InboundOut(status=result.status, feed_id=result.feed_id, entry_id=result.entry_id)


# --- Gestion (interface) ---------------------------------------------------------

router = APIRouter(prefix="/api/newsletters", tags=["newsletters"], dependencies=[Depends(epoch_guard)])


class NewsletterAddressOut(BaseModel):
    id: int
    token: str
    # Adresse complète ; null tant que le domaine de réception n'est pas réglé.
    email: str | None
    label: str
    feed_id: int | None
    category_id: int | None
    received_count: int
    last_received_at: datetime | None
    created_at: datetime


class NewsletterConfigOut(BaseModel):
    domain: str
    secret_set: bool
    max_bytes: int
    addresses: list[NewsletterAddressOut]


class NewsletterAddressIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    # Vrai : tout ce qui arrive à cette adresse va dans un seul flux, créé tout de suite.
    dedicated: bool = False
    category_id: int | None = None


class SecretOut(BaseModel):
    secret: str


def _out(a: NewsletterAddress, domain: str) -> NewsletterAddressOut:
    return NewsletterAddressOut(
        id=a.id,
        token=a.token,
        email=address_of(a.token, domain),
        label=a.label,
        feed_id=a.feed_id,
        category_id=a.category_id,
        received_count=a.received_count,
        last_received_at=a.last_received_at,
        created_at=a.created_at,
    )


async def _domain(session: AsyncSession) -> str:
    return (await get_setting(session, "newsletter_domain") or "").strip().lower()


@router.get("", response_model=NewsletterConfigOut)
async def get_config(_: Auth, session: Session) -> NewsletterConfigOut:
    domain = await _domain(session)
    rows = (await session.execute(select(NewsletterAddress).order_by(NewsletterAddress.created_at, NewsletterAddress.id))).scalars().all()
    return NewsletterConfigOut(
        domain=domain,
        secret_set=bool(await get_secret(session)),
        max_bytes=MAX_EMAIL_BYTES,
        addresses=[_out(a, domain) for a in rows],
    )


@router.post("/addresses", response_model=NewsletterAddressOut, status_code=201)
async def create_address(body: NewsletterAddressIn, _: Auth, session: Session) -> NewsletterAddressOut:
    label = body.label.strip()
    if not label:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nom de l'adresse requis")
    if body.category_id is not None and await session.get(Category, body.category_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catégorie introuvable")
    token = new_token()
    address = NewsletterAddress(token=token, label=label, category_id=body.category_id)
    if body.dedicated:
        feed = await create_email_feed(session, url=f"email:address:{token}", title=label, category_id=body.category_id)
        address.feed_id = feed.id
    session.add(address)
    await session.commit()
    await session.refresh(address)
    return _out(address, await _domain(session))


@router.delete("/addresses/{address_id}", status_code=204)
async def delete_address(address_id: int, _: Auth, session: Session) -> None:
    """Révoque l'adresse : les mails suivants sont rejetés. Le flux et ses articles restent."""
    address = await session.get(NewsletterAddress, address_id)
    if address is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Adresse introuvable")
    await session.delete(address)
    await session.commit()


@router.post("/secret", response_model=SecretOut)
async def regenerate_secret(_: Auth, session: Session) -> SecretOut:
    """Nouveau secret partagé avec le Worker, affiché une seule fois ; l'ancien cesse de valoir."""
    value = await rotate_secret(session)
    await session.commit()
    return SecretOut(secret=value)
