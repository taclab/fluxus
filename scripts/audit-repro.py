"""Reproductions de l'audit du 15/09/2026, sur base migrée dédiée exclusivement.

DATABASE_URL doit finir par /fluxus_audit_repro. Aucun appel LLM ni HTTP externe.
Les assertions décrivent les défauts observés, pas le comportement souhaité.
"""

import asyncio
import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import func, select

assert os.environ.get("DATABASE_URL", "").endswith("/fluxus_audit_repro"), "Base d'audit dédiée obligatoire"

from fluxus.db import get_engine, session_factory
from fluxus.main import app
from fluxus.models import Category, Entry, Feed, Job, SmartFeed, SmartFeedMatch
from fluxus.settings_store import set_setting
from fluxus.smartfeeds import service
from fluxus.smartfeeds.jobs import summarize_entry_job
from fluxus.summarize.providers import StreamResult, Usage
from fluxus.summarize.service import SummaryEvent
from fluxus.sync.router import StateChange, SyncPush, pull, push


async def seed(session, **kwargs):
    key = uuid4().hex
    feed = Feed(url=f"https://audit.example/{key}", title="Audit", extraction_mode="none")
    session.add(feed)
    await session.flush()
    entry = Entry(feed_id=feed.id, guid=key, hash=key, title="audit", **kwargs)
    session.add(entry)
    await session.commit()
    return feed, entry


