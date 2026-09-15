"""Correctifs de l'audit du 15/09/2026, partie interface côté serveur (constats 10, 12, 16).

Nécessite la base PostgreSQL de test (DATABASE_URL).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]


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
        cat = Category(title="Audit", position=1)
        session.add(cat)
        await session.flush()
        f1 = Feed(url="https://a1.example/rss", title="Audit A", category_id=cat.id)
        f2 = Feed(url="https://a2.example/rss", title="Audit B", category_id=None)
        session.add_all([f1, f2])
        await session.flush()
        for i in range(8):
            feed = f1 if i < 5 else f2
            session.add(
                Entry(
                    feed_id=feed.id,
                    guid=f"au-{i}",
                    url=f"https://x.example/{i}",
                    title=f"Intelligence artificielle {i}" if i % 2 == 0 else f"Cuisine {i}",
                    published_at=now - timedelta(hours=i),
                    hash=f"auh{i}",
                )
            )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _entries(client: AsyncClient, **params) -> list[dict]:
    r = await client.get("/api/entries", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _entry_clocks(entry_id: int) -> tuple[datetime | None, datetime | None]:
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.models import Entry

    async with session_scope() as s:
        row = (await s.execute(select(Entry.read_changed_at, Entry.starred_changed_at).where(Entry.id == entry_id))).one()
        return row[0], row[1]


async def test_patch_entry_sets_field_clocks(client: AsyncClient):
    entry = (await _entries(client))[0]
    assert await _entry_clocks(entry["id"]) == (None, None)
    r = await client.patch(f"/api/entries/{entry['id']}", json={"starred": True})
    assert r.status_code == 200 and r.json()["starred"] is True
    read_at, starred_at = await _entry_clocks(entry["id"])
    # seul le champ modifié avance : la sync ne doit pas rejeter une action « lu » à cause d'un favori
    assert read_at is None and starred_at is not None
    r = await client.patch(f"/api/entries/{entry['id']}", json={"read": True})
    read_at2, starred_at2 = await _entry_clocks(entry["id"])
    assert read_at2 is not None and starred_at2 == starred_at
    await client.patch(f"/api/entries/{entry['id']}", json={"read": False, "starred": False})


async def test_mark_read_smart_feed_scope_and_clock(client: AsyncClient):
    r = await client.post("/api/smart-feeds", json={"name": "IA", "keywords_any": ["intelligence"]})
    assert r.status_code == 201, r.text
    sf = r.json()
    matched = {e["id"] for e in await _entries(client) if "Intelligence" in e["title"]}
    assert sf["match_count"] == len(matched) == 4
    r = await client.post("/api/entries/mark-read", json={"smart_feed_id": sf["id"], "read": True})
    assert r.status_code == 200 and r.json()["updated"] == 4
    after = {e["id"]: e for e in await _entries(client)}
    assert all(after[i]["read"] for i in matched)
    assert all(not e["read"] for i, e in after.items() if i not in matched)
    read_at, _ = await _entry_clocks(next(iter(matched)))
    assert read_at is not None
    # retour en arrière : rien n'est laissé lu
    r = await client.post("/api/entries/mark-read", json={"smart_feed_id": sf["id"], "read": False})
    assert r.json()["updated"] == 4
    await client.delete(f"/api/smart-feeds/{sf['id']}")


async def test_mark_read_by_category_and_before(client: AsyncClient):
    cats = (await client.get("/api/categories")).json()
    cat = cats[0]
    entries = await _entries(client, category=cat["id"])
    assert len(entries) == 5
    # `before` borne le périmètre : seuls les articles publiés il y a plus de 2 h 30 sont marqués
    before = (datetime.now(UTC) - timedelta(hours=2, minutes=30)).isoformat()
    r = await client.post("/api/entries/mark-read", json={"category_id": cat["id"], "before": before, "read": True})
    assert r.status_code == 200 and r.json()["updated"] == 2
    r = await client.post("/api/entries/mark-read", json={"category_id": cat["id"], "read": True})
    assert r.json()["updated"] == 3
    r = await client.post("/api/entries/mark-read", json={"category_id": cat["id"], "read": False})
    assert r.json()["updated"] == 5
    r = await client.post("/api/entries/mark-read", json={"read": True})
    assert r.status_code == 400


async def test_dashboard_layout_versioning(client: AsyncClient):
    layout_a = '{"version": 1, "updatedAt": "2026-09-15T10:00:00Z", "widgets": [{"id": "a", "type": "stats", "width": 6}]}'
    layout_b = '{"version": 1, "updatedAt": "2026-09-15T10:01:00Z", "widgets": [{"id": "b", "type": "stats", "width": 3}]}'
    settings = (await client.get("/api/settings")).json()
    assert settings["dashboard_layout_version"] == 0
    # ancien client sans version : accepté tant que le serveur n'a jamais écrit
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": layout_a}})
    assert r.status_code == 200 and r.json()["dashboard_layout_version"] == 1
    # un autre appareil resté sur la version 0 ne doit pas écraser : 409 avec la disposition courante
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": layout_b}, "dashboard_layout_base_version": 0})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["dashboard_layout"] == layout_a and detail["dashboard_layout_version"] == 1
    assert "autre appareil" in detail["message"]
    assert (await client.get("/api/settings")).json()["values"]["dashboard_layout"] == layout_a
    # même sans version (ancien cache) une fois le serveur écrit : refusé
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": layout_b}})
    assert r.status_code == 409
    # avec la bonne base : accepté, version incrémentée
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": layout_b}, "dashboard_layout_base_version": 1})
    assert r.status_code == 200 and r.json()["dashboard_layout_version"] == 2
    assert r.json()["values"]["dashboard_layout"] == layout_b
    # la réinitialisation est versionnée aussi
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": ""}, "dashboard_layout_base_version": 1})
    assert r.status_code == 409
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": ""}, "dashboard_layout_base_version": 2})
    assert r.status_code == 200 and r.json()["dashboard_layout_version"] == 3
    # un autre réglage ne touche pas à la version
    r = await client.patch("/api/settings", json={"values": {"llm_language": "fr"}})
    assert r.status_code == 200 and r.json()["dashboard_layout_version"] == 3
