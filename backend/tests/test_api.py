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
    from fluxus.models import SEARCH_VECTOR_STATEMENTS  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
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
