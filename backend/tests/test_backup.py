"""Sauvegarde complète : export, import par remplacement, refus sans effet. Nécessite PostgreSQL."""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL non défini"),
    pytest.mark.asyncio(loop_scope="module"),
]

PASSWORD = "motdepasse"


@pytest.fixture(scope="module")
async def client():
    os.environ.setdefault("FLUXUS_STATIC_DIR", "/nonexistent")
    from sqlalchemy import text

    from fluxus.db import Base, get_engine, session_scope
    from fluxus.main import app
    from fluxus.models import (
        EXTENSION_STATEMENTS,
        SEARCH_VECTOR_STATEMENTS,
        Category,
        Entry,
        Feed,
        SmartFeed,
        SmartFeedMatch,
        Summary,
        Transcript,
    )

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
        tech = Category(title="Tech", position=1, view_mode="cards")
        session.add(tech)
        await session.flush()
        hn = Feed(url="https://hn.example/rss", title="Hacker News", category_id=tech.id, view_mode="list", auto_summary=True, summary_daily_cap=3, fetch_interval_fixed_min=60)
        solo = Feed(url="https://solo.example/rss", title="Solo", position=-2)
        session.add_all([hn, solo])
        await session.flush()
        sf = SmartFeed(name="IA", keywords_any=["intelligence"], fields={"title": True}, view_mode="magazine")
        session.add(sf)
        await session.flush()
        entries = []
        for i in range(5):
            e = Entry(
                feed_id=hn.id,
                guid=f"hn-{i}",
                url=f"https://hn.example/{i}",
                title=f"Intelligence artificielle numéro {i}",
                author="Ada",
                summary_html="<p>Résumé du flux</p>",
                content_text="Texte complet « accentué » " * 10,
                published_at=now - timedelta(hours=i),
                read=i % 2 == 0,
                starred=i == 1,
                read_changed_at=now,
                hash=f"h{i}",
                meta={"cover": f"https://img.example/{i}.png"},
            )
            session.add(e)
            entries.append(e)
        await session.flush()
        session.add(Summary(entry_id=entries[0].id, model="m", text_md="**Résumé**", origin="auto", smart_feed_id=sf.id))
        session.add(Transcript(entry_id=entries[1].id, language="fr", text="bonjour", segments=[{"start": 0, "duration": 1, "text": "bonjour"}]))
        session.add(SmartFeedMatch(smart_feed_id=sf.id, entry_id=entries[0].id))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/auth/setup", json={"password": PASSWORD})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        await c.patch("/api/settings", json={"values": {"llm_api_key_anthropic": "sk-ant-secret-1234567890", "default_view_mode": "grid", "llm_language": "en"}})
        yield c
    await engine.dispose()


async def _export(client: AsyncClient) -> bytes:
    link = await client.post("/api/backup/export-link")
    assert link.status_code == 200, link.text
    r = await client.get(link.json()["url"], headers={"Authorization": ""})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/gzip"
    assert "fluxus-export-" in r.headers["content-disposition"]
    return r.content


def _records(archive: bytes) -> tuple[dict, list[dict]]:
    lines = [json.loads(line) for line in gzip.decompress(archive).decode().splitlines() if line.strip()]
    return lines[0], lines[1:]


async def _import(client: AsyncClient, archive: bytes, *, password: str = PASSWORD, confirm: str = "REMPLACER"):
    return await client.post(
        "/api/backup/import",
        files={"file": ("sauvegarde.jsonl.gz", archive, "application/gzip")},
        data={"password": password, "confirm": confirm},
    )


async def _snapshot(client: AsyncClient) -> dict:
    pull = (await client.get("/api/sync", params={"limit": 500})).json()
    strip = lambda rows, *keys: sorted(({k: v for k, v in r.items() if k not in keys} for r in rows), key=lambda r: r["id"])  # noqa: E731
    return {
        "categories": strip(pull["categories"], "unread_count"),
        "feeds": strip(pull["feeds"], "unread_count"),
        "smart_feeds": strip(pull["smart_feeds"], "match_count", "unread_count", "auto_today"),
        "entries": strip(pull["entries"]),
        "summaries": sorted(pull["summaries"], key=lambda s: s["id"]),
        "default_view_mode": pull["default_view_mode"],
        "epoch": pull["epoch"],
    }


