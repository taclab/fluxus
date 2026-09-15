"""Ordre manuel de la barre latérale : position des flux, route de réorganisation. Nécessite la base PostgreSQL de test."""

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
        tech = Category(title="Tech", position=1)
        actu = Category(title="Actu", position=2)
        session.add_all([tech, actu])
        await session.flush()
        feeds = [
            Feed(url="https://t1.example/rss", title="Tech A", category_id=tech.id, position=1),
            Feed(url="https://t2.example/rss", title="Tech B", category_id=tech.id, position=2),
            Feed(url="https://t3.example/rss", title="Tech C", category_id=tech.id, position=3),
            Feed(url="https://a1.example/rss", title="Actu A", category_id=actu.id, position=1),
            Feed(url="https://n1.example/rss", title="Sans dossier", category_id=None, position=1),
        ]
        session.add_all(feeds)
        await session.flush()
        for i, feed in enumerate(feeds):
            session.add(
                Entry(
                    feed_id=feed.id,
                    guid=f"ro-{i}",
                    url=f"https://x.example/{i}",
                    title=f"Intelligence artificielle {i}",
                    published_at=now - timedelta(hours=i),
                    hash=f"roh{i}",
                )
            )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _feeds(client: AsyncClient) -> dict[str, dict]:
    return {f["title"]: f for f in (await client.get("/api/feeds")).json()}


async def test_feeds_listed_by_position(client: AsyncClient):
    titles = [f["title"] for f in (await client.get("/api/feeds")).json() if f["category_id"] is not None]
    assert titles == ["Tech A", "Tech B", "Tech C", "Actu A"]
    assert all("position" in f for f in (await client.get("/api/feeds")).json())


async def test_category_change_puts_feed_first(client: AsyncClient):
    feeds = await _feeds(client)
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    r = await client.put(f"/api/feeds/{feeds['Actu A']['id']}", json={"category_id": cats["Tech"]["id"]})
    assert r.status_code == 200
    assert r.json()["position"] < min(feeds[t]["position"] for t in ("Tech A", "Tech B", "Tech C"))
    titles = [f["title"] for f in (await client.get("/api/feeds")).json() if f["category_id"] == cats["Tech"]["id"]]
    assert titles == ["Actu A", "Tech A", "Tech B", "Tech C"]
    # retour dans son dossier d'origine, désormais vide : position 0 (le flux lui-même ne compte pas)
    r = await client.put(f"/api/feeds/{feeds['Actu A']['id']}", json={"category_id": cats["Actu"]["id"]})
    assert r.json()["position"] == 0


async def test_opml_import_keeps_file_order_at_top(client: AsyncClient):
    opml = b"""<?xml version="1.0"?><opml version="2.0"><body>
    <outline text="Tech"><outline type="rss" text="Import 1" xmlUrl="https://i1.example/rss"/>
    <outline type="rss" text="Import 2" xmlUrl="https://i2.example/rss"/></outline></body></opml>"""
    r = await client.post("/api/opml/import", files={"file": ("f.opml", opml, "text/xml")})
    assert r.status_code == 200 and r.json()["imported"] == 2
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    titles = [f["title"] for f in (await client.get("/api/feeds")).json() if f["category_id"] == cats["Tech"]["id"]]
    assert titles == ["Import 1", "Import 2", "Tech A", "Tech B", "Tech C"]
    # l'export suit le même ordre
    xml = (await client.get("/api/opml/export")).text
    assert xml.index("Import 1") < xml.index("Import 2") < xml.index("Tech A")


