"""Transformation d'un flux brut (feedparser) en entrées normalisées, sans accès base."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser

from ..extract.clean import sanitize_html

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "xtor", "igshid",
}

YOUTUBE_ID_RE = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


@dataclass
class ParsedEntry:
    guid: str
    url: str | None
    title: str
    author: str | None
    published_at: datetime
    summary_html: str | None
    hash: str
    meta: dict = field(default_factory=dict)


@dataclass
class ParsedFeed:
    title: str
    site_url: str | None
    description: str | None
    icon_url: str | None
    entries: list[ParsedEntry]
    is_youtube: bool
    bozo_error: str | None = None


def normalize_url(url: str | None, base: str | None = None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _struct_to_datetime(struct) -> datetime | None:
    if not struct:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(struct) - time.timezone, tz=UTC)
    except (OverflowError, ValueError):
        return None


def _entry_content(entry) -> str | None:
    """Prend le contenu le plus riche disponible : content[] puis summary."""
    contents = entry.get("content") or []
    best = ""
    for c in contents:
        value = c.get("value") or ""
        if len(value) > len(best):
            best = value
    if not best:
        best = entry.get("summary") or entry.get("description") or ""
    return best or None


def _youtube_id(url: str | None, entry) -> str | None:
    if entry.get("yt_videoid"):
        return entry["yt_videoid"]
    if url:
        m = YOUTUBE_ID_RE.search(url)
        if m:
            return m.group(1)
    return None


IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.IGNORECASE)


def _looks_like_image(m: dict) -> bool:
    medium = str(m.get("medium", ""))
    mtype = str(m.get("type", ""))
    if medium.startswith("image") or mtype.startswith("image"):
        return True
    # Beaucoup de flux (Le Monde…) donnent un media:content sans type : on se fie à l'extension.
    return not medium and not mtype and bool(IMAGE_EXT_RE.search(str(m.get("url", ""))))


def _cover_image(entry, summary_html: str | None) -> str | None:
    for m in entry.get("media_thumbnail") or []:
        if m.get("url"):
            return m["url"]
    for m in entry.get("media_content") or []:
        if m.get("url") and _looks_like_image(m):
            return m["url"]
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href")
    if summary_html:
        m = re.search(r'<img[^>]+src="([^"]+)"', summary_html)
        if m:
            return m.group(1)
    return None


def parse_feed(raw: bytes, feed_url: str) -> ParsedFeed:
    parsed = feedparser.parse(raw)
    fp_feed = parsed.get("feed", {})
    site_url = normalize_url(fp_feed.get("link"), feed_url)
    is_youtube = "youtube.com/feeds/videos.xml" in feed_url or "youtube.com" in (site_url or "")

    icon_url = None
    if fp_feed.get("icon"):
        icon_url = normalize_url(fp_feed["icon"], feed_url)
    elif fp_feed.get("image") and fp_feed["image"].get("href"):
        icon_url = normalize_url(fp_feed["image"]["href"], feed_url)

    entries: list[ParsedEntry] = []
    for e in parsed.get("entries", []):
        url = normalize_url(e.get("link"), site_url or feed_url)
        raw_html = _entry_content(e)
        summary_html = sanitize_html(raw_html, base_url=url or feed_url) if raw_html else None
        published = _struct_to_datetime(e.get("published_parsed")) or _struct_to_datetime(
            e.get("updated_parsed")
        )
        title = (e.get("title") or "").strip() or "(sans titre)"
        guid = (e.get("id") or "").strip() or url or hashlib.sha1(
            f"{title}|{published}".encode()
        ).hexdigest()

        # Empreinte de dédup : URL normalisée sinon titre+date.
        if url:
            digest_src = url
        else:
            digest_src = f"{title}|{published.isoformat() if published else ''}"
        entry_hash = hashlib.sha256(digest_src.encode()).hexdigest()

        meta: dict = {}
        video_id = _youtube_id(url, e) if is_youtube or (url and "youtu" in url) else None
        if video_id:
            meta["youtube_id"] = video_id
        cover = _cover_image(e, summary_html)
        if cover:
            meta["cover"] = normalize_url(cover, url or feed_url)
        enclosures = [
            {"url": link.get("href"), "type": link.get("type")}
            for link in e.get("links") or []
            if link.get("rel") == "enclosure" and link.get("href")
        ]
        if enclosures:
            meta["enclosures"] = enclosures

        entries.append(
            ParsedEntry(
                guid=guid[:2048],
                url=url,
                title=title[:1000],
                author=(e.get("author") or None),
                published_at=published or datetime.now(UTC),
                summary_html=summary_html,
                hash=entry_hash,
                meta=meta,
            )
        )

    bozo_error = None
    if not entries and (parsed.get("bozo") or not parsed.get("version")):
        exc = parsed.get("bozo_exception")
        bozo_error = str(exc) if exc else "Ce document n'est pas un flux RSS ou Atom"

    return ParsedFeed(
        title=(fp_feed.get("title") or "").strip(),
        site_url=site_url,
        description=(fp_feed.get("subtitle") or fp_feed.get("description") or None),
        icon_url=icon_url,
        entries=entries,
        is_youtube=is_youtube,
        bozo_error=bozo_error,
    )
