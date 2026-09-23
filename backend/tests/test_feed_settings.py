"""Réglages par flux : présentation, actions groupées, intervalle fixe, résumés automatiques.

Nécessite la base PostgreSQL de test.
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

LONG_TEXT = "Texte suffisamment long pour un résumé. " * 20


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
        session.add(tech)
        await session.flush()
        hn = Feed(url="https://hn.example/rss", title="Hacker News", category_id=tech.id)
        korben = Feed(url="https://korben.example/rss", title="Korben", category_id=tech.id)
        solo = Feed(url="https://solo.example/rss", title="Solo")
        session.add_all([hn, korben, solo])
        await session.flush()
        for i in range(4):
            session.add(
                Entry(
                    feed_id=korben.id,
                    guid=f"k-{i}",
                    url=f"https://korben.example/{i}",
                    title=f"Article Korben {i}",
                    content_text=LONG_TEXT,
                    published_at=now - timedelta(days=i * 5),
                    hash=f"kh{i}",
                )
            )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _feeds(client: AsyncClient) -> dict[str, dict]:
    return {f["title"]: f for f in (await client.get("/api/feeds")).json()}


async def test_view_mode_per_feed_category_and_default(client: AsyncClient):
    feeds = await _feeds(client)
    hn, korben = feeds["Hacker News"], feeds["Korben"]
    assert hn["view_mode"] is None

    r = await client.put("/api/view-mode", json={"kind": "feed", "id": hn["id"], "view_mode": "list"})
    assert r.status_code == 204
    r = await client.put(f"/api/feeds/{korben['id']}", json={"view_mode": "magazine"})
    assert r.json()["view_mode"] == "magazine"
    cat = (await client.get("/api/categories")).json()[0]
    r = await client.put("/api/view-mode", json={"kind": "category", "id": cat["id"], "view_mode": "cards"})
    assert r.status_code == 204
    r = await client.put("/api/view-mode", json={"kind": "default", "view_mode": "grid"})
    assert r.status_code == 204

    pull = (await client.get("/api/sync")).json()
    assert pull["default_view_mode"] == "grid"
    assert pull["categories"][0]["view_mode"] == "cards"
    by_title = {f["title"]: f for f in pull["feeds"]}
    assert by_title["Hacker News"]["view_mode"] == "list"
    assert by_title["Korben"]["view_mode"] == "magazine"

    # « inherit » efface la présentation propre ; le défaut ne peut pas hériter.
    r = await client.put("/api/view-mode", json={"kind": "feed", "id": hn["id"], "view_mode": "inherit"})
    assert r.status_code == 204
    assert (await _feeds(client))["Hacker News"]["view_mode"] is None
    r = await client.put("/api/view-mode", json={"kind": "default", "view_mode": "inherit"})
    assert r.status_code == 422
    r = await client.put("/api/view-mode", json={"kind": "feed", "id": 99999, "view_mode": "list"})
    assert r.status_code == 404
    r = await client.patch("/api/settings", json={"values": {"default_view_mode": "mosaïque"}})
    assert r.status_code == 400


async def test_fixed_interval(client: AsyncClient):
    from fluxus.db import session_scope
    from fluxus.fetcher.service import _next_interval
    from fluxus.models import Feed

    solo = (await _feeds(client))["Solo"]
    r = await client.put(f"/api/feeds/{solo['id']}", json={"fetch_interval_fixed_min": 180})
    body = r.json()
    assert body["fetch_interval_fixed_min"] == 180 and body["fetch_interval_min"] == 180
    # La prochaine récupération n'est jamais repoussée par le réglage.
    assert datetime.fromisoformat(body["next_fetch_at"]) <= datetime.now(UTC) + timedelta(minutes=181)
    async with session_scope() as session:
        feed = await session.get(Feed, solo["id"])
        assert _next_interval(feed, None, 0) == 180
        assert _next_interval(feed, object(), 10) == 180  # type: ignore[arg-type]

    # Retour à l'adaptatif depuis un intervalle long : l'intervalle repart du défaut et la prochaine
    # récupération est ramenée d'autant.
    r = await client.put(f"/api/feeds/{solo['id']}", json={"fetch_interval_fixed_min": 1440})
    assert datetime.fromisoformat(r.json()["next_fetch_at"]) <= datetime.now(UTC) + timedelta(minutes=181)
    async with session_scope() as session:
        feed = await session.get(Feed, solo["id"])
        feed.next_fetch_at = datetime.now(UTC) + timedelta(hours=20)
    r = await client.put(f"/api/feeds/{solo['id']}", json={"fetch_interval_fixed_min": 0})
    body = r.json()
    assert body["fetch_interval_fixed_min"] is None and body["fetch_interval_min"] == 30
    assert datetime.fromisoformat(body["next_fetch_at"]) <= datetime.now(UTC) + timedelta(minutes=31)
    r = await client.put(f"/api/feeds/{solo['id']}", json={"fetch_interval_fixed_min": 2})
    assert r.status_code == 422


async def test_bulk_update_and_stats(client: AsyncClient):
    feeds = await _feeds(client)
    ids = [feeds["Hacker News"]["id"], feeds["Solo"]["id"]]
    r = await client.post("/api/feeds/bulk", json={"feed_ids": ids, "changes": {"disabled": True, "extraction_mode": "http", "view_mode": "titles"}})
    assert r.status_code == 200, r.text
    out = r.json()["feeds"]
    assert {f["id"] for f in out} == set(ids)
    assert all(f["disabled"] and f["extraction_mode"] == "http" and f["view_mode"] == "titles" for f in out)
    assert (await _feeds(client))["Korben"]["disabled"] is False

    # Tout ou rien : un identifiant inconnu n'applique rien.
    r = await client.post("/api/feeds/bulk", json={"feed_ids": [*ids, 99999], "changes": {"disabled": False}})
    assert r.status_code == 404
    assert all(f["disabled"] for f in (await _feeds(client)).values() if f["id"] in ids)

    # Déplacement groupé vers la racine.
    r = await client.post("/api/feeds/bulk", json={"feed_ids": ids, "changes": {"clear_category": True, "disabled": False}})
    assert all(f["category_id"] is None and not f["disabled"] for f in r.json()["feeds"])

    # Déplacement groupé vers un dossier : les flux arrivent en tête, dans leur ordre relatif d'origine.
    cat = (await client.post("/api/categories", json={"title": "Destination"})).json()
    before = sorted((f for f in (await _feeds(client)).values() if f["category_id"] is None), key=lambda f: f["position"])
    r = await client.post("/api/feeds/bulk", json={"feed_ids": [f["id"] for f in before], "changes": {"category_id": cat["id"]}})
    after = sorted(r.json()["feeds"], key=lambda f: f["position"])
    assert [f["id"] for f in after] == [f["id"] for f in before]
    await client.post("/api/feeds/bulk", json={"feed_ids": [f["id"] for f in before], "changes": {"clear_category": True}})

    stats = {s["feed_id"]: s for s in (await client.get("/api/feeds/stats")).json()}
    korben = stats[feeds["Korben"]["id"]]
    assert korben["entries_7d"] == 2 and korben["entries_30d"] == 4 and korben["last_entry_at"] is not None
    assert stats[feeds["Solo"]["id"]]["entries_30d"] == 0


async def test_feed_auto_summaries(client: AsyncClient, monkeypatch):
    from sqlalchemy import delete, select

    from fluxus.db import session_scope
    from fluxus.models import Entry, Feed, Job, LlmUsage, Summary
    from fluxus.smartfeeds.jobs import summarize_entry_job
    from fluxus.summarize import auto
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
                usage.input_tokens, usage.output_tokens = 500, 50

            return StreamResult(gen(), usage)

    monkeypatch.setattr(sum_service, "build_summarizer", lambda settings: Fake())
    await client.patch("/api/settings", json={"values": {"llm_auto_daily_token_budget": "100000"}})
    korben = (await _feeds(client))["Korben"]
    async with session_scope() as session:
        entry_ids = set((await session.execute(select(Entry.id).where(Entry.feed_id == korben["id"]))).scalars())

    # Option inactive : rien n'est planifié.
    async with session_scope() as session:
        feed = await session.get(Feed, korben["id"])
        assert await auto.schedule_feed_auto_summaries(session, feed, entry_ids) == 0

    r = await client.put(f"/api/feeds/{korben['id']}", json={"auto_summary": True, "summary_daily_cap": 2})
    assert r.json()["auto_summary"] is True and r.json()["summary_daily_cap"] == 2
    async with session_scope() as session:
        feed = await session.get(Feed, korben["id"])
        assert await auto.schedule_feed_auto_summaries(session, feed, entry_ids) == 2
        # Quota compté avec les jobs en attente : une seconde passe n'ajoute rien.
        assert await auto.schedule_feed_auto_summaries(session, feed, entry_ids) == 0

    async with session_scope() as session:
        jobs = (await session.execute(select(Job).where(Job.type == "summarize_entry"))).scalars().all()
        assert len(jobs) == 2 and all(j.payload["feed_id"] == korben["id"] for j in jobs)
        for job in jobs:
            job.status = "running"
            await summarize_entry_job(session, job)
            job.status = "done"
    async with session_scope() as session:
        rows = (await session.execute(select(Summary).where(Summary.entry_id.in_(entry_ids)))).scalars().all()
        assert len(rows) == 2 and all(s.origin == "auto" for s in rows)
        usage = (await session.execute(select(LlmUsage).where(LlmUsage.origin == "auto"))).scalars().all()
        assert len(usage) == 2 and all(u.status == "done" for u in usage)
    stats = {s["feed_id"]: s for s in (await client.get("/api/feeds/stats")).json()}
    assert stats[korben["id"]]["auto_summaries_today"] == 2

    # Les résumés d'un flux intelligent ne consomment pas le quota du flux.
    async with session_scope() as session:
        from fluxus.models import SmartFeed

        sf = SmartFeed(name="Autre quota", keywords_any=["korben"], fields={"title": True})
        session.add(sf)
        await session.flush()
        for s_row in (await session.execute(select(Summary).where(Summary.entry_id.in_(entry_ids)))).scalars():
            s_row.smart_feed_id = sf.id
        await session.flush()
        assert await auto.feed_auto_count_today(session, korben["id"]) == 0
        for s_row in (await session.execute(select(Summary).where(Summary.entry_id.in_(entry_ids)))).scalars():
            s_row.smart_feed_id = None
        await session.delete(sf)

    # Désactivation : un job encore en file est abandonné sans appel.
    async with session_scope() as session:
        await session.execute(delete(Job))
        await session.execute(delete(Summary))
        feed = await session.get(Feed, korben["id"])
        feed.summary_daily_cap = 10
    async with session_scope() as session:
        feed = await session.get(Feed, korben["id"])
        assert await auto.schedule_feed_auto_summaries(session, feed, entry_ids) == 4
    await client.put(f"/api/feeds/{korben['id']}", json={"auto_summary": False})
    async with session_scope() as session:
        before = len((await session.execute(select(LlmUsage))).scalars().all())
        for job in (await session.execute(select(Job).where(Job.type == "summarize_entry"))).scalars().all():
            await summarize_entry_job(session, job)
        assert len((await session.execute(select(LlmUsage))).scalars().all()) == before
        assert not (await session.execute(select(Summary))).scalars().all()
        await session.execute(delete(Job))

    # Lot manuel « Résumer les non résumés » : autorisé même sans l'option, borné par le budget.
    await client.patch("/api/settings", json={"values": {"llm_auto_daily_token_budget": "7000"}})
    r = await client.post(f"/api/feeds/{korben['id']}/summarize-all", json={"unread_only": False})
    body = r.json()
    assert r.status_code == 200 and body["queued"] == 1 and body["skipped_budget"] == 3
