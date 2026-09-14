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
