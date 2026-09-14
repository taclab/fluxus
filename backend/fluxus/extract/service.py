"""Extraction de l'article complet : HTTP + trafilatura, repli navigateur headless."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import trafilatura
from trafilatura.settings import use_config

from ..config import get_settings
from ..fetcher.http import assert_public_url, make_client
from .clean import html_to_text, sanitize_html

log = logging.getLogger(__name__)

# Domaines dont on sait que le contenu n'est disponible qu'après exécution du JavaScript.
JS_HEAVY_DOMAINS = (
    "medium.com", "substack.com", "twitter.com", "x.com", "threads.net", "bsky.app",
    "linkedin.com", "notion.site",
)

# Si l'extraction HTTP renvoie moins que ce ratio du contenu du flux, on tente le navigateur.
MIN_GAIN_RATIO = 1.2
MIN_TEXT_CHARS = 400

_traf_config = use_config()
_traf_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")


class ExtractionError(Exception):
    pass


@dataclass
class Extracted:
    html: str
    text: str
    method: str  # http | browser
    title: str | None = None


def _extract_from_html(page_html: str, url: str) -> tuple[str, str] | None:
    result = trafilatura.extract(
        page_html,
        url=url,
        output_format="html",
        include_images=True,
        include_links=True,
        include_tables=True,
        include_formatting=True,
        favor_recall=True,
        config=_traf_config,
    )
    if not result:
        return None
    cleaned = sanitize_html(result, base_url=url)
    text = html_to_text(cleaned)
    if len(text) < 80:
        return None
    return cleaned, text


async def _fetch_http(url: str) -> str:
    async with make_client(
        headers={
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
    ) as client:
        response = await client.get(url)
    response.raise_for_status()
    ctype = response.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype and not response.text.lstrip().startswith("<"):
        raise ExtractionError(f"Contenu non HTML ({ctype or 'type inconnu'})")
    return response.text


async def _fetch_browser(url: str) -> str:
    settings = get_settings()
    # trust_env=False : le service navigateur est sur le réseau interne, jamais via un proxy HTTP.
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post(f"{settings.browser_url}/render", json={"url": url})
    if response.status_code != 200:
        detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise ExtractionError(f"Navigateur: {detail}")
    return response.json()["html"]


async def extract_article(url: str, mode: str = "auto", reference_text_len: int = 0) -> Extracted:
    """`mode` : http | browser | auto."""
    assert_public_url(url)
    if mode not in ("http", "browser", "auto"):
        mode = "auto"

    http_result: tuple[str, str] | None = None
    http_error: str | None = None
    force_browser = mode == "browser" or (mode == "auto" and any(d in url for d in JS_HEAVY_DOMAINS))

    if not force_browser:
        try:
            page = await _fetch_http(url)
            http_result = _extract_from_html(page, url)
        except (httpx.HTTPError, ExtractionError) as exc:
            http_error = str(exc)
            log.debug("extraction HTTP échouée pour %s: %s", url, exc)

        good_enough = http_result is not None and (
            len(http_result[1]) >= MIN_TEXT_CHARS
            and len(http_result[1]) >= reference_text_len * MIN_GAIN_RATIO
        )
        if mode == "http" or good_enough:
            if http_result is None:
                raise ExtractionError(http_error or "Aucun contenu exploitable")
            return Extracted(html=http_result[0], text=http_result[1], method="http")

    # Repli navigateur.
    try:
        page = await _fetch_browser(url)
        browser_result = _extract_from_html(page, url)
    except (httpx.HTTPError, ExtractionError) as exc:
        if http_result is not None:
            return Extracted(html=http_result[0], text=http_result[1], method="http")
        raise ExtractionError(f"Repli navigateur impossible: {exc}") from exc

    if browser_result is None:
        if http_result is not None:
            return Extracted(html=http_result[0], text=http_result[1], method="http")
        raise ExtractionError(http_error or "Aucun contenu exploitable après rendu")

    if http_result is not None and len(http_result[1]) > len(browser_result[1]):
        return Extracted(html=http_result[0], text=http_result[1], method="http")
    return Extracted(html=browser_result[0], text=browser_result[1], method="browser")
