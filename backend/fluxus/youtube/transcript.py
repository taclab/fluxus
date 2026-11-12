"""Récupération des sous-titres YouTube (aucun repli audio : sans sous-titres, on échoue proprement)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class TranscriptUnavailable(Exception):
    pass


@dataclass
class Segment:
    start: float
    duration: float
    text: str


@dataclass
class VideoTranscript:
    language: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    def as_timestamped_text(self, bucket_seconds: int = 30) -> str:
        """Regroupe les segments par tranches pour donner des repères temporels au modèle."""
        lines: list[str] = []
        current_bucket = -1
        buffer: list[str] = []
        for seg in self.segments:
            bucket = int(seg.start // bucket_seconds)
            if bucket != current_bucket:
                if buffer:
                    lines.append(" ".join(buffer))
                current_bucket = bucket
                buffer = [f"[{_fmt(seg.start)}]"]
            buffer.append(seg.text.strip())
        if buffer:
            lines.append(" ".join(buffer))
        return "\n".join(lines)


def _fmt(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:d}:{sec:02d}"


# Une page de vidéo pèse 1 à 2 Mo, des sous-titres de plusieurs heures quelques Mo.
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _bounded_session():
    """Session `requests` bornée en durée et en taille : la bibliothèque ne pose ni délai ni limite,
    et un appel figé dans un thread ne peut pas être annulé. La bibliothèque sort hors de `safe_get`
    (elle ne joint que YouTube) ; ces bornes en tiennent lieu."""
    import requests

    class BoundedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", (10, 30))
            kwargs["stream"] = True  # corps lu ici, par morceaux, au lieu d'être chargé d'un bloc
            response = super().request(*args, **kwargs)
            body = bytearray()
            for piece in response.iter_content(64 * 1024):
                body += piece
                if len(body) > MAX_RESPONSE_BYTES:
                    response.close()
                    raise TranscriptUnavailable(f"Réponse de plus de {MAX_RESPONSE_BYTES // (1024 * 1024)} Mo refusée")
            response._content = bytes(body)  # ce que `response.text` et `.json()` liront ensuite
            return response

    return BoundedSession()


def _fetch_sync(video_id: str, languages: list[str]) -> VideoTranscript:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (  # type: ignore[attr-defined]
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    try:
        try:
            api = YouTubeTranscriptApi(http_client=_bounded_session())
        except TypeError:  # API < 1.0 : pas de client injectable
            api = YouTubeTranscriptApi()
        if hasattr(api, "list"):
            # API >= 1.0
            transcript_list = api.list(video_id)
            try:
                transcript = transcript_list.find_transcript(languages)
            except NoTranscriptFound:
                # Aucune langue préférée : on prend la première disponible (souvent auto-générée).
                transcript = next(iter(transcript_list))
            fetched = transcript.fetch()
            segments = [Segment(start=s.start, duration=s.duration, text=s.text) for s in fetched]
            return VideoTranscript(language=transcript.language_code, segments=segments)
        # API < 1.0
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)  # type: ignore[attr-defined]
        try:
            transcript = transcript_list.find_transcript(languages)
        except NoTranscriptFound:
            transcript = next(iter(transcript_list))
        data = transcript.fetch()
        segments = [Segment(start=d["start"], duration=d.get("duration", 0), text=d["text"]) for d in data]
        return VideoTranscript(language=transcript.language_code, segments=segments)
    except (TranscriptsDisabled, VideoUnavailable) as exc:
        raise TranscriptUnavailable("Cette vidéo n'a pas de sous-titres disponibles") from exc
    except StopIteration as exc:
        raise TranscriptUnavailable("Cette vidéo n'a aucun sous-titre") from exc
    except TranscriptUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TranscriptUnavailable(f"Sous-titres inaccessibles: {exc}") from exc


async def fetch_transcript(video_id: str, languages: list[str] | None = None) -> VideoTranscript:
    langs = languages or ["fr", "fr-FR", "en", "en-US", "en-GB"]
    return await asyncio.to_thread(_fetch_sync, video_id, langs)
