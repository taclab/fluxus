"""Correctifs de l'audit du 15/09/2026 (constats 6, 7, 8, 9, 13, 14, 17) : chaque test échouait avant.

Nécessite la base PostgreSQL de test (DATABASE_URL). Aucun appel LLM : fournisseur simulé.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]

LONG_TEXT = "Un article synthétique sur l'intelligence artificielle, suffisamment long pour être résumé. " * 6


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
        tech = Category(title="Tech", position=0)
        actu = Category(title="Actu", position=1)
        session.add_all([tech, actu])
        await session.flush()
        f1 = Feed(url="https://t.example/rss", title="Tech Feed", category_id=tech.id, extraction_mode="none")
        f2 = Feed(url="https://a.example/rss", title="Actu Feed", category_id=actu.id, extraction_mode="none")
        session.add_all([f1, f2])
        await session.flush()
        for i, (feed, title) in enumerate([(f1, "Intelligence artificielle à l'école"), (f1, "GPU et intelligence"), (f2, "L'intelligence au parlement"), (f2, "Élection locale")]):
            session.add(
                Entry(
                    feed_id=feed.id,
                    guid=f"au-{i}",
                    url=f"https://x.example/{i}",
                    title=title,
                    summary_html=f"<p>{title}</p>",
                    content_text=LONG_TEXT,
                    extraction_status="done",
                    published_at=now - timedelta(hours=i),
                    hash=f"auh{i}",
                )
            )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


class FakeSummarizer:
    """Fournisseur simulé : 90 tokens d'entrée + 10 de sortie ; mémorise les plafonds demandés."""

    name = "fake"
    model = "fake-auto"
    calls: list[int] = []
    fail = False

    def stream(self, system, user, max_tokens=4000):
        from fluxus.summarize.providers import StreamResult, SummarizerError, Usage

        FakeSummarizer.calls.append(max_tokens)
        usage = Usage()

        async def gen():
            if FakeSummarizer.fail:
                usage.input_tokens = 90
                raise SummarizerError("Fournisseur indisponible (simulé)")
            yield "Résumé simulé."
            usage.input_tokens, usage.output_tokens = 90, 10

        return StreamResult(gen(), usage)


@pytest.fixture
def fake_provider(monkeypatch):
    from fluxus.summarize import service as sum_service

    FakeSummarizer.calls = []
    FakeSummarizer.fail = False
    monkeypatch.setattr(sum_service, "build_summarizer", lambda settings: FakeSummarizer())
    return FakeSummarizer


async def _entry_id(client: AsyncClient, title_part: str) -> int:
    items = (await client.get("/api/entries?limit=50")).json()["items"]
    return next(e["id"] for e in items if title_part in e["title"])


async def _set_budget(client: AsyncClient, value: int) -> None:
    r = await client.patch("/api/settings", json={"values": {"llm_auto_daily_token_budget": str(value)}})
    assert r.status_code == 200, r.text


# --- 7 : le budget est un plafond -----------------------------------------------------------------