async def run():
    results = {}
    factory = session_factory()
    async with factory() as s:
        _, e = await seed(s, updated_at=datetime.now(UTC) - timedelta(hours=2))
        now = datetime.now(UTC)
        result = await push(SyncPush(changes=[
            StateChange(entry_id=e.id, read=True, at=now - timedelta(minutes=2)),
            StateChange(entry_id=e.id, read=False, at=now - timedelta(minutes=1)),
            StateChange(entry_id=e.id, starred=True, at=now - timedelta(seconds=30)),
        ]), "owner", s)
        assert (e.read, e.starred, result.applied, result.ignored) == (True, False, 1, 2)
        results["offline_batch_loses_last_actions"] = result.model_dump(mode="json")

    async with factory() as s:
        _, e = await seed(s, updated_at=datetime.now(UTC))
        result = await push(SyncPush(changes=[StateChange(entry_id=e.id, starred=True,
            at=datetime.now(UTC) - timedelta(minutes=1))]), "owner", s)
        assert result.ignored == 1 and not e.starred
        results["content_clock_rejects_independent_state"] = True

    async with factory() as writer, factory() as reader:
        _, e = await seed(writer, updated_at=datetime.now(UTC) - timedelta(hours=1))
        e.title = "modification non validée"
        e.updated_at = datetime.now(UTC) - timedelta(seconds=5)
        await writer.flush()
        first = await pull("owner", reader, since=datetime.now(UTC) - timedelta(seconds=1), cursor=None, limit=500, include_content=True)
        assert e.id not in [x.id for x in first.entries]
        await writer.commit()
        second = await pull("owner", reader, since=first.server_time, cursor=None, limit=500, include_content=True)
        assert e.id not in [x.id for x in second.entries]
        results["late_commit_permanently_missed_by_delta"] = True

    async def public(url):
        return url

    compressed = gzip.compress(b"<rss>audit</rss>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=compressed))) as client:
        with patch("fluxus.fetcher.http.assert_public_url_async", public):
            from fluxus.fetcher.http import safe_get
            try:
                await safe_get(client, "https://audit.example/rss")
            except httpx.DecodingError:
                results["gzip_double_decompression"] = True
            else:
                raise AssertionError("La réponse gzip a été acceptée : reproduction à réviser")

    async with factory() as s:
        feed, e = await seed(s)
        sf = SmartFeed(name=uuid4().hex, keywords_any=["audit"], fields={"title": True})
        s.add(sf)
        await s.flush()
        s.add(SmartFeedMatch(smart_feed_id=sf.id, entry_id=e.id))
        await s.commit()
        with patch.object(service, "_insert_matches", AsyncMock(return_value=set())):
            result = await service.apply(s, sf)
        await s.commit()
        count = await s.scalar(select(func.count()).select_from(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf.id))
        assert count == 0 and result["removed"] == 1
        results["apply_erases_matches_when_insert_times_out"] = result

    async with factory() as s:
        feed, e = await seed(s)
        cat = Category(title=uuid4().hex)
        s.add(cat)
        await s.flush()
        feed.category_id = cat.id
        sf = SmartFeed(name=uuid4().hex, scope_kind="category", scope_id=cat.id,
            keywords_any=["audit"], fields={"title": True})
        s.add(sf)
        await s.flush()
        s.add(SmartFeedMatch(smart_feed_id=sf.id, entry_id=e.id))
        await s.commit()
        from fluxus.feeds.router import delete_category
        await delete_category(cat.id, "owner", s)
        await s.refresh(feed)
        count = await s.scalar(select(func.count()).select_from(SmartFeedMatch).where(SmartFeedMatch.smart_feed_id == sf.id))
        assert feed.category_id is None and count == 1
        results["deleted_category_keeps_stale_smart_matches"] = True

    async with factory() as s:
        _, e = await seed(s, extraction_status="done", content_text="Article synthétique suffisamment long. " * 20)
        sf = SmartFeed(name=uuid4().hex, auto_summary=False, summary_daily_cap=10)
        s.add(sf)
        used = await service.auto_tokens_today(s)
        await set_setting(s, "llm_auto_daily_token_budget", str(used + 1))
        await s.commit()
        calls = []
        async def fake_summary(*args, **kwargs):
            calls.append(kwargs)
            yield SummaryEvent("done", {})
        with patch("fluxus.smartfeeds.jobs.summarize_entry", fake_summary):
            await summarize_entry_job(s, Job(payload={"entry_id": e.id, "smart_feed_id": sf.id}))
        assert len(calls) == 1
        results["disabled_auto_summary_still_calls_provider_with_one_token_budget"] = True
        class FakeSummarizer:
            name = "audit"
            model = "audit"

            def stream(self, *args, **kwargs):
                async def chunks():
                    yield "Résumé simulé, sans appel fournisseur."
                return StreamResult(chunks(), Usage(input_tokens=90, output_tokens=10))

        with patch("fluxus.summarize.service.build_summarizer", return_value=FakeSummarizer()):
            await summarize_entry_job(s, Job(payload={"entry_id": e.id, "smart_feed_id": sf.id}))
        spent = await service.auto_tokens_today(s) - used
        assert spent == 100
        results["budget_overrun"] = {"remaining_before": 1, "tokens_spent_mock_provider": spent}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://audit") as c:
        response = await c.post("/api/auth/setup", json={"password": "audit-password-only"})
        if response.status_code == 409:
            response = await c.post("/api/auth/login", json={"password": "audit-password-only"})
        c.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        one = (await c.post("/api/categories", json={"title": uuid4().hex})).json()
        two = (await c.post("/api/categories", json={"title": uuid4().hex})).json()
        response = await c.put(f"/api/categories/{two['id']}", json={"title": one["title"]})
        assert response.status_code == 500
        results["duplicate_category_rename_returns_500"] = True
        response = await c.post("/api/entries/mark-read", json={"all": True, "before": "2026-09-15T00:00:00"})
        assert response.status_code == 200
        results["control_naive_mark_read_date_accepted"] = True

    from fluxus.feeds.opml import build_opml
    xml = build_opml([Category(id=123, title="Folder", position=10)], [
        Feed(id=123, category_id=123, title="Inside", position=0, url="https://example.com/inside"),
        Feed(id=124, category_id=None, title="Root first", position=-10, url="https://example.com/root"),
    ])
    assert xml.index('text="Folder"') < xml.index('text="Root first"')
    results["opml_loses_interleaved_root_order"] = True
    print(json.dumps(results, indent=2, ensure_ascii=False))
    await get_engine().dispose()


asyncio.run(run())
