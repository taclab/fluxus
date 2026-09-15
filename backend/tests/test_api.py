"""Tests d'intégration contre la base PostgreSQL de développement (docker compose up postgres).

Ignorés si la base n'est pas joignable.
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
        yield c
    await engine.dispose()


async def test_auth_flow(client: AsyncClient):
    r = await client.get("/api/auth/status")
    assert r.json() == {"setup_required": True}
    r = await client.post("/api/auth/setup", json={"password": "motdepasse"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    r = await client.post("/api/auth/setup", json={"password": "motdepasse"})
    assert r.status_code == 409
    r = await client.post("/api/auth/login", json={"password": "mauvais-mdp"})
    assert r.status_code == 401
    r = await client.get("/api/feeds")
    assert r.status_code == 401
    r = await client.get("/api/feeds", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == []
    client.headers["Authorization"] = f"Bearer {token}"


async def test_password_change_invalidates_sessions(client: AsyncClient):
    old = client.headers["Authorization"]
    r = await client.post("/api/auth/password", json={"current_password": "motdepasse", "new_password": "motdepasse2"})
    assert r.status_code == 200
    new_token = r.json()["access_token"]
    r = await client.get("/api/feeds", headers={"Authorization": old})
    assert r.status_code == 401
    client.headers["Authorization"] = f"Bearer {new_token}"
    r = await client.get("/api/feeds")
    assert r.status_code == 200
    # on remet l'ancien mot de passe pour les tests suivants
    r = await client.post("/api/auth/password", json={"current_password": "motdepasse2", "new_password": "motdepasse"})
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


async def test_device_token(client: AsyncClient):
    r = await client.post("/api/auth/devices", json={"name": "Téléphone", "password": "motdepasse"})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token.startswith("flx_")
    r = await client.get("/api/feeds", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    devices = (await client.get("/api/auth/devices")).json()
    assert devices[0]["name"] == "Téléphone"
    r = await client.delete(f"/api/auth/devices/{devices[0]['id']}")
    assert r.status_code == 204
    r = await client.get("/api/feeds", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_feed_ssrf_rejected(client: AsyncClient):
    r = await client.post("/api/feeds", json={"url": "http://127.0.0.1:1/feed"})
    assert r.status_code == 400
    assert "privée" in r.json()["detail"]


async def test_entries_and_sync(client: AsyncClient):
    from fluxus.db import session_scope
    from fluxus.models import Entry, Feed

    async with session_scope() as session:
        feed = Feed(url="https://test.example/rss", title="Test")
        session.add(feed)
        await session.flush()
        now = datetime.now(UTC)
        for i in range(3):
            session.add(
                Entry(
                    feed_id=feed.id,
                    guid=f"g{i}",
                    url=f"https://test.example/{i}",
                    title=f"Article {i} chaton",
                    published_at=now - timedelta(hours=i),
                    summary_html="<p>Un texte sur les chatons</p>",
                    hash=f"h{i}",
                )
            )

    r = await client.get("/api/entries")
    items = r.json()["items"]
    assert [i["title"] for i in items] == ["Article 0 chaton", "Article 1 chaton", "Article 2 chaton"]

    r = await client.get("/api/entries", params={"q": "chatons"})
    assert len(r.json()["items"]) == 3
    r = await client.get("/api/entries", params={"q": "girafe"})
    assert r.json()["items"] == []

    r = await client.get("/api/entries", params={"limit": 2})
    assert r.json()["next_cursor"]
    r2 = await client.get("/api/entries", params={"limit": 2, "cursor": r.json()["next_cursor"]})
    assert [i["title"] for i in r2.json()["items"]] == ["Article 2 chaton"]

    first_id = items[0]["id"]
    r = await client.patch(f"/api/entries/{first_id}", json={"starred": True, "read": True})
    assert r.json()["starred"] is True and r.json()["read"] is True

    r = await client.get("/api/entries", params={"status": "unread"})
    assert len(r.json()["items"]) == 2

    r = await client.post("/api/entries/mark-read", json={"all": True})
    assert r.json()["updated"] == 2

    # Sync delta : tout puis rien.
    r = await client.get("/api/sync")
    body = r.json()
    assert len(body["entries"]) == 3 and body["has_more"] is False
    assert body["retention_days"] == 30
    # La marge de sécurité de 2 s renvoie encore les entrées tout juste modifiées ; au-delà, plus rien.
    later = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
    r = await client.get("/api/sync", params={"since": later})
    assert r.json()["entries"] == []

    # Push : un changement plus récent gagne, un changement périmé est ignoré.
    stale = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    fresh = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    r = await client.post(
        "/api/sync",
        json={"changes": [{"entry_id": first_id, "read": False, "at": stale}, {"entry_id": items[1]["id"], "read": False, "at": fresh}]},
    )
    assert r.json()["applied"] == 1 and r.json()["ignored"] == 1
    r = await client.get(f"/api/entries/{items[1]['id']}")
    assert r.json()["read"] is False


async def test_settings_secrets_masked(client: AsyncClient):
    r = await client.patch("/api/settings", json={"values": {"llm_api_key_anthropic": "sk-ant-secret-1234567890"}})
    assert r.status_code == 200
    body = r.json()
    assert body["secrets_set"]["llm_api_key_anthropic"] is True
    assert "secret" not in body["values"]["llm_api_key_anthropic"]
    r = await client.patch("/api/settings", json={"values": {"inconnu": "x"}})
    assert r.status_code == 400


async def test_dashboard_layout_setting(client: AsyncClient):
    layout = '{"version": 1, "updatedAt": "2026-09-14T15:00:00Z", "widgets": [{"id": "a", "type": "stats", "width": 6}]}'
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": layout}})
    assert r.status_code == 200
    assert r.json()["values"]["dashboard_layout"] == layout
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": "pas du json"}})
    assert r.status_code == 400
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": '{"widgets": 3}'}})
    assert r.status_code == 400
    r = await client.patch("/api/settings", json={"values": {"dashboard_layout": ""}})
    assert r.status_code == 200 and r.json()["values"]["dashboard_layout"] == ""


async def test_summarize_sse_with_mocked_provider(client: AsyncClient, monkeypatch):
    """Vérifie la chaîne SSE complète avec un fournisseur simulé (aucune clé API nécessaire)."""
    from fluxus.summarize import service
    from fluxus.summarize.providers import StreamResult, Summarizer, Usage

    class FakeSummarizer(Summarizer):
        name = "fake"

        def __init__(self):
            self.model = "fake-1"
            self.effort = "low"

        def stream(self, system, user, max_tokens=4000):
            usage = Usage()

            async def gen():
                assert "chatons" in user
                for piece in ["### L'essentiel\n", "Des **chatons**.", "\n\n### Points clés\n- un\n- deux"]:
                    yield piece
                usage.input_tokens, usage.output_tokens = 120, 30

            return StreamResult(gen(), usage)

    monkeypatch.setattr(service, "build_summarizer", lambda settings: FakeSummarizer())
    monkeypatch.setattr(service, "MIN_SOURCE_CHARS", 5)

    entry_id = (await client.get("/api/entries")).json()["items"][0]["id"]
    # Le flux ne fournit pas assez de texte et l'URL test.example n'existe pas : l'extraction échoue
    # proprement et le service retombe sur le contenu du flux.
    r = await client.post(f"/api/entries/{entry_id}/summarize")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [line for line in r.text.splitlines() if line.startswith("event:")]
    assert events[0] == "event: status"
    assert "event: delta" in events
    assert events[-1] == "event: done"
    assert "Des **chatons**." in r.text

    stored = (await client.get(f"/api/entries/{entry_id}/summary")).json()
    assert stored["model"] == "fake-1" and stored["input_tokens"] == 120
    assert stored["text_md"].startswith("### L'essentiel")

    # Second appel : résumé en cache, aucun nouvel appel au fournisseur.
    r = await client.post(f"/api/entries/{entry_id}/summarize")
    assert '"cached": true' in r.text
    assert "event: delta" not in r.text


async def test_sync_pagination_with_identical_timestamps(client: AsyncClient):
    """Des centaines d'entrées créées dans la même seconde ne doivent pas bloquer le curseur."""
    from fluxus.db import session_scope
    from fluxus.models import Entry, Feed

    same_time = datetime.now(UTC)
    async with session_scope() as session:
        feed = Feed(url="https://bulk.example/rss", title="Bulk")
        session.add(feed)
        await session.flush()
        for i in range(7):
            session.add(
                Entry(feed_id=feed.id, guid=f"bulk{i}", title=f"Bulk {i}", published_at=same_time, hash=f"bh{i}", updated_at=same_time)
            )

    seen: list[int] = []
    since = (same_time - timedelta(seconds=1)).isoformat()
    params: dict = {"since": since, "limit": 3}
    for _ in range(10):
        r = await client.get("/api/sync", params=params)
        body = r.json()
        seen += [e["id"] for e in body["entries"] if e["title"].startswith("Bulk")]
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        params = {"cursor": body["next_cursor"], "limit": 3}
    assert len(seen) == 7 and len(set(seen)) == 7

    r = await client.get("/api/sync", params={"cursor": "n-importe-quoi"})
    assert r.status_code == 400