async def test_budget_is_a_hard_cap(client: AsyncClient, fake_provider):
    from fluxus.db import session_scope
    from fluxus.models import Job, LlmUsage, Summary
    from fluxus.smartfeeds import service
    from fluxus.smartfeeds.jobs import summarize_entry_job

    r = await client.post("/api/smart-feeds", json={"name": "Budget", "keywords_any": ["intelligence"], "auto_summary": True, "summary_daily_cap": 10})
    sf = r.json()
    assert sf["match_count"] == 3
    entry_id = await _entry_id(client, "école")

    # 1 token disponible : le fournisseur n'est jamais appelé, aucun résumé, aucune consommation.
    await _set_budget(client, 1)
    async with session_scope() as session:
        await summarize_entry_job(session, Job(payload={"entry_id": entry_id, "smart_feed_id": sf["id"]}))
        assert fake_provider.calls == []
        assert (await session.execute(select(func.count()).select_from(Summary))).scalar_one() == 0
        assert await service.auto_tokens_today(session) == 0

    # Budget serré : la sortie est bornée au solde restant (entrée estimée + plancher de sortie).
    input_est = service.estimate_input_tokens(LONG_TEXT)
    await _set_budget(client, input_est + 500)
    async with session_scope() as session:
        await summarize_entry_job(session, Job(payload={"entry_id": entry_id, "smart_feed_id": sf["id"]}))
        assert fake_provider.calls == [500]
        rows = (await session.execute(select(LlmUsage))).scalars().all()
        assert [(u.origin, u.status, u.tokens) for u in rows] == [("auto", "done", 100)]
        assert await service.auto_tokens_today(session) == 100
    listing = (await client.get("/api/smart-feeds")).json()
    assert listing["tokens_today"] == 100

    # Solde insuffisant pour le plancher de sortie (256) → abandon avant l'appel.
    other = await _entry_id(client, "GPU")
    await _set_budget(client, 100 + input_est + 100)
    async with session_scope() as session:
        await summarize_entry_job(session, Job(payload={"entry_id": other, "smart_feed_id": sf["id"]}))
        assert fake_provider.calls == [500]

    # Un appel échoué après consommation est compté (tokens connus) et n'a pas produit de résumé.
    await _set_budget(client, 100_000)
    fake_provider.fail = True
    async with session_scope() as session:
        with pytest.raises(Exception, match="simulé"):
            await summarize_entry_job(session, Job(payload={"entry_id": other, "smart_feed_id": sf["id"]}))
    async with session_scope() as session:
        failed = (await session.execute(select(LlmUsage).where(LlmUsage.status == "failed"))).scalars().all()
        assert len(failed) == 1 and failed[0].tokens == 90
        assert await service.auto_tokens_today(session) == 190
        assert (await session.execute(select(func.count()).select_from(LlmUsage).where(LlmUsage.status == "reserved"))).scalar_one() == 0

    # Un résumé manuel est journalisé (origin manual) mais n'entre pas dans le budget automatique.
    fake_provider.fail = False
    await _set_budget(client, 0)
    r = await client.post(f"/api/entries/{other}/summarize")
    assert r.status_code == 200
    async with session_scope() as session:
        manual = (await session.execute(select(LlmUsage).where(LlmUsage.origin == "manual"))).scalars().all()
        assert [(u.status, u.tokens) for u in manual] == [("done", 100)]
        assert await service.auto_tokens_today(session) == 190
    await client.delete(f"/api/smart-feeds/{sf['id']}")


# --- 6 : jobs devenus sans objet ---------------------------------------------------------------------


async def test_job_abandons_when_rule_is_gone_disabled_or_no_longer_matches(client: AsyncClient, fake_provider):
    from fluxus.db import session_scope
    from fluxus.models import Job
    from fluxus.smartfeeds.jobs import summarize_entry_job

    await _set_budget(client, 100_000)
    r = await client.post("/api/smart-feeds", json={"name": "Désactivé", "keywords_any": ["intelligence"], "auto_summary": False})
    sf = r.json()
    matched = await _entry_id(client, "parlement")
    unmatched = await _entry_id(client, "Élection")
    async with session_scope() as session:
        # règle désactivée
        await summarize_entry_job(session, Job(payload={"entry_id": matched, "smart_feed_id": sf["id"]}))
        # article hors correspondance (même en lot manuel)
        await summarize_entry_job(session, Job(payload={"entry_id": unmatched, "smart_feed_id": sf["id"], "manual_batch": True}))
        # règle supprimée
        await summarize_entry_job(session, Job(payload={"entry_id": matched, "smart_feed_id": 999_999}))
        assert fake_provider.calls == []
        # lot manuel sur un article correspondant : la règle désactivée ne bloque pas
        await summarize_entry_job(session, Job(payload={"entry_id": matched, "smart_feed_id": sf["id"], "manual_batch": True}))
        assert len(fake_provider.calls) == 1
    await client.delete(f"/api/smart-feeds/{sf['id']}")
    await client.delete(f"/api/entries/{matched}/summary")


# --- 8 : recalcul atomique -------------------------------------------------------------------------


