"""Flux intelligents : moteur de règles SQL, API, quotas. Nécessite la base PostgreSQL de test."""

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
        f1 = Feed(url="https://t.example/rss", title="Tech Feed", category_id=tech.id)
        f2 = Feed(url="https://a.example/rss", title="Actu Feed", category_id=actu.id)
        session.add_all([f1, f2])
        await session.flush()
        rows = [
            (f1, "Élève et intelligence artificielle à l'école", "Jean", "<p>Un article sur l'IA</p>"),
            (f1, "Nouveau GPU pour le machine learning", None, "<p>Benchmarks</p>"),
            (f1, "Recette de tarte aux pommes", "Marie", "<p>Cuisine</p>"),
            (f2, "L'intelligence artificielle au parlement", "Paul", "<p>Politique et IA</p>"),
            (f2, "Élection locale", None, None),
        ]
        for i, (feed, title, author, html) in enumerate(rows):
            session.add(
                Entry(
                    feed_id=feed.id,
                    guid=f"sf-{i}",
                    url=f"https://x.example/{i}",
                    title=title,
                    author=author,
                    summary_html=html,
                    published_at=now - timedelta(hours=i),
                    hash=f"sfh{i}",
                )
            )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _titles(client: AsyncClient, definition: dict) -> list[str]:
    r = await client.post("/api/smart-feeds/preview", json=definition)
    assert r.status_code == 200, r.text
    return sorted(s["title"] for s in r.json()["samples"])


async def test_keywords_ignore_case_and_accents(client: AsyncClient):
    titles = await _titles(client, {"keywords_any": ["INTELLIGENCE ARTIFICIELLE"]})
    assert titles == ["L'intelligence artificielle au parlement", "Élève et intelligence artificielle à l'école"]
    # « eleve » sans accent trouve « Élève »
    assert await _titles(client, {"keywords_any": ["eleve"]}) == ["Élève et intelligence artificielle à l'école"]


async def test_keywords_none_does_not_exclude_null_author(client: AsyncClient):
    # « aucun de : Jean » : les entrées sans auteur (NULL) doivent rester.
    titles = await _titles(client, {"keywords_any": ["intelligence", "gpu", "élection"], "keywords_none": ["Jean"]})
    assert "Élève et intelligence artificielle à l'école" not in titles
    assert "Nouveau GPU pour le machine learning" in titles
    assert "Élection locale" in titles


async def test_keywords_all_and_scope(client: AsyncClient):
    tech_id = (await client.get("/api/categories")).json()[0]["id"]
    titles = await _titles(client, {"keywords_all": ["intelligence", "école"], "scope_kind": "category", "scope_id": tech_id})
    assert titles == ["Élève et intelligence artificielle à l'école"]
    titles = await _titles(client, {"keywords_any": ["intelligence"], "scope_kind": "category", "scope_id": tech_id})
    assert titles == ["Élève et intelligence artificielle à l'école"]


async def test_like_wildcards_are_escaped(client: AsyncClient):
    assert await _titles(client, {"keywords_any": ["%"]}) == []
    assert await _titles(client, {"keywords_any": ["_"]}) == []


async def test_regex_mode_and_validation(client: AsyncClient):
    titles = await _titles(client, {"mode": "regex", "regex": r"\m(gpu|parlement)\M"})
    assert titles == ["L'intelligence artificielle au parlement", "Nouveau GPU pour le machine learning"]
    # Syntaxe refusée par PostgreSQL (groupe nommé Python) → 422 avec le message serveur
    r = await client.post("/api/smart-feeds/preview", json={"mode": "regex", "regex": "(?P<n>abc)"})
    assert r.status_code == 422 and "refusée" in r.json()["detail"]
    r = await client.post("/api/smart-feeds/preview", json={"mode": "regex", "regex": r"(a)\1"})
    assert r.status_code == 422 and "références arrière" in r.json()["detail"]
    r = await client.post("/api/smart-feeds/preview", json={"mode": "keywords"})
    assert r.status_code == 422