async def test_spa_path_traversal_is_blocked(client: AsyncClient, tmp_path, monkeypatch):
    """Le repli SPA ne doit servir que des fichiers sous le dossier statique."""
    import importlib

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>")
    (static / "app.js").write_text("js")
    (tmp_path / "secret.txt").write_text("SECRET")
    monkeypatch.setenv("FLUXUS_STATIC_DIR", str(static))
    from fluxus import config, main

    config.get_settings.cache_clear()
    module = importlib.reload(main)
    async with AsyncClient(transport=ASGITransport(app=module.app), base_url="http://test") as c:
        assert (await c.get("/app.js")).text == "js"
        assert (await c.get("/n-importe-quoi")).text == "<html>ok</html>"
        for path in ("/..%2Fsecret.txt", "/%2e%2e/secret.txt", "/a/../../secret.txt"):
            r = await c.get(path)
            assert "SECRET" not in r.text, path
    monkeypatch.delenv("FLUXUS_STATIC_DIR")
    config.get_settings.cache_clear()
    importlib.reload(main)


async def test_enqueue_collision_keeps_other_jobs(client: AsyncClient):
    from sqlalchemy import delete, select

    from fluxus.db import session_scope
    from fluxus.jobs.queue import enqueue
    from fluxus.models import Job

    async with session_scope() as session:
        await session.execute(delete(Job))
        assert await enqueue(session, "fetch_feed", {"feed_id": 2}, dedupe_key="fetch_feed:2")
    async with session_scope() as session:
        results = [await enqueue(session, "fetch_feed", {"feed_id": i}, dedupe_key=f"fetch_feed:{i}") is not None for i in (1, 2, 3)]
    assert results == [True, False, True]
    async with session_scope() as session:
        keys = sorted((await session.execute(select(Job.dedupe_key))).scalars())
    assert keys == ["fetch_feed:1", "fetch_feed:2", "fetch_feed:3"]


async def test_sync_push_clamps_future_clock(client: AsyncClient):
    entry_id = (await client.get("/api/entries", params={"limit": 1})).json()["items"][0]["id"]
    future = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    r = await client.post("/api/sync", json={"changes": [{"entry_id": entry_id, "starred": True, "at": future}]})
    assert r.json()["applied"] == 1
    updated_at = datetime.fromisoformat((await client.get(f"/api/entries/{entry_id}")).json()["updated_at"])
    assert updated_at < datetime.now(UTC) + timedelta(minutes=1)
    # Un changement correctement daté peut ensuite reprendre la main.
    r = await client.post("/api/sync", json={"changes": [{"entry_id": entry_id, "starred": False, "at": datetime.now(UTC).isoformat()}]})
    assert r.json()["applied"] == 1


async def test_naive_dates_are_accepted(client: AsyncClient):
    r = await client.get("/api/sync", params={"since": "2026-01-01T00:00:00"})
    assert r.status_code == 200
