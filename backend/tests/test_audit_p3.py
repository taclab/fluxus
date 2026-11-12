"""Constats P3 de l'audit du 12/11/2026 côté données : verrou du push, garde d'époque, OPML, adresse d'un flux,
changement de mot de passe. Nécessite la base PostgreSQL de test (DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]

STALE = {"X-Fluxus-Epoch": "autre-epoque"}


@pytest.fixture(scope="module")
async def client():
    os.environ.setdefault("FLUXUS_STATIC_DIR", "/nonexistent")
    from sqlalchemy import text

    from fluxus.db import Base, get_engine, session_scope
    from fluxus.main import app
    from fluxus.models import EXTENSION_STATEMENTS, SEARCH_VECTOR_STATEMENTS, Category, Entry, Feed

    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in EXTENSION_STATEMENTS:
            await conn.execute(text(stmt))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in SEARCH_VECTOR_STATEMENTS:
            await conn.execute(text(stmt))

    now = datetime.now(UTC)
    async with session_scope() as session:
        tech = Category(title="Tech", position=1)
        actu = Category(title="Actu", position=2)
        session.add_all([tech, actu])
        await session.flush()
        feeds = [
            Feed(url="https://t1.example/rss", title="Tech A", category_id=tech.id, position=1),
            Feed(url="https://n1.example/rss", title="Sans dossier", category_id=None, position=10),
        ]
        session.add_all(feeds)
        await session.flush()
        for i in range(3):
            session.add(Entry(feed_id=feeds[0].id, guid=f"p3-{i}", url=f"https://x.example/{i}", title=f"Article {i}", published_at=now - timedelta(hours=i), hash=f"p3h{i}"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _entry_ids(client: AsyncClient) -> list[int]:
    return [e["id"] for e in (await client.get("/api/sync")).json()["entries"]]


async def _entry(entry_id: int):
    from fluxus.db import session_scope
    from fluxus.models import Entry

    async with session_scope() as session:
        entry = await session.get(Entry, entry_id)
        return entry.read, entry.read_changed_at


async def test_stale_epoch_is_refused_on_writes_only(client: AsyncClient):
    """Constat 21 : une écriture par identifiant annoncée avec une époque périmée est refusée."""
    ids = await _entry_ids(client)
    body = {"entry_ids": ids[:1], "read": True}
    r = await client.post("/api/entries/mark-read", json=body, headers=STALE)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_epoch"
    assert (await _entry(ids[0]))[0] is False
    r = await client.patch(f"/api/entries/{ids[0]}", json={"read": True}, headers=STALE)
    assert r.status_code == 409
    cat = (await client.get("/api/categories")).json()[0]
    r = await client.put(f"/api/categories/{cat['id']}", json={"title": "Renommé"}, headers=STALE)
    assert r.status_code == 409
    assert (await client.get("/api/categories")).json()[0]["title"] == cat["title"]
    # Une lecture n'est pas concernée ; l'époque courante et l'absence d'en-tête (scripts) passent.
    assert (await client.get("/api/feeds", headers=STALE)).status_code == 200
    assert (await client.post("/api/entries/mark-read", json=body, headers={"X-Fluxus-Epoch": "0"})).status_code == 200
    assert (await client.post("/api/entries/mark-read", json={**body, "read": False})).status_code == 200


async def test_writes_are_refused_during_an_import(client: AsyncClient):
    """Constat 21 : pendant un import de sauvegarde (verrou exclusif), une écriture répond 503."""
    from sqlalchemy import func, select

    from fluxus.db import get_engine
    from fluxus.maintenance import MAINTENANCE_LOCK_KEY

    ids = await _entry_ids(client)
    async with get_engine().connect() as conn:
        await conn.execute(select(func.pg_advisory_lock(MAINTENANCE_LOCK_KEY)))
        try:
            r = await client.post("/api/entries/mark-read", json={"entry_ids": ids[:1], "read": True})
            assert r.status_code == 503
            assert (await client.get("/api/feeds")).status_code == 200
        finally:
            await conn.execute(select(func.pg_advisory_unlock(MAINTENANCE_LOCK_KEY)))
            await conn.commit()
    assert (await _entry(ids[0]))[0] is False


async def test_push_waits_for_a_concurrent_write(client: AsyncClient):
    """Constat 20 : un push plus ancien qu'un « marquer lu » encore en cours ne fait pas reculer son horloge."""
    from sqlalchemy import update

    from fluxus.db import get_engine
    from fluxus.models import Entry

    entry_id = (await _entry_ids(client))[1]
    now = datetime.now(UTC)
    async with get_engine().connect() as conn:
        # Écriture concurrente non encore validée : lu, à l'instant.
        await conn.execute(update(Entry).where(Entry.id == entry_id).values(read=True, read_changed_at=now, updated_at=now))
        change = {"entry_id": entry_id, "read": False, "at": (now - timedelta(minutes=5)).isoformat()}
        push = asyncio.create_task(client.post("/api/sync", json={"changes": [change]}))
        await asyncio.sleep(0.5)
        assert not push.done()  # le push attend le verrou de ligne au lieu de lire l'état d'avant
        await conn.commit()
    r = await push
    assert r.status_code == 200
    assert r.json()["conflicts"] == [{"entry_id": entry_id, "read": True, "starred": False}]
    read, changed_at = await _entry(entry_id)
    assert read is True and changed_at == now


async def test_opml_folder_goes_to_end_of_root(client: AsyncClient):
    """Constat 22 : un dossier créé par l'import OPML se place après le dernier élément de la racine."""
    opml = b"""<?xml version="1.0"?><opml version="2.0"><body>
    <outline text="Nouveau dossier"><outline type="rss" text="Import" xmlUrl="https://i1.example/rss"/></outline></body></opml>"""
    r = await client.post("/api/opml/import", files={"file": ("f.opml", opml, "text/xml")})
    assert r.status_code == 200 and r.json()["imported"] == 1
    cats = {c["title"]: c["position"] for c in (await client.get("/api/categories")).json()}
    assert cats["Nouveau dossier"] == 11  # après le flux sans dossier en position 10


async def test_feed_url_is_validated_on_update(client: AsyncClient):
    """Constat 23 : l'adresse d'un flux modifié passe par la garde SSRF."""
    feed = next(f for f in (await client.get("/api/feeds")).json() if f["title"] == "Tech A")
    for url in ("http://127.0.0.1:1/flux", "file:///etc/passwd", "http://100.64.0.1/flux"):
        r = await client.put(f"/api/feeds/{feed['id']}", json={"url": url})
        assert r.status_code == 400, url
    assert (await client.get(f"/api/feeds/{feed['id']}")).json()["url"] == feed["url"]
    other = next(f for f in (await client.get("/api/feeds")).json() if f["title"] == "Sans dossier")
    r = await client.put(f"/api/feeds/{feed['id']}", json={"url": other["url"]})
    assert r.status_code == 409


async def test_password_change_is_rate_limited(client: AsyncClient):
    """Constat 26 : le mot de passe actuel ne se devine pas à volonté depuis une session ouverte."""
    from fluxus.auth.security import login_limiter

    login_limiter._attempts.clear()
    try:
        codes = []
        for i in range(8):
            r = await client.post("/api/auth/password", json={"current_password": f"mauvais-mot-de-passe-{i}", "new_password": "nouveau-mot-de-passe"})
            codes.append(r.status_code)
        assert codes == [401] * 5 + [429] * 3
        assert (await client.get("/api/feeds")).status_code == 200  # le mot de passe n'a pas changé
    finally:
        login_limiter._attempts.clear()