async def test_failed_recalculation_keeps_existing_matches(client: AsyncClient, monkeypatch):
    from fluxus.db import session_scope
    from fluxus.models import SmartFeed, SmartFeedMatch
    from fluxus.smartfeeds import service

    r = await client.post("/api/smart-feeds", json={"name": "Atomique", "keywords_any": ["intelligence"]})
    sf = r.json()
    assert sf["match_count"] == 3

    async def broken(session, sf_row, where):
        raise DBAPIError("INSERT …", {}, Exception("canceling statement due to statement timeout (simulé)"))

    monkeypatch.setattr(service, "_run_insert", broken)
    async with session_scope() as session:
        row = await session.get(SmartFeed, sf["id"])
        with pytest.raises(service.EvaluationFailed, match="Atomique"):
            await service.apply(session, row)
        count = (await session.execute(select(func.count()).select_from(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf["id"]))).scalar_one()
        assert count == 3
        # la session reste utilisable après l'échec (savepoint annulé, transaction intacte)
        await session.execute(select(func.count()).select_from(SmartFeedMatch))

    # Par l'API : 503 explicite, définition et correspondances inchangées.
    r = await client.put(f"/api/smart-feeds/{sf['id']}", json={"name": "Atomique", "keywords_any": ["gpu"]})
    assert r.status_code == 503 and "impossible" in r.json()["detail"]
    current = (await client.get(f"/api/smart-feeds/{sf['id']}")).json()
    assert current["match_count"] == 3 and current["keywords_any"] == ["intelligence"]

    # Évaluation d'un lot après extraction : l'échec est non bloquant et ne retire rien.
    async with session_scope() as session:
        from fluxus.models import Entry, Feed

        feed = (await session.execute(select(Feed).where(Feed.title == "Tech Feed"))).scalar_one()
        ids = list((await session.execute(select(Entry.id).where(Entry.feed_id == feed.id))).scalars())
        row = await session.get(SmartFeed, sf["id"])
        row.fields = {**row.fields, "content": True}
        await session.flush()
        assert await service.evaluate(session, feed, ids, content_only=True) == []
        count = (await session.execute(select(func.count()).select_from(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf["id"]))).scalar_one()
        assert count == 3
    monkeypatch.undo()
    await client.delete(f"/api/smart-feeds/{sf['id']}")


# --- 17 : règles sur le contenu, retraits ------------------------------------------------------------


async def test_content_reevaluation_removes_false_matches_and_orphans_queued_job(client: AsyncClient, fake_provider):
    from fluxus.db import session_scope
    from fluxus.models import Entry, Feed, Job, SmartFeedMatch
    from fluxus.smartfeeds import service
    from fluxus.smartfeeds.jobs import summarize_entry_job

    await _set_budget(client, 100_000)
    r = await client.post(
        "/api/smart-feeds",
        json={"name": "Contenu", "keywords_any": ["école"], "keywords_none": ["interdit"], "fields": {"title": True, "excerpt": True, "author": True, "url": True, "content": True}, "auto_summary": True},
    )
    sf = r.json()
    entry_id = await _entry_id(client, "école")
    assert sf["match_count"] == 1
    since = datetime.now(UTC)

    async with session_scope() as session:
        entry = await session.get(Entry, entry_id)
        feed = await session.get(Feed, entry.feed_id)
        # Le contenu complet arrive et contient le mot exclu : la correspondance devient fausse.
        entry.content_text = LONG_TEXT + " Ce passage est interdit."
        await session.flush()
        assert await service.evaluate(session, feed, [entry_id], content_only=True) == []
        remaining = (await session.execute(select(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf["id"]))).scalars().all()
        assert remaining == []
    # propagé aux clients (bump updated_at) et visible par l'API
    delta = (await client.get("/api/sync", params={"since": since.isoformat()})).json()
    assert any(e["id"] == entry_id and sf["id"] not in e["smart_feed_ids"] for e in delta["entries"])
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 0
    # un résumé automatique encore en file pour cette paire n'appelle plus le fournisseur
    async with session_scope() as session:
        await summarize_entry_job(session, Job(payload={"entry_id": entry_id, "smart_feed_id": sf["id"]}))
        assert fake_provider.calls == []
    # le contenu redevient conforme : la correspondance revient
    async with session_scope() as session:
        entry = await session.get(Entry, entry_id)
        feed = await session.get(Feed, entry.feed_id)
        entry.content_text = LONG_TEXT
        await session.flush()
        pairs = await service.evaluate(session, feed, [entry_id], content_only=True)
        assert [(p[0].id, p[1]) for p in pairs] == [(sf["id"], {entry_id})]
    await client.delete(f"/api/smart-feeds/{sf['id']}")


# --- 9 : suppression d'une catégorie ciblée ------------------------------------------------------------


