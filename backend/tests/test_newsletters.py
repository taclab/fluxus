"""Réception des newsletters de bout en bout : adresses, signature du Worker, flux et articles créés."""

from __future__ import annotations

import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_newsletter_parse import build_email

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]

DOMAIN = "news.example.org"


@pytest.fixture(scope="module")
async def client():
    os.environ.setdefault("FLUXUS_STATIC_DIR", "/nonexistent")
    from sqlalchemy import text

    from fluxus.db import Base, get_engine
    from fluxus.main import app
    from fluxus.models import EXTENSION_STATEMENTS, SEARCH_VECTOR_STATEMENTS

    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in EXTENSION_STATEMENTS:
            await conn.execute(text(stmt))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in SEARCH_VECTOR_STATEMENTS:
            await conn.execute(text(stmt))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


state: dict = {}


async def deliver(client: AsyncClient, raw: bytes, to: str, *, secret: str | None = None, ts: str | None = None, signature: str | None = None):
    """Ce que fait l'Email Worker : POST du mail brut signé, sans jeton de session."""
    from fluxus.newsletters.service import sign

    ts = ts or str(int(time.time() * 1000))
    sig = signature if signature is not None else sign(secret or state["secret"], ts, to, raw)
    headers = {"X-Fluxus-Timestamp": ts, "X-Fluxus-To": to, "X-Fluxus-Signature": sig, "Authorization": ""}
    return await client.post("/api/inbound/email", content=raw, headers=headers)


async def test_setup_domain_and_addresses(client: AsyncClient):
    r = await client.get("/api/newsletters")
    assert r.status_code == 200
    assert r.json()["domain"] == "" and r.json()["secret_set"] is False and r.json()["addresses"] == []

    r = await client.patch("/api/settings", json={"values": {"newsletter_domain": "pas un domaine"}})
    assert r.status_code == 400
    r = await client.patch("/api/settings", json={"values": {"newsletter_domain": " News.Example.org. "}})
    assert r.status_code == 200 and r.json()["values"]["newsletter_domain"] == DOMAIN

    cat = (await client.post("/api/categories", json={"title": "Lettres"})).json()
    r = await client.post("/api/newsletters/addresses", json={"label": "Principale", "category_id": cat["id"]})
    assert r.status_code == 201
    main = r.json()
    assert len(main["token"]) == 12 and main["email"] == f"{main['token']}@{DOMAIN}" and main["feed_id"] is None
    r = await client.post("/api/newsletters/addresses", json={"label": "Lettre privée", "dedicated": True})
    dedicated = r.json()
    assert dedicated["feed_id"] is not None and dedicated["token"] != main["token"]
    feed = (await client.get(f"/api/feeds/{dedicated['feed_id']}")).json()
    assert feed["source"] == "email" and feed["title"] == "Lettre privée"
    r = await client.post("/api/newsletters/addresses", json={"label": "x", "category_id": 999999})
    assert r.status_code == 404
    state.update(main=main, dedicated=dedicated, category_id=cat["id"])


async def test_inbound_refused_until_secret_then_checks_signature(client: AsyncClient):
    raw = build_email()
    to = state["main"]["email"]
    r = await deliver(client, raw, to, secret="nimporte")
    assert r.status_code == 503  # aucun secret : la réception n'est pas configurée

    r = await client.post("/api/newsletters/secret")
    assert r.status_code == 200
    state["secret"] = r.json()["secret"]
    assert len(state["secret"]) >= 40
    assert (await client.get("/api/newsletters")).json()["secret_set"] is True
    # Le secret ne sort jamais par les réglages.
    settings = (await client.get("/api/settings")).json()
    assert all(state["secret"] not in v for v in settings["values"].values())
    assert not any(k.startswith("_") for k in settings["values"])

    assert (await deliver(client, raw, to, secret="mauvais")).status_code == 401
    assert (await deliver(client, raw, to, signature="")).status_code == 401
    old = str(int(time.time() * 1000) - 10 * 60 * 1000)
    assert (await deliver(client, raw, to, ts=old)).status_code == 401
    # Signature valide pour un autre destinataire : refusée.
    from fluxus.newsletters.service import sign

    ts = str(int(time.time() * 1000))
    other = sign(state["secret"], ts, f"autre@{DOMAIN}", raw)
    assert (await deliver(client, raw, to, ts=ts, signature=other)).status_code == 401
    assert (await deliver(client, raw, f"inconnu12345@{DOMAIN}")).status_code == 404
    r = await client.post("/api/inbound/email", content=b"x", headers={"Content-Length": str(20 * 1024 * 1024), "X-Fluxus-To": to})
    assert r.status_code == 413


