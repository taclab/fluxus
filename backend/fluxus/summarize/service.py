"""Orchestration d'un résumé : choix de la source, découpage, appel du fournisseur, persistance."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_engine, session_scope
from ..extract.clean import html_to_text
from ..extract.jobs import run_extraction
from ..models import Entry, LlmUsage, Summary, Transcript
from ..settings_store import get_all_settings
from ..youtube.transcript import Segment, TranscriptUnavailable, VideoTranscript, fetch_transcript
from . import prompts
from .estimate import CHUNK_OUTPUT_TOKENS, CHUNK_THRESHOLD, estimate_input_tokens
from .estimate import chunks as _chunks
from .providers import SummarizerError, Usage, build_summarizer

log = logging.getLogger(__name__)

MIN_SOURCE_CHARS = 200
# Verrou consultatif à deux clés (espace distinct des verrous à clé unique) : une génération par article.
GENERATION_LOCK_KEY = 727005
ALREADY_RUNNING = "Un résumé de cet article est déjà en cours de génération"


@asynccontextmanager
async def generation_lock(entry_id: int) -> AsyncIterator[bool]:
    """Une seule génération à la fois par article (manuelle ou automatique) : sans lui, un clic sur
    « Résumer » pendant le job automatique du même article payait deux générations. Renvoie faux si
    une autre génération tient le verrou. Connexion dédiée : le bloc valide sa session en cours de route."""
    async with get_engine().connect() as conn:
        acquired = bool((await conn.execute(select(func.pg_try_advisory_lock(GENERATION_LOCK_KEY, entry_id)))).scalar_one())
        await conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await conn.execute(select(func.pg_advisory_unlock(GENERATION_LOCK_KEY, entry_id)))
                    await conn.commit()
                except BaseException:
                    # Annulation pendant la libération : un verrou de session survivrait au retour de la
                    # connexion dans le pool. On la ferme, le serveur le relâche.
                    await conn.invalidate()
                    raise


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
            # Rattachée à l'entrée : une seconde préparation (estimation du budget puis résumé) la réutilise.
            entry.transcript = Transcript(
                entry_id=entry.id,
                language=vt.language,
                text=vt.text,
                segments=[{"start": s.start, "duration": s.duration, "text": s.text} for s in vt.segments],
            )
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


async def prepare_source(session: AsyncSession, entry_id: int) -> tuple[str, str]:
    """(nature, texte) réellement envoyés au modèle (extraction ou transcription comprises, sans appel
    LLM) : base de l'estimation d'un résumé automatique avant la réservation de budget. À repasser à
    `summarize_entry(source=…)` : une seconde préparation pourrait donner un autre texte que celui réservé."""
    entry = (
        await session.execute(select(Entry).options(selectinload(Entry.feed), selectinload(Entry.transcript)).where(Entry.id == entry_id))
    ).scalar_one()
    kind, text, _ = await _prepare_source(session, entry)
    return kind, text


async def _abandon(usage_id: int, tokens: int) -> None:
    """Solde, sur une session neuve, une ligne restée `reserved` (génération interrompue)."""
    async with session_scope() as fresh:
        await fresh.execute(update(LlmUsage).where(LlmUsage.id == usage_id, LlmUsage.status == "reserved").values(status="failed", tokens=tokens))


async def _settle(session: AsyncSession, usage: LlmUsage | None, *, status: str, tokens: int, entry_id: int, origin: str, smart_feed_id: int | None) -> None:
    """Solde la ligne du journal de consommation (réservée avant l'appel pour les résumés automatiques,
    créée ici pour les résumés manuels). Un appel échoué après consommation est compté quand même."""
    if usage is None:
        if tokens == 0 and status != "done":
            return  # rien n'a été appelé : pas de ligne
        usage = LlmUsage(entry_id=entry_id, smart_feed_id=smart_feed_id, origin=origin)
        session.add(usage)
    usage.status = status
    usage.tokens = tokens
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("journal de consommation LLM non enregistré pour l'entrée %s", entry_id)
        await session.rollback()


async def summarize_entry(
    session: AsyncSession,
    entry_id: int,
    force: bool = False,
    *,
    origin: str = "manual",
    smart_feed_id: int | None = None,
    model_override: str | None = None,
    usage: LlmUsage | None = None,
    max_output_tokens: int | None = None,
    source: tuple[str, str] | None = None,
    style: str | None = None,
    question: str | None = None,
    deep: bool = False,
) -> AsyncIterator[SummaryEvent]:
    """Générateur d'événements : status → delta* → done, ou error.

    `style` : type de résumé (`prompts.STYLES`), sinon le type par défaut des réglages ; `question`
    (type « question ») : question libre du lecteur ; `deep` : modèle approfondi (réglage
    `llm_deep_model`, sinon le modèle courant) à l'effort maximal. Un résumé existant du même type
    (et de la même question) est renvoyé tel quel sauf `force`.

    `origin="auto"` et `smart_feed_id` marquent un résumé déclenché par un flux intelligent ;
    `model_override` remplace le modèle des réglages (modèle économique des résumés automatiques) ;
    `usage` est la réservation de budget déjà committée à solder (sinon une ligne est réservée ici, avant l'appel) ;
    `max_output_tokens` borne la sortie du fournisseur au solde réservé ;
    `source` est le résultat de `prepare_source` sur lequel la réservation a été estimée.
    """
    async with generation_lock(entry_id) as acquired:
        if not acquired:
            if usage is not None:
                await _settle(session, usage, status="done", tokens=0, entry_id=entry_id, origin=origin, smart_feed_id=smart_feed_id)
            yield SummaryEvent("error", {"message": ALREADY_RUNNING})
            return
        run = _Run(usage=usage)
        try:
            options = _Options(style=style, question=question, deep=deep)
            async for event in _generate(run, session, entry_id, force, origin, smart_feed_id, model_override, max_output_tokens, source, options):
                yield event
        finally:
            # Génération interrompue sans être soldée (client SSE parti, job annulé à son délai) : la
            # session de la requête n'est plus sûre, la ligne est soldée sur une session neuve, à l'abri
            # de l'annulation en cours. L'appel a pu être facturé : on compte ce qu'on en sait.
            if not run.settled and run.usage is not None and run.usage.id is not None:
                await asyncio.shield(_abandon(run.usage.id, run.known_tokens()))


@dataclass
class _Options:
    """Demande du lecteur : type, question libre, mode approfondi."""

    style: str | None = None
    question: str | None = None
    deep: bool = False


@dataclass
class _Run:
    """État d'une génération partagé avec son enveloppe : la ligne du journal, soldée ou non."""

    usage: LlmUsage | None
    settled: bool = False
    known_tokens: Callable[[], int] = lambda: 0


async def _generate(
    run: _Run,
    session: AsyncSession,
    entry_id: int,
    force: bool,
    origin: str,
    smart_feed_id: int | None,
    model_override: str | None,
    max_output_tokens: int | None,
    source: tuple[str, str] | None,
    options: _Options,
) -> AsyncIterator[SummaryEvent]:
    usage = run.usage

    async def settle(status: str, tokens: int) -> None:
        run.settled = True
        await _settle(session, run.usage, status=status, tokens=tokens, entry_id=entry_id, origin=origin, smart_feed_id=smart_feed_id)

    result = await session.execute(
        select(Entry)
        .options(selectinload(Entry.feed), selectinload(Entry.transcript))
        .where(Entry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        await settle("failed", 0)
        yield SummaryEvent("error", {"message": "Article introuvable"})
        return

    settings = await get_all_settings(session)
    question = (options.question or "").strip()[: prompts.MAX_QUESTION_CHARS] or None
    default_style = settings.get("llm_default_style")
    if default_style == "question":  # sans question, ce n'est pas un type par défaut possible
        default_style = None
    style = "question" if question else prompts.style_or_default(options.style or default_style)
    if style == "question" and question is None:
        await settle("failed", 0)
        yield SummaryEvent("error", {"message": "Question vide"})
        return
    style_cap = prompts.STYLES[style].max_output_tokens
    out_cap = max(1, min(style_cap, max_output_tokens)) if max_output_tokens is not None else style_cap

    if not force:
        same = select(Summary).where(Summary.entry_id == entry_id, Summary.style == style)
        if question is not None:
            same = same.where(Summary.question == question)
        existing = (await session.execute(same.order_by(Summary.created_at.desc()).limit(1))).scalar_one_or_none()
        if existing is not None:
            if usage is not None:
                await settle("done", 0)  # réservation libérée : rien n'a été appelé
            yield SummaryEvent("done", {"summary": _summary_dict(existing), "cached": True})
            return

    if model_override:
        settings = {**settings, "llm_model": model_override}
    if options.deep:
        settings = {**settings, "llm_model": (settings.get("llm_deep_model") or "").strip() or settings.get("llm_model", ""), "llm_effort": "high"}
    try:
        summarizer = build_summarizer(settings)
    except SummarizerError as exc:
        await settle("failed", 0)
        yield SummaryEvent("error", {"message": str(exc)})
        return

    yield SummaryEvent("status", {"message": "Préparation du contenu…"})
    try:
        if source is not None:
            kind, text = source
        else:
            kind, text, _ = await _prepare_source(session, entry)
        if usage is None:
            # Résumé manuel : ligne réservée et validée avant tout appel. Si le client se déconnecte
            # ou si le processus meurt en cours de génération, l'appel facturé laisse une trace.
            usage = run.usage = LlmUsage(entry_id=entry_id, smart_feed_id=smart_feed_id, origin=origin, status="reserved", estimated_tokens=estimate_input_tokens(text) + out_cap)
            session.add(usage)
        await session.commit()
    except (TranscriptUnavailable, SummarizerError) as exc:
        await settle("failed", 0)
        yield SummaryEvent("error", {"message": str(exc)})
        return
    except Exception as exc:
        log.exception("préparation du résumé en échec pour l'entrée %s", entry_id)
        await settle("failed", 0)
        yield SummaryEvent("error", {"message": f"Préparation impossible: {exc}"})
        return

    language = settings.get("llm_language", "fr")
    system = prompts.system_prompt(language, settings.get("llm_instructions", ""))
    total_usage = Usage()
    full_text: list[str] = []
    stream = None

    def known_tokens() -> int:
        """Tokens réellement consommés, y compris ceux du flux interrompu ; sinon l'estimation réservée."""
        partial = (stream.usage.input_tokens + stream.usage.output_tokens) if stream is not None else 0
        return total_usage.input_tokens + total_usage.output_tokens + partial or (usage.estimated_tokens if usage else 0)

    run.known_tokens = known_tokens

    try:
        if len(text) > CHUNK_THRESHOLD:
            parts = _chunks(text)
            partials: list[str] = []
            for i, part in enumerate(parts, start=1):
                yield SummaryEvent("status", {"message": f"Résumé de la partie {i}/{len(parts)}…"})
                stream = summarizer.stream(system, prompts.chunk_prompt(entry.title, i, len(parts), part), max_tokens=min(CHUNK_OUTPUT_TOKENS, out_cap))
                buf: list[str] = []
                async for piece in stream:
                    buf.append(piece)
                partials.append("".join(buf))
                total_usage.input_tokens += stream.usage.input_tokens
                total_usage.output_tokens += stream.usage.output_tokens
                stream = None
            yield SummaryEvent("status", {"message": "Fusion des résumés…"})
            user_prompt = prompts.merge_prompt(entry.title, entry.url, kind, partials, style, question)
        elif kind == "video":
            user_prompt = prompts.video_prompt(entry.title, entry.url, text, style, question)
        else:
            user_prompt = prompts.article_prompt(entry.title, entry.url, text, style, question)

        yield SummaryEvent("status", {"message": "Rédaction du résumé…"})
        stream = summarizer.stream(system, user_prompt, max_tokens=out_cap)
        async for piece in stream:
            full_text.append(piece)
            yield SummaryEvent("delta", {"text": piece})
        total_usage.input_tokens += stream.usage.input_tokens
        total_usage.output_tokens += stream.usage.output_tokens
        stream = None
    except SummarizerError as exc:
        # Échec après consommation éventuelle : compté avec les tokens connus, sinon l'estimation réservée.
        await settle("failed", known_tokens())
        yield SummaryEvent("error", {"message": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("échec du résumé pour l'entrée %s", entry_id)
        await settle("failed", known_tokens())
        yield SummaryEvent("error", {"message": f"Erreur inattendue: {exc}"})
        return

    summary = Summary(
        entry_id=entry.id,
        kind=kind,
        provider=summarizer.name,
        model=summarizer.model[:100],
        prompt_version=prompts.PROMPT_VERSION,
        style=style,
        question=question,
        text_md="".join(full_text).strip(),
        input_tokens=total_usage.input_tokens,
        output_tokens=total_usage.output_tokens,
        origin=origin,
        smart_feed_id=smart_feed_id,
    )
    spent = total_usage.input_tokens + total_usage.output_tokens
    try:
        session.add(summary)
        entry.updated_at = datetime.now(UTC)  # déclenche la sync delta côté clients
        await session.commit()
        await session.refresh(summary)
    except Exception as exc:
        log.exception("enregistrement du résumé en échec pour l'entrée %s", entry_id)
        await session.rollback()
        await settle("failed", spent)
        # Le texte a été produit et streamé : on le renvoie quand même, sans identifiant.
        yield SummaryEvent("error", {"message": f"Résumé produit mais non enregistré: {exc}"})
        return
    await settle("done", spent)
    yield SummaryEvent("done", {"summary": _summary_dict(summary), "cached": False})


def _summary_dict(s: Summary) -> dict:
    return {
        "id": s.id,
        "entry_id": s.entry_id,
        "kind": s.kind,
        "style": s.style,
        "question": s.question,
        "provider": s.provider,
        "model": s.model,
        "text_md": s.text_md,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "created_at": s.created_at.isoformat(),
    }