async def test_reorder_all_kinds(client: AsyncClient):
    feeds = await _feeds(client)
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    r = await client.post("/api/smart-feeds", json={"name": "S1", "keywords_any": ["x"]})
    s1 = r.json()
    r = await client.post("/api/smart-feeds", json={"name": "S2", "keywords_any": ["y"]})
    s2 = r.json()
    body = {
        "categories": [{"id": cats["Actu"]["id"], "position": 0}, {"id": cats["Tech"]["id"], "position": 1}],
        "feeds": [
            {"id": feeds["Tech C"]["id"], "position": 0, "category_id": cats["Tech"]["id"]},
            {"id": feeds["Tech A"]["id"], "position": 1, "category_id": cats["Tech"]["id"]},
            {"id": feeds["Tech B"]["id"], "position": 2, "category_id": cats["Tech"]["id"]},
            {"id": feeds["Import 1"]["id"], "position": 3, "category_id": cats["Tech"]["id"]},
            {"id": feeds["Import 2"]["id"], "position": 4, "category_id": cats["Tech"]["id"]},
        ],
        "smart_feeds": [{"id": s2["id"], "position": 0}, {"id": s1["id"], "position": 1}],
    }
    r = await client.post("/api/subscriptions/reorder", json=body)
    assert r.status_code == 204, r.text
    assert [c["title"] for c in (await client.get("/api/categories")).json()][:2] == ["Actu", "Tech"]
    tech_titles = [f["title"] for f in (await client.get("/api/feeds")).json() if f["category_id"] == cats["Tech"]["id"]]
    assert tech_titles[:3] == ["Tech C", "Tech A", "Tech B"]
    assert [s["name"] for s in (await client.get("/api/smart-feeds")).json()["items"]] == ["S2", "S1"]
    # la sync expose la position des flux
    pull = (await client.get("/api/sync")).json()
    assert {f["title"]: f["position"] for f in pull["feeds"]}["Tech C"] == 0


async def test_reorder_moves_feed_between_categories_and_reevaluates(client: AsyncClient):
    feeds = await _feeds(client)
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    r = await client.post("/api/smart-feeds", json={"name": "IA Actu", "keywords_any": ["intelligence"], "scope_kind": "category", "scope_id": cats["Actu"]["id"]})
    sf = r.json()
    assert sf["match_count"] == 1
    # « Sans dossier » entre dans Actu, en tête ; « Actu A » sort vers « sans dossier »
    body = {
        "feeds": [
            {"id": feeds["Sans dossier"]["id"], "position": 0, "category_id": cats["Actu"]["id"]},
            {"id": feeds["Actu A"]["id"], "position": 0, "category_id": None},
        ]
    }
    r = await client.post("/api/subscriptions/reorder", json=body)
    assert r.status_code == 204
    feeds = await _feeds(client)
    assert feeds["Sans dossier"]["category_id"] == cats["Actu"]["id"]
    assert feeds["Actu A"]["category_id"] is None
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 1
    # deux flux dans Actu → deux correspondances
    r = await client.post("/api/subscriptions/reorder", json={"feeds": [{"id": feeds["Actu A"]["id"], "position": 1, "category_id": cats["Actu"]["id"]}]})
    assert r.status_code == 204
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 2


async def test_reorder_unknown_id_changes_nothing(client: AsyncClient):
    before = await _feeds(client)
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    body = {"feeds": [{"id": before["Tech A"]["id"], "position": 99, "category_id": cats["Tech"]["id"]}, {"id": 999999, "position": 0, "category_id": None}]}
    r = await client.post("/api/subscriptions/reorder", json=body)
    assert r.status_code == 404 and r.json()["detail"] == "Flux introuvable"
    assert (await _feeds(client))["Tech A"]["position"] == before["Tech A"]["position"]
    r = await client.post("/api/subscriptions/reorder", json={"feeds": [{"id": before["Tech A"]["id"], "position": 0, "category_id": 999999}]})
    assert r.status_code == 404 and r.json()["detail"] == "Catégorie introuvable"
    r = await client.post("/api/subscriptions/reorder", json={"categories": [{"id": 999999, "position": 0}]})
    assert r.status_code == 404
    r = await client.post("/api/subscriptions/reorder", json={"smart_feeds": [{"id": 999999, "position": 0}]})
    assert r.status_code == 404