async def test_delete_category_removes_stale_matches(client: AsyncClient):
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    r = await client.post("/api/categories", json={"title": "Temporaire"})
    temp = r.json()
    feeds = {f["title"]: f for f in (await client.get("/api/feeds")).json()}
    r = await client.put(f"/api/feeds/{feeds['Actu Feed']['id']}", json={"category_id": temp["id"]})
    assert r.status_code == 200
    r = await client.post("/api/smart-feeds", json={"name": "Portée", "keywords_any": ["intelligence"], "scope_kind": "category", "scope_id": temp["id"]})
    sf = r.json()
    assert sf["match_count"] == 1
    since = datetime.now(UTC)
    r = await client.delete(f"/api/categories/{temp['id']}")
    assert r.status_code == 204
    assert (await client.get(f"/api/smart-feeds/{sf['id']}")).json()["match_count"] == 0
    delta = (await client.get("/api/sync", params={"since": since.isoformat()})).json()
    parlement = next(e for e in delta["entries"] if "parlement" in e["title"])
    assert sf["id"] not in parlement["smart_feed_ids"]
    feed = next(f for f in (await client.get("/api/feeds")).json() if f["title"] == "Actu Feed")
    assert feed["category_id"] is None
    # remise en place
    await client.put(f"/api/feeds/{feed['id']}", json={"category_id": cats["Actu"]["id"]})
    await client.delete(f"/api/smart-feeds/{sf['id']}")


# --- 13 : renommage en doublon ----------------------------------------------------------------------


async def test_rename_category_to_existing_title_is_409(client: AsyncClient):
    cats = {c["title"]: c for c in (await client.get("/api/categories")).json()}
    r = await client.put(f"/api/categories/{cats['Actu']['id']}", json={"title": "tech"})
    assert r.status_code == 409 and r.json()["detail"] == "Cette catégorie existe déjà"
    # renommer vers son propre titre reste permis
    r = await client.put(f"/api/categories/{cats['Actu']['id']}", json={"title": "Actu"})
    assert r.status_code == 200
    assert (await client.get("/api/categories")).status_code == 200


# --- 14 : export OPML, racine mixte ----------------------------------------------------------------


async def test_opml_keeps_mixed_root_order():
    from fluxus.feeds.opml import build_opml
    from fluxus.models import Category, Feed

    xml = build_opml(
        [Category(id=1, title="Folder", position=10), Category(id=2, title="Empty", position=5)],
        [
            Feed(id=10, category_id=1, title="Inside", position=0, url="https://example.com/inside"),
            Feed(id=11, category_id=None, title="Root first", position=-10, url="https://example.com/root"),
            Feed(id=12, category_id=None, title="Root last", position=20, url="https://example.com/last"),
        ],
    )
    assert xml.index('text="Root first"') < xml.index('text="Folder"') < xml.index('text="Inside"') < xml.index('text="Root last"')
    assert 'text="Empty"' not in xml


# --- Audit du 12/11 : journal des résumés manuels (28), source réservée (29), verrou par article (30) ---


async def _usage_rows(entry_id: int) -> list[tuple[str, str, int, int]]:
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.models import LlmUsage

    async with session_scope() as session:
        rows = (await session.execute(select(LlmUsage).where(LlmUsage.entry_id == entry_id).order_by(LlmUsage.id))).scalars().all()
        return [(u.origin, u.status, u.tokens, u.estimated_tokens) for u in rows]


async def test_manual_summary_is_logged_before_the_call_and_after_a_disconnect(client: AsyncClient, monkeypatch):
    """Constat 28 : la ligne `LlmUsage` existe avant l'appel ; un client parti en cours de génération la laisse soldée."""
    from fluxus.db import session_scope
    from fluxus.summarize import service as sum_service
    from fluxus.summarize.providers import StreamResult, Usage

    entry_id = await _entry_id(client, "Élection")
    await client.delete(f"/api/entries/{entry_id}/summary")
    seen_during_call: list = []

    class SlowSummarizer:
        name, model = "fake", "fake-manuel"

        def stream(self, system, user, max_tokens=4000):
            async def gen():
                seen_during_call.extend(await _usage_rows(entry_id))
                yield "Début du résumé"
                yield " — suite jamais lue"

            return StreamResult(gen(), Usage())

    monkeypatch.setattr(sum_service, "build_summarizer", lambda settings: SlowSummarizer())
    before = len(await _usage_rows(entry_id))
    async with session_scope() as session:
        events = sum_service.summarize_entry(session, entry_id, force=True)
        async for event in events:
            if event.type == "delta":
                break
        await events.aclose()  # le client SSE se déconnecte au premier fragment

    reserved = seen_during_call[before:]
    assert len(reserved) == 1 and reserved[0][:2] == ("manual", "reserved") and reserved[0][3] > 0
    after = (await _usage_rows(entry_id))[before:]
    # Tokens réels inconnus (flux interrompu) : l'estimation réservée est comptée.
    assert after == [("manual", "failed", reserved[0][3], reserved[0][3])]


