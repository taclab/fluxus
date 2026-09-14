"""Orchestration d'un résumé : choix de la source, découpage, appel du fournisseur, persistance."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..extract.clean import html_to_text
from ..extract.jobs import run_extraction
from ..models import Entry, Summary, Transcript
from ..settings_store import get_all_settings
from ..youtube.transcript import Segment, TranscriptUnavailable, VideoTranscript, fetch_transcript
from . import prompts
from .providers import SummarizerError, Usage, build_summarizer

log = logging.getLogger(__name__)

# Au-delà de cette taille (en caractères), on découpe et on fusionne des résumés partiels.
CHUNK_THRESHOLD = 250_000
CHUNK_SIZE = 120_000
MIN_SOURCE_CHARS = 200


@dataclass
class SummaryEvent:
    type: str  # status | delta | done | error
    data: dict


async def _prepare_source(session: AsyncSession, entry: Entry) -> tuple[str, str, str]:
    """Renvoie (kind, texte source, description de l'étape)."""
    meta = entry.meta or {}
    video_id = meta.get("youtube_id")
    if video_id:
        transcript = entry.transcript
        if transcript is None:
            vt = await fetch_transcript(video_id)
            transcript = Transcript(
                entry_id=entry.id,
                language=vt.language,
                text=vt.text,
                segments=[{"start": s.start, "duration": s.duration, "text": s.text} for s in vt.segments],
            )
            session.add(transcript)
            await session.flush()
        else:
            vt = VideoTranscript(
                language=transcript.language,
                segments=[Segment(**s) for s in transcript.segments],
            )
        text = vt.as_timestamped_text()
        if len(text) < MIN_SOURCE_CHARS:
            raise TranscriptUnavailable("La transcription est vide")
        return "video", text, "transcription"

    # Article : on privilégie le texte complet, sinon on tente l'extraction, sinon le flux.
    if entry.extraction_status != "done" and entry.url and (entry.feed is None or entry.feed.extraction_mode != "none"):
        await run_extraction(session, entry)
    text = entry.content_text or html_to_text(entry.summary_html)
    if len(text) < MIN_SOURCE_CHARS:
        raise SummarizerError("Le contenu est trop court pour être résumé (l'article complet n'a pas pu être récupéré)")
    return "article", text, "article"


def _chunks(text: str, size: int = CHUNK_SIZE) -> list[str]:
    parts: list[str] = []
    while text:
        if len(text) <= size:
            parts.append(text)
            break
        cut = text.rfind("\n", size - 5000, size)
        if cut == -1:
            cut = size
        parts.append(text[:cut])
        text = text[cut:]
    return parts


async def summarize_entry(session: AsyncSession, entry_id: int, force: bool = False) -> AsyncIterator[SummaryEvent]:
    """Générateur d'événements : status → delta* → done, ou error."""
    result = await session.execute(
        select(Entry)
        .options(selectinload(Entry.feed), selectinload(Entry.transcript))
        .where(Entry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        yield SummaryEvent("error", {"message": "Article introuvable"})
        return

    if not force:
        existing = (
            await session.execute(
                select(Summary).where(Summary.entry_id == entry_id).order_by(Summary.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            yield SummaryEvent("done", {"summary": _summary_dict(existing), "cached": True})
            return

    settings = await get_all_settings(session)
    try:
        summarizer = build_summarizer(settings)
    except SummarizerError as exc:
        yield SummaryEvent("error", {"message": str(exc)})
        return

    yield SummaryEvent("status", {"message": "Préparation du contenu…"})
    try:
        kind, text, _ = await _prepare_source(session, entry)
    except (TranscriptUnavailable, SummarizerError) as exc:
        yield SummaryEvent("error", {"message": str(exc)})
        return
    await session.commit()

    language = settings.get("llm_language", "fr")
    system = prompts.system_prompt(language)
    total_usage = Usage()
    full_text: list[str] = []

    try:
        if len(text) > CHUNK_THRESHOLD:
            parts = _chunks(text)
            partials: list[str] = []
            for i, part in enumerate(parts, start=1):
                yield SummaryEvent("status", {"message": f"Résumé de la partie {i}/{len(parts)}…"})
                stream = summarizer.stream(system, prompts.chunk_prompt(entry.title, i, len(parts), part), max_tokens=2000)
                buf: list[str] = []
                async for piece in stream:
                    buf.append(piece)
                partials.append("".join(buf))
                total_usage.input_tokens += stream.usage.input_tokens
                total_usage.output_tokens += stream.usage.output_tokens
            yield SummaryEvent("status", {"message": "Fusion des résumés…"})
            user_prompt = prompts.merge_prompt(entry.title, entry.url, kind, partials)
        elif kind == "video":
            user_prompt = prompts.video_prompt(entry.title, entry.url, text)
        else:
            user_prompt = prompts.article_prompt(entry.title, entry.url, text)

        yield SummaryEvent("status", {"message": "Rédaction du résumé…"})
        stream = summarizer.stream(system, user_prompt, max_tokens=4000)
        async for piece in stream:
            full_text.append(piece)
            yield SummaryEvent("delta", {"text": piece})
        total_usage.input_tokens += stream.usage.input_tokens
        total_usage.output_tokens += stream.usage.output_tokens
    except SummarizerError as exc:
        yield SummaryEvent("error", {"message": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("échec du résumé pour l'entrée %s", entry_id)
        yield SummaryEvent("error", {"message": f"Erreur inattendue: {exc}"})
        return

    summary = Summary(
        entry_id=entry.id,
        kind=kind,
        provider=summarizer.name,
        model=summarizer.model,
        prompt_version=prompts.PROMPT_VERSION,
        text_md="".join(full_text).strip(),
        input_tokens=total_usage.input_tokens,
        output_tokens=total_usage.output_tokens,
    )
    session.add(summary)
    entry.updated_at = datetime.now(UTC)  # déclenche la sync delta côté clients
    await session.commit()
    await session.refresh(summary)
    yield SummaryEvent("done", {"summary": _summary_dict(summary), "cached": False})


def _summary_dict(s: Summary) -> dict:
    return {
        "id": s.id,
        "entry_id": s.entry_id,
        "kind": s.kind,
        "provider": s.provider,
        "model": s.model,
        "text_md": s.text_md,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "created_at": s.created_at.isoformat(),
    }
