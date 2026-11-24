from fluxus.summarize import prompts
from fluxus.summarize.providers import SummarizerError, build_summarizer
from fluxus.summarize.service import _chunks
from fluxus.youtube.transcript import Segment, VideoTranscript


def test_chunks_cut_on_newlines_and_cover_everything():
    text = "\n".join(f"ligne {i} " + "x" * 90 for i in range(3000))
    parts = _chunks(text, size=50_000)
    assert len(parts) > 1
    assert "".join(parts) == text
    for p in parts[:-1]:
        assert len(p) <= 50_000


def test_build_summarizer_requires_key():
    try:
        build_summarizer({"llm_provider": "anthropic", "llm_api_key_anthropic": ""})
    except SummarizerError as exc:
        assert "clé" in str(exc).lower()
    else:
        raise AssertionError("une clé vide doit lever SummarizerError")


def test_build_summarizer_anthropic_defaults():
    s = build_summarizer({"llm_provider": "anthropic", "llm_api_key_anthropic": "sk-test", "llm_model": ""})
    assert s.name == "anthropic"
    assert s.model == "claude-opus-5"
    assert s.effort == "low"


def test_transcript_timestamped_buckets():
    vt = VideoTranscript(language="fr", segments=[Segment(0, 5, "bonjour"), Segment(10, 5, "à tous"), Segment(65, 5, "suite")])
    text = vt.as_timestamped_text(bucket_seconds=30)
    assert text.splitlines() == ["[0:00] bonjour à tous", "[1:05] suite"]
    assert vt.text == "bonjour à tous suite"


def test_prompts_mention_structure():
    assert "### L'essentiel" in prompts.article_prompt("T", None, "texte")
    assert "[mm:ss]" in prompts.video_prompt("T", None, "texte")
    assert "français" in prompts.system_prompt("fr")


def test_style_prompts_and_user_text_limits():
    from fluxus.summarize.estimate import PROMPT_OVERHEAD_TOKENS, text_tokens

    for name, style in prompts.STYLES.items():
        assert style.article and style.video, name
        assert style.article in prompts.article_prompt("T", None, "texte", name)
        assert style.video in prompts.merge_prompt("T", None, "video", ["a"], name)
    assert prompts.style_or_default("inconnu") == "standard"
    # Consignes personnelles et question tronquées : l'estimation les couvre à leur plafond.
    system = prompts.system_prompt("fr", "x" * 5000)
    assert "x" * prompts.MAX_INSTRUCTIONS_CHARS in system and "x" * (prompts.MAX_INSTRUCTIONS_CHARS + 1) not in system
    # Pire cas : consignes et question en idéogrammes, type le plus long, enrobage de l'appel.
    worst = max(
        text_tokens(prompts.system_prompt("fr", "語" * 5000))
        + text_tokens(builder("Titre", "https://exemple.org/article", "", name, "語" * 5000))
        for name in prompts.STYLES
        for builder in (prompts.article_prompt, prompts.video_prompt)
    )
    assert worst <= PROMPT_OVERHEAD_TOKENS, worst


# --- Audit du 12/11, constat 29 : l'estimation couvre ce qui est réellement consommé ------------------


def test_estimate_latin_text_is_about_three_chars_per_token():
    from fluxus.summarize.estimate import PROMPT_OVERHEAD_TOKENS, estimate_input_tokens

    assert estimate_input_tokens("Un article en français, accentué. " * 100) == 3400 // 3 + PROMPT_OVERHEAD_TOKENS


def test_estimate_does_not_underestimate_cjk_and_cyrillic():
    from fluxus.summarize.estimate import text_tokens

    assert text_tokens("人工知能の記事" * 1000) >= 7000  # l'ancien calcul donnait 2333
    assert text_tokens("статья" * 1000) >= 6000


def test_estimate_covers_intermediate_calls_of_chunked_mode():
    from fluxus.summarize.estimate import CHUNK_THRESHOLD, PROMPT_OVERHEAD_TOKENS, chunks, estimate_input_tokens

    text = ("mot " * 30 + "\n") * 2500  # 302 500 caractères : mode découpé
    assert len(text) > CHUNK_THRESHOLD
    parts = len(chunks(text))
    assert parts >= 3
    # Chaque partie : consignes, résumé partiel produit (2000) puis relu par la fusion (2000).
    assert estimate_input_tokens(text) == len(text) // 3 + PROMPT_OVERHEAD_TOKENS + parts * (PROMPT_OVERHEAD_TOKENS + 4000)


# --- Constat 33 : réponses bornées hors safe_get --------------------------------------------------------


def _serve(body: bytes, status: int = 200, content_type: str = "application/json"):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_transcript_http_session_is_bounded_in_size(monkeypatch):
    import pytest

    from fluxus.youtube import transcript

    server, url = _serve(b"x" * 5000, content_type="text/plain")
    try:
        session = transcript._bounded_session()
        session.trust_env = False
        assert session.get(url).text == "x" * 5000
        monkeypatch.setattr(transcript, "MAX_RESPONSE_BYTES", 1000)
        with pytest.raises(transcript.TranscriptUnavailable, match="refusée"):
            session.get(url)
    finally:
        server.shutdown()


async def test_renderer_response_is_bounded_in_size(monkeypatch):
    import json

    import pytest

    from fluxus.config import get_settings
    from fluxus.extract import service

    page = "<html><body>" + "contenu " * 500 + "</body></html>"
    server, url = _serve(json.dumps({"html": page, "final_url": "https://exemple.fr/"}).encode())
    monkeypatch.setattr(get_settings(), "browser_url", url)
    try:
        assert await service._fetch_browser("https://exemple.fr/") == page
        monkeypatch.setattr(service, "MAX_RENDERED_BYTES", 1000)
        with pytest.raises(service.ExtractionError, match="refusée"):
            await service._fetch_browser("https://exemple.fr/")
    finally:
        server.shutdown()
    server, url = _serve(json.dumps({"detail": "Adresse privée refusée"}).encode(), status=400)
    monkeypatch.setattr(get_settings(), "browser_url", url)
    try:
        with pytest.raises(service.ExtractionError, match="Adresse privée refusée"):
            await service._fetch_browser("https://exemple.fr/")
    finally:
        server.shutdown()