async def test_content_field_only_matches_extracted_text(client: AsyncClient):
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.models import Entry

    async with session_scope() as session:
        e = (await session.execute(select(Entry).where(Entry.guid == "sf-2"))).scalar_one()
        e.content_text = "Une longue tarte avec de la cannelle et des noisettes"
    assert await _titles(client, {"keywords_any": ["noisettes"]}) == []
    assert await _titles(client, {"keywords_any": ["noisettes"], "fields": {"content": True}}) == ["Recette de tarte aux pommes"]


async def test_crud_apply_and_sync_propagation(client: AsyncClient):
    r = await client.post("/api/smart-feeds", json={"name": "IA", "keywords_any": ["intelligence artificielle"]})
    assert r.status_code == 201, r.text
    sf = r.json()
    assert sf["match_count"] == 2 and sf["unread_count"] == 2

    # smart_feed_ids exposé dans /entries et /sync
    items = (await client.get("/api/entries")).json()["items"]
    tagged = {i["title"] for i in items if sf["id"] in i["smart_feed_ids"]}
    assert tagged == {"L'intelligence artificielle au parlement", "Élève et intelligence artificielle à l'école"}
    sync = (await client.get("/api/sync")).json()
    assert [s["id"] for s in sync["smart_feeds"]] == [sf["id"]]
    assert sum(1 for e in sync["entries"] if sf["id"] in e["smart_feed_ids"]) == 2

    # Modification rétroactive : ajout d'un mot → une entrée entre, updated_at bumpé → visible en delta
    since = datetime.now(UTC).isoformat()
    r = await client.put(f"/api/smart-feeds/{sf['id']}", json={"name": "IA", "keywords_any": ["intelligence artificielle", "gpu"]})
    assert r.status_code == 200 and r.json()["match_count"] == 3
    delta = (await client.get("/api/sync", params={"since": since})).json()["entries"]
    assert {e["title"] for e in delta} >= {"Nouveau GPU pour le machine learning"}

    # Retrait d'un mot → l'entrée sort et est aussi propagée
    since = datetime.now(UTC).isoformat()
    r = await client.put(f"/api/smart-feeds/{sf['id']}", json={"name": "IA", "keywords_any": ["intelligence artificielle"]})
    assert r.json()["match_count"] == 2
    delta = (await client.get("/api/sync", params={"since": since})).json()["entries"]
    gpu = next(e for e in delta if e["title"].startswith("Nouveau GPU"))
    assert sf["id"] not in gpu["smart_feed_ids"]

    # Doublon de nom refusé ; nom seul modifié sans ré-application
    r = await client.post("/api/smart-feeds", json={"name": "ia", "keywords_any": ["x"]})
    assert r.status_code == 409


async def test_new_entries_are_evaluated_at_insert(client: AsyncClient):
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.fetcher.parse import ParsedEntry, ParsedFeed
    from fluxus.fetcher.service import _insert_new_entries
    from fluxus.models import Feed

    async with session_scope() as session:
        feed = (await session.execute(select(Feed).where(Feed.title == "Actu Feed"))).scalar_one()
        parsed = ParsedFeed(
            title="Actu Feed",
            site_url=None,
            description=None,
            icon_url=None,
            is_youtube=False,
            entries=[
                ParsedEntry(guid="new-ia", url="https://a.example/new", title="Sommet sur l'intelligence artificielle", author=None, published_at=datetime.now(UTC), summary_html=None, hash="newh"),
            ],
        )
        assert await _insert_new_entries(session, feed, parsed) == 1
    sf_id = (await client.get("/api/smart-feeds")).json()["items"][0]["id"]
    items = (await client.get("/api/entries", params={"limit": 3})).json()["items"]
    new = next(i for i in items if i["title"].startswith("Sommet"))
    assert sf_id in new["smart_feed_ids"]


