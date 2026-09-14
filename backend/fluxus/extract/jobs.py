from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from ..jobs.registry import job_handler
from ..models import Entry, Job
from .clean import html_to_text
from .service import ExtractionError, extract_article

log = logging.getLogger(__name__)

_IMG_SRC_RE = re.compile(r'<img[^>]+src="(https?://[^"]+)"', re.IGNORECASE)


def first_image(html: str | None) -> str | None:
    """Première image http(s) d'un HTML nettoyé, pour servir de couverture."""
    if not html:
        return None
    m = _IMG_SRC_RE.search(html)
    return m.group(1) if m else None


async def run_extraction(session: AsyncSession, entry: Entry, mode: str | None = None) -> Entry:
    """Extrait l'article complet et met l'entrée à jour. Ne lève pas : l'échec est stocké."""
    effective_mode = mode or (entry.feed.extraction_mode if entry.feed else "auto")
    if effective_mode == "none":
        effective_mode = "auto"
    if not entry.url:
        entry.extraction_status = "failed"
        entry.extraction_error = "Entrée sans URL"
        return entry
    try:
        result = await extract_article(
            entry.url,
            mode=effective_mode,
            reference_text_len=len(html_to_text(entry.summary_html)),
        )
    except Exception as exc:
        # Tout échec (réseau, parseur, URL refusée) est stocké sur l'entrée, jamais remonté en 500.
        if not isinstance(exc, (ExtractionError, ValueError)):
            log.warning("extraction inattendue en échec pour %s: %s", entry.url, exc)
        entry.extraction_status = "failed"
        entry.extraction_error = str(exc)[:2000]
        return entry
    entry.content_html = result.html
    entry.content_text = result.text
    entry.extraction_status = "done"
    entry.extraction_error = None
    meta = dict(entry.meta or {})
    meta["extraction_method"] = result.method
    if not meta.get("cover"):
        cover = first_image(result.html)
        if cover:
            meta["cover"] = cover
    entry.meta = meta
    return entry


@job_handler("extract_entry")
async def extract_entry_job(session: AsyncSession, job: Job) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Entry).options(selectinload(Entry.feed)).where(Entry.id == int(job.payload["entry_id"]))
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return
    await run_extraction(session, entry, job.payload.get("mode"))
