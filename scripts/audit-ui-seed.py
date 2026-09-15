"""Jeu de données synthétique pour audit-ui.py ; base temporaire uniquement."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

assert os.environ.get("DATABASE_URL", "").endswith("/fluxus_audit_repro")

from fluxus.db import get_engine, session_scope
from fluxus.models import Entry, Feed


async def main():
    async with session_scope() as s:
        feed = Feed(title="Audit UI 130", url=f"https://audit.example/{uuid4().hex}", extraction_mode="none")
        s.add(feed)
        await s.flush()
        for i in range(130):
            s.add(Entry(feed_id=feed.id, guid=str(i), hash=uuid4().hex,
                title=f"Auditneedle article {i:03}", summary_html="<p>Contenu synthétique pour tester la lecture.</p>",
                url=f"https://audit.example/article/{i}", published_at=datetime.now(UTC) - timedelta(minutes=i)))
        print(f"feed_id={feed.id}")
    await get_engine().dispose()


asyncio.run(main())