async def test_export_content_and_exclusions(client: AsyncClient):
    header, rows = _records(await _export(client))
    assert header["format"] == "fluxus-export" and header["version"] == 1
    tables = [r["t"] for r in rows]
    assert tables == sorted(tables, key=["categories", "feeds", "smart_feeds", "entries", "summaries", "transcripts", "smart_feed_matches", "settings"].index)
    assert header["counts"]["entries"] == 5 and header["counts"]["transcripts"] == 1
    keys = {r["r"]["key"] for r in rows if r["t"] == "settings"}
    assert "default_view_mode" in keys and "llm_language" in keys
    assert not any(k.startswith("_") or k.startswith("llm_api_key") for k in keys)
    assert "sk-ant" not in gzip.decompress(await _export(client)).decode()
    assert all("search_vector" not in r["r"] for r in rows if r["t"] == "entries")


async def test_roundtrip_replaces_everything(client: AsyncClient):
    from sqlalchemy import func, select

    from fluxus.db import session_scope
    from fluxus.models import Entry, LlmUsage

    async with session_scope() as session:
        first = (await session.execute(select(Entry.id).limit(1))).scalar_one()
        session.add(LlmUsage(entry_id=first, origin="auto", status="done", tokens=1234))
    before = await _snapshot(client)
    assert before["epoch"] == "0"  # instance jamais restaurée
    archive = await _export(client)

    # L'instance diverge : nouveau flux, dossier renommé, article supprimé, réglage changé.
    await client.post("/api/categories", json={"title": "Parasite"})
    await client.patch("/api/settings", json={"values": {"default_view_mode": "titles"}})
    async with session_scope() as session:
        e = (await session.execute(select(Entry).limit(1))).scalar_one()
        await session.delete(e)

    r = await _import(client, archive)
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["entries"] == 5
    # Le journal LLM survit (budget du jour), ses liens vers les anciens articles sont coupés.
    async with session_scope() as session:
        assert (await session.execute(select(func.sum(LlmUsage.tokens)))).scalar_one() == 1234
        assert (await session.execute(select(LlmUsage.entry_id))).scalar_one() is None

    after = await _snapshot(client)
    assert after["epoch"] != before["epoch"]
    for key in ("categories", "feeds", "smart_feeds", "entries", "summaries", "default_view_mode"):
        assert after[key] == before[key], key
    # Réglages et secrets de la cible conservés ; mot de passe inchangé.
    settings = (await client.get("/api/settings")).json()
    assert settings["secrets_set"]["llm_api_key_anthropic"] is True
    assert settings["values"]["llm_language"] == "en"
    assert (await client.post("/api/auth/login", json={"password": PASSWORD})).status_code == 200
    # Transcription et correspondances restaurées ; recherche plein texte recalculée par le trigger.
    assert (await client.get(f"/api/smart-feeds/{before['smart_feeds'][0]['id']}")).json()["match_count"] == 1
    assert (await client.get("/api/entries", params={"q": "complet"})).json()["items"]
    # Séquences recalées : une nouvelle création ne collisionne pas.
    r = await client.post("/api/categories", json={"title": "Nouveau"})
    assert r.status_code == 201 and r.json()["id"] > max(c["id"] for c in before["categories"])


