"""Protocole de synchronisation (constats 3, 4 et 5 de l'audit du 15/09/2026). Nécessite la base PostgreSQL de test."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest.fixture(scope="module")
async def client():
    os.environ.setdefault("FLUXUS_STATIC_DIR", "/nonexistent")
    from fluxus.db import Base, get_engine, session_scope
    from fluxus.main import app
    from fluxus.models import EXTENSION_STATEMENTS, SEARCH_VECTOR_STATEMENTS, Feed

    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in EXTENSION_STATEMENTS:
            await conn.execute(text(stmt))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in SEARCH_VECTOR_STATEMENTS:
            await conn.execute(text(stmt))
    async with session_scope() as session:
        session.add(Feed(url="https://sync.example/rss", title="Sync"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": "motdepasse"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c
    await engine.dispose()


async def _new_entry(**kwargs) -> int:
    from sqlalchemy import select

    from fluxus.db import session_scope
    from fluxus.models import Entry, Feed

    key = f"k{datetime.now(UTC).timestamp()}"
    async with session_scope() as session:
        feed_id = (await session.execute(select(Feed.id))).scalar_one()
        entry = Entry(feed_id=feed_id, guid=key, hash=key, url=f"https://sync.example/{key}", title="Article", **kwargs)
        session.add(entry)
        await session.flush()
        return entry.id


async def test_batch_applies_successive_actions_in_order(client: AsyncClient):
    """Constat 3 : lu → non lu → favori, datées dans le passé, toutes appliquées."""
    entry_id = await _new_entry(updated_at=datetime.now(UTC) - timedelta(hours=2))
    now = datetime.now(UTC)
    changes = [
        {"entry_id": entry_id, "read": True, "at": (now - timedelta(minutes=2)).isoformat()},
        {"entry_id": entry_id, "read": False, "at": (now - timedelta(minutes=1)).isoformat()},
        {"entry_id": entry_id, "starred": True, "at": (now - timedelta(seconds=30)).isoformat()},
    ]
    r = await client.post("/api/sync", json={"changes": changes})
    body = r.json()
    assert (body["applied"], body["ignored"], body["conflicts"]) == (3, 0, [])
    e = (await client.get(f"/api/entries/{entry_id}")).json()
    assert (e["read"], e["starred"]) == (False, True)


async def test_content_clock_does_not_invalidate_user_actions(client: AsyncClient):
    """Constat 5 : un favori hors ligne antérieur à une mise à jour de contenu est accepté."""
    entry_id = await _new_entry(updated_at=datetime.now(UTC))  # « contenu » tout juste mis à jour
    at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    r = await client.post("/api/sync", json={"changes": [{"entry_id": entry_id, "starred": True, "at": at}]})
    assert r.json()["applied"] == 1 and r.json()["ignored"] == 0
    assert (await client.get(f"/api/entries/{entry_id}")).json()["starred"] is True


async def test_conflicting_field_change_is_refused_with_server_state(client: AsyncClient):
    """Un changement plus ancien que le dernier changement *du même champ* est refusé, avec l'état serveur."""
    entry_id = await _new_entry()
    now = datetime.now(UTC)
    r = await client.post("/api/sync", json={"changes": [{"entry_id": entry_id, "read": True, "at": now.isoformat()}]})
    assert r.json()["applied"] == 1
    # un autre appareil, en retard, veut remettre non lu et ajouter un favori : le favori passe, le lu non
    stale = (now - timedelta(minutes=5)).isoformat()
    r = await client.post("/api/sync", json={"changes": [{"entry_id": entry_id, "read": False, "starred": True, "at": stale}]})
    body = r.json()
    assert body["applied"] == 1 and body["ignored"] == 1
    assert body["conflicts"] == [{"entry_id": entry_id, "read": True, "starred": True}]


async def test_pull_horizon_waits_for_open_transactions(client: AsyncClient):
    """Constat 4 : une écriture validée après le pull, mais horodatée avant, n'est pas perdue."""
    from fluxus.db import session_factory

    entry_id = await _new_entry(updated_at=datetime.now(UTC) - timedelta(days=1))
    # état initial connu du client
    since = (await client.get("/api/sync")).json()["server_time"]

    factory = session_factory()
    async with factory() as other:
        # Transaction ouverte : comme dans l'application, updated_at est pris à l'horloge Python après le
        # début de la transaction (jamais avant), puis le commit arrive après le pull d'un client.
        await other.execute(text("SELECT 1"))
        stamp = datetime.now(UTC)
        await other.execute(text("UPDATE entries SET read = true, updated_at = :t WHERE id = :id"), {"t": stamp, "id": entry_id})
        pulled = (await client.get("/api/sync", params={"since": since})).json()
        assert entry_id not in [e["id"] for e in pulled["entries"]]  # pas encore visible : normal
        next_since = pulled["server_time"]
        # l'horizon renvoyé recule jusqu'au début de la transaction encore ouverte
        assert datetime.fromisoformat(next_since) <= stamp
        await other.commit()

    pulled = (await client.get("/api/sync", params={"since": next_since})).json()
    assert entry_id in [e["id"] for e in pulled["entries"]]
    # sans transaction ouverte, l'horizon revient à « maintenant »
    fresh = (await client.get("/api/sync")).json()["server_time"]
    assert datetime.now(UTC) - datetime.fromisoformat(fresh) < timedelta(seconds=5)


async def test_pull_horizon_covers_a_commit_during_the_pull(client: AsyncClient, monkeypatch):
    """Audit du 12/11, constat 8 : une transaction validée pendant que le pull prépare sa page
    (après la lecture des entrées) n'est ni dans la page ni perdue au pull suivant."""
    from fluxus.db import session_factory
    from fluxus.sync import router as sync_router

    entry_id = await _new_entry(updated_at=datetime.now(UTC) - timedelta(days=1))
    since = (await client.get("/api/sync")).json()["server_time"]

    factory = session_factory()
    async with factory() as other:
        await other.execute(text("SELECT 1"))
        stamp = datetime.now(UTC)
        await other.execute(text("UPDATE entries SET read = true, updated_at = :t WHERE id = :id"), {"t": stamp, "id": entry_id})

        original = sync_router.smart_feed_ids_for

        async def commit_then_continue(session, ids):
            # Appelé juste après la lecture des entrées : la transaction du worker se termine ici.
            await other.commit()
            return await original(session, ids)

        monkeypatch.setattr(sync_router, "smart_feed_ids_for", commit_then_continue)
        pulled = (await client.get("/api/sync", params={"since": since})).json()
        monkeypatch.undo()

    assert entry_id not in [e["id"] for e in pulled["entries"]]
    assert datetime.fromisoformat(pulled["server_time"]) <= stamp
    pulled = (await client.get("/api/sync", params={"since": pulled["server_time"]})).json()
    assert entry_id in [e["id"] for e in pulled["entries"]]
