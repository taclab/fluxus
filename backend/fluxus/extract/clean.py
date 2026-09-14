"""Nettoyage du HTML avant stockage : suppression des scripts, absolutisation des URLs."""

from __future__ import annotations

import re
from urllib.parse import urljoin

import nh3

ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "details", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd",
    "li", "mark", "ol", "p", "pre", "q", "s", "small", "span", "strong", "sub", "summary", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "video", "audio", "source",
    "iframe",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "video": {"src", "poster", "controls", "width", "height"},
    "audio": {"src", "controls"},
    "source": {"src", "type"},
    "iframe": {"src", "width", "height", "allowfullscreen", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "code": {"class"},
    "pre": {"class"},
    "*": set(),
}

IFRAME_ALLOWED_HOSTS = ("youtube.com", "youtube-nocookie.com", "vimeo.com", "player.vimeo.com", "dailymotion.com")

_SRC_RE = re.compile(r'(?P<attr>\b(?:src|href|poster))="(?P<url>[^"]+)"', re.IGNORECASE)
_SRCSET_RE = re.compile(r'\s(?:srcset|data-srcset)="[^"]*"', re.IGNORECASE)
_LAZY_RE = re.compile(r'<img([^>]*?)\sdata-(?:src|original|lazy-src)="([^"]+)"', re.IGNORECASE)


def _absolutize(html: str, base_url: str | None) -> str:
    if not base_url:
        return html

    def repl(m: re.Match) -> str:
        url = m.group("url")
        if url.startswith(("http://", "https://", "data:", "mailto:", "#")):
            return m.group(0)
        return f'{m.group("attr")}="{urljoin(base_url, url)}"'

    return _SRC_RE.sub(repl, html)


def _iframe_host_allowed(src: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(src)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in IFRAME_ALLOWED_HOSTS)


def _filter_iframes(html: str) -> str:
    def repl(m: re.Match) -> str:
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag)
        if src and _iframe_host_allowed(src.group(1)):
            return tag
        return ""

    return re.sub(r"<iframe[^>]*>(?:.*?</iframe>)?", repl, html, flags=re.IGNORECASE | re.DOTALL)


def sanitize_html(html: str | None, base_url: str | None = None) -> str:
    if not html:
        return ""
    # Images chargées paresseusement : on promeut data-src en src avant nettoyage.
    html = _LAZY_RE.sub(r'<img\1 src="\2"', html)
    html = _SRCSET_RE.sub("", html)
    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
        url_schemes={"http", "https", "mailto"},
    )
    cleaned = _absolutize(cleaned, base_url)
    cleaned = _filter_iframes(cleaned)
    return cleaned.strip()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<(/p|/div|/h\d|/li|/tr|/blockquote|/pre)[^>]*>", "\n\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