async def test_reserved_source_is_not_prepared_twice(client: AsyncClient, fake_provider, monkeypatch):
    """Constat 29 : le texte sur lequel le budget a été réservé est celui qui est envoyé."""
    from fluxus.db import session_scope
    from fluxus.summarize import service as sum_service

    entry_id = await _entry_id(client, "Élection")
    await client.delete(f"/api/entries/{entry_id}/summary")

    async def boom(*args, **kwargs):
        raise AssertionError("seconde préparation de la source")

    monkeypatch.setattr(sum_service, "_prepare_source", boom)
    async with session_scope() as session:
        events = [e.type async for e in sum_service.summarize_entry(session, entry_id, force=True, source=("article", "Texte réservé. " * 40))]
    assert events[-1] == "done"


async def test_one_generation_at_a_time_per_entry(client: AsyncClient, fake_provider):
    """Constat 30 : une génération en cours sur un article refuse la seconde, puis rend la main."""
    from fluxus.db import session_scope
    from fluxus.models import LlmUsage
    from fluxus.summarize import service as sum_service

    entry_id = await _entry_id(client, "Élection")
    await client.delete(f"/api/entries/{entry_id}/summary")
    before = len(await _usage_rows(entry_id))
    async with sum_service.generation_lock(entry_id) as acquired:
        assert acquired
        r = await client.post(f"/api/entries/{entry_id}/summarize?force=true")
        assert "déjà en cours" in r.text and fake_provider.calls == []
        # Un job automatique dans la même situation rend sa réservation de budget.
        async with session_scope() as session:
            reservation = LlmUsage(entry_id=entry_id, origin="auto", status="reserved", estimated_tokens=500)
            session.add(reservation)
            await session.commit()
            events = [e async for e in sum_service.summarize_entry(session, entry_id, force=True, origin="auto", usage=reservation)]
        assert [e.type for e in events] == ["error"] and "déjà en cours" in events[0].data["message"]
        # Un autre article n'est pas concerné.
        other = await _entry_id(client, "GPU")
        async with sum_service.generation_lock(other) as other_acquired:
            assert other_acquired
    assert (await _usage_rows(entry_id))[before:] == [("auto", "done", 0, 500)]
    assert fake_provider.calls == []
    r = await client.post(f"/api/entries/{entry_id}/summarize?force=true")
    assert "Résumé simulé" in r.text and len(fake_provider.calls) == 1


async def test_sse_client_disconnect_leaves_a_settled_usage_line(client: AsyncClient, monkeypatch):
    """Constat 28, bout en bout : vrai serveur HTTP, client SSE qui coupe pendant la génération."""
    import asyncio
    import socket

    import httpx
    import uvicorn

    from fluxus.main import app
    from fluxus.summarize import service as sum_service
    from fluxus.summarize.providers import StreamResult, Usage

    entry_id = await _entry_id(client, "Élection")
    await client.delete(f"/api/entries/{entry_id}/summary")
    before = len(await _usage_rows(entry_id))

    class EndlessSummarizer:
        name, model = "fake", "fake-lent"

        def stream(self, system, user, max_tokens=4000):
            async def gen():
                while True:
                    yield "fragment "
                    await asyncio.sleep(0.05)

            return StreamResult(gen(), Usage())

    monkeypatch.setattr(sum_service, "build_summarizer", lambda settings: EndlessSummarizer())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(trust_env=False, headers={"Authorization": client.headers["Authorization"]}) as http:
            async with http.stream("POST", f"http://127.0.0.1:{port}/api/entries/{entry_id}/summarize?force=true") as response:
                async for line in response.aiter_lines():
                    if "fragment" in line:
                        break  # sortie du bloc : la connexion est coupée en pleine génération
        rows: list = []
        for _ in range(100):
            rows = (await _usage_rows(entry_id))[before:]
            if rows and rows[0][1] != "reserved":
                break
            await asyncio.sleep(0.1)
        assert len(rows) == 1 and rows[0][:2] == ("manual", "failed") and rows[0][2] == rows[0][3] > 0
        # Le verrou de génération est rendu : l'article peut être résumé à nouveau.
        async with sum_service.generation_lock(entry_id) as acquired:
            assert acquired
    finally:
        server.should_exit = True
        await task