async def test_newsletter_becomes_a_feed_and_an_entry(client: AsyncClient):
    r = await deliver(client, build_email(), state["main"]["email"])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "created"
    feed = (await client.get(f"/api/feeds/{body['feed_id']}")).json()
    assert feed["source"] == "email"
    assert feed["title"] == "La Lettre Tech"
    assert feed["url"] == "email:sender:a1b2c3.list-id.lettre.example"
    assert feed["category_id"] == state["category_id"]
    assert feed["icon_url"] == "https://lettre.example/favicon.ico"
    assert feed["extraction_mode"] == "none"
    entry = (await client.get(f"/api/entries/{body['entry_id']}")).json()
    assert entry["title"] == "Numéro 42 : les nouvelles"
    assert entry["author"] is None  # déjà le titre du flux
    assert entry["url"] == "https://lettre.example/web/42"
    assert "Les nouvelles de la semaine" in entry["summary_html"]
    assert entry["cover"] == "https://cdn.example/banniere.jpg"
    assert entry["meta"]["unsubscribe_url"] == "https://lettre.example/unsub?u=1"
    assert entry["published_at"].startswith("2026-11-24T06:30:00")
    # Recherche plein texte sur le contenu du mail.
    found = (await client.get("/api/entries", params={"q": "Colonne"})).json()["items"]
    assert [e["id"] for e in found] == [body["entry_id"]]
    state["sender_feed_id"] = body["feed_id"]

    # Même message renvoyé (nouvel essai du Worker) : pas de doublon.
    again = await deliver(client, build_email(), state["main"]["email"])
    assert again.json() == {"status": "duplicate", "feed_id": body["feed_id"], "entry_id": body["entry_id"]}
    # Numéro suivant de la même liste : même flux.
    nxt = await deliver(client, build_email(headers={"Message-ID": "<numero-43@lettre.example>", "Subject": "Numéro 43"}), state["main"]["email"])
    assert nxt.json()["status"] == "created" and nxt.json()["feed_id"] == body["feed_id"]
    addresses = (await client.get("/api/newsletters")).json()["addresses"]
    main = next(a for a in addresses if a["id"] == state["main"]["id"])
    assert main["received_count"] == 3 and main["last_received_at"]


async def test_dedicated_address_and_paused_feed(client: AsyncClient):
    raw = build_email(headers={"Message-ID": "<autre@ailleurs.example>", "From": "Autre <x@ailleurs.example>", "List-Id": None})
    r = await deliver(client, raw, state["dedicated"]["email"])
    assert r.json()["status"] == "created" and r.json()["feed_id"] == state["dedicated"]["feed_id"]
    entry = (await client.get(f"/api/entries/{r.json()['entry_id']}")).json()
    assert entry["author"] == "Autre"  # flux dédié : l'expéditeur reste utile

    r = await client.put(f"/api/feeds/{state['sender_feed_id']}", json={"disabled": True})
    assert r.status_code == 200
    raw = build_email(headers={"Message-ID": "<numero-44@lettre.example>"})
    r = await deliver(client, raw, state["main"]["email"])
    assert r.status_code == 202 and r.json()["status"] == "paused" and r.json()["entry_id"] is None
    await client.put(f"/api/feeds/{state['sender_feed_id']}", json={"disabled": False})


async def test_email_feeds_are_never_fetched(client: AsyncClient):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from fluxus.db import session_scope
    from fluxus.models import Feed, Job
    from fluxus.worker import schedule_due_feeds

    r = await client.post(f"/api/feeds/{state['sender_feed_id']}/refresh")
    assert r.json() == {"queued": False}
    r = await client.put(f"/api/feeds/{state['sender_feed_id']}", json={"url": "https://exemple.org/rss"})
    assert r.status_code == 400
    async with session_scope() as session:
        await session.execute(update(Feed).values(next_fetch_at=datetime.now(UTC) - timedelta(minutes=1)))
    await schedule_due_feeds()
    await client.post("/api/feeds/refresh-all")
    async with session_scope() as session:
        jobs = (await session.execute(select(Job).where(Job.type == "fetch_feed"))).scalars().all()
        assert jobs == []
    opml = (await client.get("/api/opml/export")).text
    assert "email:" not in opml


async def test_revoked_address_and_deleted_feed(client: AsyncClient):
    r = await client.delete(f"/api/newsletters/addresses/{state['main']['id']}")
    assert r.status_code == 204
    raw = build_email(headers={"Message-ID": "<numero-45@lettre.example>"})
    assert (await deliver(client, raw, state["main"]["email"])).status_code == 404
    # Le flux et ses articles restent après la révocation.
    assert (await client.get(f"/api/feeds/{state['sender_feed_id']}")).status_code == 200

    # Supprimer le flux d'une adresse dédiée supprime l'adresse.
    assert (await client.delete(f"/api/feeds/{state['dedicated']['feed_id']}")).status_code == 204
    assert (await client.get("/api/newsletters")).json()["addresses"] == []
    assert (await deliver(client, raw, state["dedicated"]["email"])).status_code == 404


async def test_backup_carries_addresses_not_secret(client: AsyncClient):
    import gzip
    import json

    r = await client.post("/api/newsletters/addresses", json={"label": "Sauvée"})
    token = r.json()["token"]
    link = (await client.post("/api/backup/export-link")).json()
    r = await client.get(link["url"])
    lines = [json.loads(line) for line in gzip.decompress(r.content).decode().splitlines()]
    tables = {line.get("t") for line in lines}
    assert "newsletter_addresses" in tables
    assert any(line.get("t") == "newsletter_addresses" and line["r"]["token"] == token for line in lines)
    assert state["secret"] not in r.content.decode("latin-1") and state["secret"] not in gzip.decompress(r.content).decode()