async def test_category_change_reevaluates(client: AsyncClient):
    cats = (await client.get("/api/categories")).json()
    tech, actu = cats[0], cats[1]
    r = await client.post("/api/smart-feeds", json={"name": "IA Tech", "keywords_any": ["intelligence"], "scope_kind": "category", "scope_id": tech["id"]})
    sf = r.json()
    assert sf["match_count"] == 1
    feeds = (await client.get("/api/feeds")).json()
    actu_feed = next(f for f in feeds if f["title"] == "Actu Feed")
    r = await client.put(f"/api/feeds/{actu_feed['id']}", json={"category_id": tech["id"]})
    assert r.status_code == 200
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 3
    r = await client.put(f"/api/feeds/{actu_feed['id']}", json={"category_id": actu["id"]})
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 1


async def test_auto_summary_quota_and_budget(client: AsyncClient, monkeypatch):
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.models import Job, SmartFeed
    from fluxus.smartfeeds import jobs as sf_jobs
    from fluxus.smartfeeds import service
    from fluxus.summarize import service as sum_service
    from fluxus.summarize.providers import StreamResult, Summarizer, Usage

    class Fake(Summarizer):
        name = "fake"

        def __init__(self):
            self.model = "fake-auto"
            self.effort = "low"

        def stream(self, system, user, max_tokens=4000):
            usage = Usage()

            async def gen():
                yield "Résumé auto."
                usage.input_tokens, usage.output_tokens = 1000, 100

            return StreamResult(gen(), usage)

    monkeypatch.setattr(sum_service, "build_summarizer", lambda settings: Fake())
    # Certaines entrées du jeu de test n'ont aucun texte : on neutralise le seuil pour tester les quotas seuls.
    monkeypatch.setattr(sum_service, "MIN_SOURCE_CHARS", 0)

    r = await client.post("/api/smart-feeds", json={"name": "Auto", "keywords_any": ["intelligence", "gpu", "élection", "tarte"], "auto_summary": True, "summary_daily_cap": 2})
    sf = r.json()
    assert sf["match_count"] >= 4

    # summarize-all : plafonné par le budget (3 000 tokens estimés par résumé) → budget 7 000 → 2 jobs
    await client.patch("/api/settings", json={"values": {"llm_auto_daily_token_budget": "7000"}})
    r = await client.post(f"/api/smart-feeds/{sf['id']}/summarize-all", json={"unread_only": False})
    body = r.json()
    assert body["queued"] == 2 and body["skipped_budget"] >= 2

    # Exécution des jobs par le handler : les résumés portent origin=auto et le smart_feed_id
    async with session_scope() as session:
        jobs = (await session.execute(select(Job).where(Job.type == "summarize_entry"))).scalars().all()
        assert len(jobs) == 2
        for job in jobs:
            await sf_jobs.summarize_entry_job(session, job)
    r = await client.get("/api/smart-feeds")
    listing = r.json()
    assert listing["tokens_today"] == 2200
    me = next(i for i in listing["items"] if i["id"] == sf["id"])
    assert me["auto_today"] >= 2

    # Planification automatique : quota 2/jour déjà consommé → rien de nouveau
    async with session_scope() as session:
        sf_row = await session.get(SmartFeed, sf["id"])
        queued = await service.schedule_auto_summaries(session, [(sf_row, {1, 2, 3})])
        assert queued == 0
        # Budget épuisé (7 000 ≥ … non : 2 200 utilisés) mais quota plein ; on relève le quota → budget limite
        sf_row.summary_daily_cap = 50
        await session.flush()
        await client.patch("/api/settings", json={"values": {"llm_auto_daily_token_budget": "2000"}})
    async with session_scope() as session:
        sf_row = await session.get(SmartFeed, sf["id"])
        assert await service.schedule_auto_summaries(session, [(sf_row, {1, 2, 3})]) == 0


async def test_delete_smart_feed_propagates(client: AsyncClient):
    sf = (await client.get("/api/smart-feeds")).json()["items"][0]
    since = datetime.now(UTC).isoformat()
    r = await client.delete(f"/api/smart-feeds/{sf['id']}")
    assert r.status_code == 204
    delta = (await client.get("/api/sync", params={"since": since})).json()
    assert delta["entries"] and all(sf["id"] not in e["smart_feed_ids"] for e in delta["entries"])
