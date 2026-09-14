"""Fournisseurs LLM derrière une interface commune de streaming."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

log = logging.getLogger(__name__)


class SummarizerError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class StreamResult:
    """Itérateur de texte qui expose l'usage une fois le flux consommé."""

    def __init__(self, gen: AsyncIterator[str], usage: Usage) -> None:
        self._gen = gen
        self.usage = usage

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen


class Summarizer:
    name = "base"

    def __init__(self, api_key: str, model: str, effort: str = "low", base_url: str | None = None) -> None:
        if not api_key:
            raise SummarizerError("Aucune clé API configurée pour ce fournisseur (Paramètres → Résumés)")
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.base_url = base_url or None

    def stream(self, system: str, user: str, max_tokens: int = 4000) -> StreamResult:
        raise NotImplementedError


class AnthropicSummarizer(Summarizer):
    name = "anthropic"

    def stream(self, system: str, user: str, max_tokens: int = 4000) -> StreamResult:
        import anthropic

        usage = Usage()
        client = anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.base_url, max_retries=2)

        async def gen() -> AsyncIterator[str]:
            kwargs: dict = dict(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            # Les modèles 4.6+ acceptent le thinking adaptatif et le niveau d'effort ;
            # Haiku 4.5 et les anciens modèles non.
            if not self.model.startswith("claude-haiku"):
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": self.effort}
            try:
                async with client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final = await stream.get_final_message()
            except anthropic.AuthenticationError as exc:
                raise SummarizerError("Clé API Anthropic refusée") from exc
            except anthropic.RateLimitError as exc:
                raise SummarizerError("Limite de débit Anthropic atteinte, réessayez dans une minute") from exc
            except anthropic.NotFoundError as exc:
                raise SummarizerError(f"Modèle inconnu: {self.model}") from exc
            except anthropic.BadRequestError as exc:
                raise SummarizerError(f"Requête refusée par l'API: {exc.message}") from exc
            except anthropic.APIStatusError as exc:
                raise SummarizerError(f"Erreur API Anthropic ({exc.status_code})") from exc
            except anthropic.APIConnectionError as exc:
                raise SummarizerError("Impossible de joindre l'API Anthropic") from exc
            usage.input_tokens = final.usage.input_tokens
            usage.output_tokens = final.usage.output_tokens
            if final.stop_reason == "refusal":
                raise SummarizerError("Le modèle a refusé de résumer ce contenu")
            if final.stop_reason == "max_tokens":
                yield "\n\n*(résumé tronqué : limite de longueur atteinte)*"

        return StreamResult(gen(), usage)


class OpenAISummarizer(Summarizer):
    name = "openai"

    def stream(self, system: str, user: str, max_tokens: int = 4000) -> StreamResult:
        import openai

        usage = Usage()
        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, max_retries=2)

        async def gen() -> AsyncIterator[str]:
            try:
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_completion_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                async for chunk in stream:
                    if chunk.usage:
                        usage.input_tokens = chunk.usage.prompt_tokens or 0
                        usage.output_tokens = chunk.usage.completion_tokens or 0
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            except openai.AuthenticationError as exc:
                raise SummarizerError("Clé API OpenAI refusée") from exc
            except openai.RateLimitError as exc:
                raise SummarizerError("Limite de débit OpenAI atteinte") from exc
            except openai.NotFoundError as exc:
                raise SummarizerError(f"Modèle inconnu: {self.model}") from exc
            except openai.APIStatusError as exc:
                raise SummarizerError(f"Erreur API OpenAI ({exc.status_code})") from exc
            except openai.APIConnectionError as exc:
                raise SummarizerError("Impossible de joindre l'API OpenAI") from exc

        return StreamResult(gen(), usage)


def build_summarizer(settings: dict[str, str]) -> Summarizer:
    provider = settings.get("llm_provider", "anthropic")
    model = settings.get("llm_model") or ("claude-opus-5" if provider == "anthropic" else "gpt-4.1-mini")
    effort = settings.get("llm_effort") or "low"
    if provider == "anthropic":
        return AnthropicSummarizer(settings.get("llm_api_key_anthropic", ""), model, effort)
    if provider == "openai":
        return OpenAISummarizer(
            settings.get("llm_api_key_openai", ""), model, effort, base_url=settings.get("openai_base_url") or None
        )
    raise SummarizerError(f"Fournisseur inconnu: {provider}")
