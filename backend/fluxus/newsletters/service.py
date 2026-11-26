"""Réception des newsletters : secret partagé avec le Worker de réception, adresses, ingestion.

`ingest_message` ne dépend pas du transport : l'Email Worker Cloudflare l'alimente aujourd'hui
(`router.receive_email`), une relève IMAP pourrait le faire demain.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import maintenance
from ..models import Entry, Feed, NewsletterAddress, Setting
from ..settings_store import decrypt, encrypt
from .parse import ParsedEmail, parse_email

# Réglage interne (jamais exporté ni renvoyé par /api/settings), chiffré comme les clés API.
SECRET_SETTING = "_newsletter_secret"
# Cloudflare Email Routing accepte jusqu'à 25 Mio ; au-delà de cette borne le Worker renvoie le mail
# vers l'adresse de secours (une newsletter dépasse rarement quelques centaines de Ko).
MAX_EMAIL_BYTES = 15 * 1024 * 1024
SIGNATURE_WINDOW_SECONDS = 300
TOKEN_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # sans l, o, 0, 1 : se recopie sans erreur
TOKEN_LENGTH = 12  # 60 bits : impossible à deviner, même en essayant des adresses en masse


class UnknownAddress(LookupError):
    """Adresse jamais créée ou révoquée : le Worker rejette le mail."""


def new_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def token_of(recipient: str) -> str:
    return recipient.strip().strip("<>").rpartition("@")[0].lower() if "@" in recipient else recipient.strip().lower()


def address_of(token: str, domain: str | None) -> str | None:
    return f"{token}@{domain}" if domain else None


# --- Signature du Worker -------------------------------------------------------


def sign(secret: str, timestamp: str, recipient: str, body: bytes) -> str:
    """HMAC-SHA256 hexadécimal de `<horodatage ms>.<destinataire en minuscules>.<sha256 hex du corps>`.
    Même calcul dans `cloudflare/email-worker/worker.js`."""
    payload = f"{timestamp}.{recipient.strip().lower()}.{hashlib.sha256(body).hexdigest()}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, recipient: str, body: bytes, signature: str, *, now_ms: int | None = None) -> bool:
    if not secret or not timestamp.isdigit() or not signature:
        return False
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(now_ms - int(timestamp)) > SIGNATURE_WINDOW_SECONDS * 1000:
        return False
    return hmac.compare_digest(sign(secret, timestamp, recipient, body), signature.strip().lower())


async def get_secret(session: AsyncSession) -> str:
    row = await session.get(Setting, SECRET_SETTING)
    return decrypt(row.value) if row is not None else ""


async def rotate_secret(session: AsyncSession) -> str:
    value = secrets.token_urlsafe(32)
    row = await session.get(Setting, SECRET_SETTING)
    if row is None:
        session.add(Setting(key=SECRET_SETTING, value=encrypt(value)))
    else:
        row.value = encrypt(value)
    await session.flush()
    return value


# --- Ingestion -----------------------------------------------------------------


@dataclass
class IngestResult:
    # created | duplicate | paused
    status: str
    feed_id: int
    entry_id: int | None = None


def _favicon(domain: str | None) -> str | None:
    return f"https://{domain}/favicon.ico" if domain else None


async def create_email_feed(session: AsyncSession, *, url: str, title: str, category_id: int | None, domain: str | None = None) -> Feed:
    from ..feeds.router import _first_position

    feed = Feed(
        url=url[:2048],
        source="email",
        title=title[:500] or "Newsletter",
        site_url=f"https://{domain}" if domain else None,
        icon_url=_favicon(domain),
        category_id=category_id,
        position=await _first_position(session, category_id),
        extraction_mode="none",
        next_fetch_at=datetime.now(UTC),
    )
    session.add(feed)
    await session.flush()
    return feed


async def _feed_for(session: AsyncSession, address: NewsletterAddress, parsed: ParsedEmail) -> Feed:
    if address.feed_id is not None:
        feed = await session.get(Feed, address.feed_id)
        assert feed is not None  # ON DELETE CASCADE : l'adresse disparaît avec son flux
        return feed
    url = f"email:sender:{parsed.sender_key}"[:2048]
    for _ in range(2):
        feed = (await session.execute(select(Feed).where(Feed.url == url))).scalar_one_or_none()
        if feed is not None:
            return feed
        try:
            async with session.begin_nested():
                return await create_email_feed(
                    session, url=url, title=parsed.sender_title, category_id=address.category_id, domain=parsed.sender_domain
                )
        except IntegrityError:
            # Même newsletter reçue au même instant par une autre adresse : son flux vient d'être créé.
            continue
    raise RuntimeError("flux de newsletter introuvable après création concurrente")


async def ingest_message(session: AsyncSession, raw: bytes, recipient: str) -> IngestResult:
    """Range un e-mail reçu dans le flux de son adresse (ou de son expéditeur) ; l'appelant commit."""
    from ..fetcher.service import after_new_entries

    await maintenance.guard(session)
    # Verrou sur l'adresse : deux mails simultanés pour elle ne créent ni deux flux ni deux articles.
    address = (
        await session.execute(select(NewsletterAddress).where(NewsletterAddress.token == token_of(recipient)).with_for_update())
    ).scalar_one_or_none()
    if address is None:
        raise UnknownAddress(recipient)

    parsed = await asyncio.to_thread(parse_email, raw)
    now = datetime.now(UTC)
    address.received_count += 1
    address.last_received_at = now
    feed = await _feed_for(session, address, parsed)
    if feed.disabled:
        return IngestResult("paused", feed.id)

    guid = f"email:{parsed.message_id}"[:2048]
    existing = (await session.execute(select(Entry.id).where(Entry.feed_id == feed.id, Entry.guid == guid))).scalar_one_or_none()
    if existing is not None:
        return IngestResult("duplicate", feed.id, existing)

    meta: dict = {"email_from": parsed.from_address}
    if parsed.cover:
        meta["cover"] = parsed.cover
    if parsed.unsubscribe_url:
        meta["unsubscribe_url"] = parsed.unsubscribe_url
    if parsed.unsubscribe_mailto:
        meta["unsubscribe_mailto"] = parsed.unsubscribe_mailto
    published = parsed.published_at if parsed.published_at and parsed.published_at <= now else now
    entry = Entry(
        feed_id=feed.id,
        guid=guid,
        url=parsed.view_online_url,
        title=parsed.subject or parsed.sender_title,
        # Le nom de l'expéditeur est déjà le titre du flux d'une newsletter rangée par expéditeur.
        author=parsed.from_name if parsed.from_name and parsed.from_name != feed.title else None,
        published_at=published,
        summary_html=parsed.html,
        content_text=parsed.text or None,
        hash=hashlib.sha256(guid.encode()).hexdigest(),
        meta=meta,
    )
    session.add(entry)
    feed.last_fetched_at = now
    await session.flush()
    await after_new_entries(session, feed, [entry])
    return IngestResult("created", feed.id, entry.id)