async def test_refusals_leave_instance_untouched(client: AsyncClient):
    archive = await _export(client)
    before = await _snapshot(client)
    assert (await _import(client, archive, password="mauvais")).status_code == 403
    assert (await _import(client, archive, confirm="oui")).status_code == 400
    r = await _import(client, b"pas du gzip")
    assert r.status_code == 400 and "illisible" in r.json()["detail"]
    r = await _import(client, archive[: len(archive) // 2])
    assert r.status_code == 400
    # En-tête gzip valide, corps altéré (zlib.error) : message clair, pas d'erreur 500.
    damaged = bytearray(archive)
    for i in range(20, min(len(damaged), 200)):
        damaged[i] ^= 0xFF
    r = await _import(client, bytes(damaged))
    assert r.status_code == 400 and "illisible" in r.json()["detail"]
    r = await client.post("/api/backup/import", data={"password": PASSWORD, "confirm": "REMPLACER"})
    assert r.status_code == 400 and "Formulaire incomplet" in r.json()["detail"]

    header, rows = _records(archive)
    rows[3]["r"]["colonne_du_futur"] = 1
    forged = gzip.compress("\n".join(json.dumps(x) for x in [header, *rows]).encode())
    r = await _import(client, forged)
    assert r.status_code == 400 and "colonne_du_futur" in r.json()["detail"]

    header, rows = _records(archive)
    missing = gzip.compress("\n".join(json.dumps(x) for x in [header, *rows[:-2]]).encode())
    r = await _import(client, missing)
    assert r.status_code == 400 and "incomplète" in r.json()["detail"]

    # Lien cassé (résumé d'un article absent) : rejeté par la base, rollback complet.
    header2, rows2 = _records(archive)
    for row in rows2:
        if row["t"] == "summaries":
            row["r"]["entry_id"] = 999999
    broken = gzip.compress("\n".join(json.dumps(x) for x in [header2, *rows2]).encode())
    r = await _import(client, broken)
    assert r.status_code == 400 and "rien n'a été modifié" in r.json()["detail"]

    after = await _snapshot(client)
    assert after == before


async def test_older_archive_without_new_columns(client: AsyncClient):
    """Une sauvegarde d'une version antérieure (colonnes absentes) prend les valeurs par défaut."""
    header, rows = _records(await _export(client))
    for row in rows:
        if row["t"] == "feeds":
            for col in ("view_mode", "auto_summary", "summary_daily_cap", "fetch_interval_fixed_min"):
                row["r"].pop(col, None)
    old = gzip.compress("\n".join(json.dumps(x) for x in [header, *rows]).encode())
    r = await _import(client, old)
    assert r.status_code == 200, r.text
    feeds = (await client.get("/api/feeds")).json()
    assert all(f["view_mode"] is None and f["auto_summary"] is False and f["summary_daily_cap"] == 10 for f in feeds)


async def test_import_waits_for_running_jobs(client: AsyncClient, monkeypatch):
    """Un job en cours (verrou partagé) retarde l'import ; au-delà du délai, refus sans effet. Un job
    supprimé par un import est ensuite ignoré par le worker."""
    from sqlalchemy import func, select

    from fluxus.backup import service
    from fluxus.db import get_engine
    from fluxus.models import Job
    from fluxus.worker import _run_locked

    archive = await _export(client)
    before = await _snapshot(client)
    monkeypatch.setattr(service, "LOCK_WAIT_SECONDS", 1)
    async with get_engine().connect() as conn:
        await conn.execute(select(func.pg_advisory_lock_shared(service.MAINTENANCE_LOCK_KEY)))
        r = await _import(client, archive)
        await conn.execute(select(func.pg_advisory_unlock_shared(service.MAINTENANCE_LOCK_KEY)))
        await conn.commit()
    assert r.status_code == 503 and "réessayez" in r.json()["detail"]
    assert await _snapshot(client) == before

    ghost = Job(id=987654, type="fetch_feed", payload={"feed_id": 1})
    await _run_locked(ghost)  # ligne absente : rien n'est exécuté ni marqué


async def test_stale_epoch_rejects_pending_changes(client: AsyncClient):
    """Après un import, un appareil qui envoie des changements avec l'ancienne époque est refusé."""
    old_epoch = (await client.get("/api/sync?limit=1")).json()["epoch"]
    assert (await _import(client, await _export(client))).status_code == 200
    new_epoch = (await client.get("/api/sync?limit=1")).json()["epoch"]
    assert new_epoch != old_epoch
    entry = (await client.get("/api/entries?limit=1")).json()["items"][0]
    change = {"entry_id": entry["id"], "read": not entry["read"], "at": datetime.now(UTC).isoformat()}
    r = await client.post("/api/sync", json={"changes": [change], "epoch": old_epoch})
    assert r.status_code == 409
    assert (await client.get(f"/api/entries/{entry['id']}")).json()["read"] == entry["read"]
    feed_id = (await client.get("/api/feeds")).json()[0]["id"]
    r = await client.put("/api/view-mode", json={"kind": "feed", "id": feed_id, "view_mode": "titles", "epoch": old_epoch})
    assert r.status_code == 409
    # Client sans époque (version antérieure) : refusé dès que l'instance a été restaurée.
    assert (await client.post("/api/sync", json={"changes": [change]})).status_code == 409
    r = await client.post("/api/sync", json={"changes": [change], "epoch": new_epoch})
    assert r.status_code == 200 and r.json()["applied"] == 1


async def test_import_running_refuses_enqueue_and_generation(client: AsyncClient):
    """Pendant un import (verrou exclusif), une mise en file depuis l'API est refusée en 503 au lieu de
    créer un job qui viserait un identifiant réattribué ; le planificateur passe son tour."""
    from sqlalchemy import func, select

    from fluxus import maintenance
    from fluxus.db import get_engine
    from fluxus.worker import schedule_due_feeds

    feed_id = (await client.get("/api/feeds")).json()[0]["id"]
    async with get_engine().connect() as conn:
        await conn.execute(select(func.pg_advisory_lock(maintenance.MAINTENANCE_LOCK_KEY)))
        try:
            r = await client.post(f"/api/feeds/{feed_id}/refresh")
            assert r.status_code == 503 and "Import" in r.json()["detail"]
            with pytest.raises(maintenance.Busy):
                await schedule_due_feeds()
        finally:
            await conn.execute(select(func.pg_advisory_unlock(maintenance.MAINTENANCE_LOCK_KEY)))
            await conn.commit()
    assert (await client.post(f"/api/feeds/{feed_id}/refresh")).status_code == 202


async def test_import_requires_content_length(client: AsyncClient):
    """Envoi « chunked » (sans Content-Length) : refusé avant toute écriture sur disque."""

    async def body():
        yield b"--x\r\n"

    r = await client.post("/api/backup/import", content=body(), headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 411


async def test_export_link_is_single_use_and_expires(client: AsyncClient, monkeypatch):
    """Le lien d'export ne sert qu'une fois, expire, et un ticket faux n'invalide pas le lien en cours."""
    from datetime import timedelta

    from fluxus.backup import router as backup_router

    assert (await client.get("/api/backup/export", params={"ticket": "faux"})).status_code == 403
    url = (await client.post("/api/backup/export-link")).json()["url"]
    assert (await client.get("/api/backup/export", params={"ticket": "faux"})).status_code == 403
    assert (await client.get(url, headers={"Authorization": ""})).status_code == 200
    assert (await client.get(url, headers={"Authorization": ""})).status_code == 403
    # Un nouveau lien remplace le précédent.
    first = (await client.post("/api/backup/export-link")).json()["url"]
    second = (await client.post("/api/backup/export-link")).json()["url"]
    assert (await client.get(first)).status_code == 403
    assert (await client.get(second)).status_code == 200
    monkeypatch.setattr(backup_router, "EXPORT_TICKET_TTL", timedelta(seconds=-1))
    expired = (await client.post("/api/backup/export-link")).json()["url"]
    assert (await client.get(expired)).status_code == 403
    assert (await client.post("/api/backup/export-link", headers={"Authorization": ""})).status_code == 401
