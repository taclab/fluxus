"""Découverte de flux à partir de l'URL d'un site."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import html as lxml_html

from .http import UnsafeURLError, assert_public_url_async, make_client, safe_get

FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
    "application/xml",
    "text/xml",
}

COMMON_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml", "/feeds/posts/default")

YOUTUBE_CHANNEL_RE = re.compile(r"youtube\.com/(?:channel/(UC[\w-]+)|@([\w.-]+)|c/([\w.-]+)|user/([\w.-]+))")


@dataclass
class DiscoveredFeed:
    url: str
    title: str


def _looks_like_feed(content_type: str, body: bytes) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    if ct in FEED_TYPES and ct != "application/json":
        return True
    head = body[:2000].lstrip().lower()
    return head.startswith(b"<?xml") and (b"<rss" in head or b"<feed" in head) or head.startswith(b"<rss") or head.startswith(b"<feed")


async def _youtube_feed(url: str, client: httpx.AsyncClient) -> list[DiscoveredFeed]:
    m = YOUTUBE_CHANNEL_RE.search(url)
    if not m:
        return []
    channel_id = m.group(1)
    if not channel_id:
        # Handle @nom ou /c/nom : il faut lire la page pour trouver l'identifiant du canal.
        # Depuis l'UE, YouTube redirige vers consent.youtube.com sans ce cookie (refus des
        # cookies non essentiels) : la page reçue ne contiendrait alors aucun identifiant.
        client.cookies.set("SOCS", "CAI", domain=".youtube.com")
        response = await safe_get(client, url)
        text = response.text
        # L'URL canonique désigne la chaîne de la page ; `"channelId"` peut citer une autre chaîne.
        found = (
            re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]+)"', text)
            or re.search(r"channel_id=(UC[\w-]+)", text)
            or re.search(r'"channelId":"(UC[\w-]+)"', text)
        )
        if not found:
            return []
        channel_id = found.group(1)
    return [
        DiscoveredFeed(
            url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            title="Chaîne YouTube",
        )
    ]


async def discover_feeds(url: str) -> list[DiscoveredFeed]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    await assert_public_url_async(url)
    async with make_client(headers={"Accept": "text/html,application/xhtml+xml,application/xml,*/*"}) as client:
        yt = await _youtube_feed(url, client)
        if yt:
            return yt
        response = await safe_get(client, url)
        response.raise_for_status()
        final_url = str(response.url)
        if _looks_like_feed(response.headers.get("content-type", ""), response.content):
            return [DiscoveredFeed(url=final_url, title="")]

        found: list[DiscoveredFeed] = []
        try:
            doc = lxml_html.fromstring(response.content)
        except Exception:  # noqa: BLE001
            doc = None
        if doc is not None:
            for link in doc.iter("link"):
                rel = (link.get("rel") or "").lower()
                typ = (link.get("type") or "").lower()
                href = link.get("href")
                if "alternate" in rel and typ in FEED_TYPES and href:
                    found.append(DiscoveredFeed(url=urljoin(final_url, href), title=link.get("title") or ""))
        if found:
            seen: set[str] = set()
            unique = []
            for f in found:
                if f.url not in seen:
                    seen.add(f.url)
                    unique.append(f)
            return unique

        # Aucune balise <link> : on tente les chemins habituels.
        parts = urlsplit(final_url)
        base = f"{parts.scheme}://{parts.netloc}"
        for path in COMMON_PATHS:
            try:
                candidate = await safe_get(client, base + path)
            except (httpx.HTTPError, UnsafeURLError):
                continue
            if candidate.status_code == 200 and _looks_like_feed(
                candidate.headers.get("content-type", ""), candidate.content
            ):
                return [DiscoveredFeed(url=str(candidate.url), title="")]
        return []
