"""Import et export OPML."""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from defusedxml import ElementTree as ET

from ..models import Category, Feed


@dataclass
class OpmlFeed:
    url: str
    title: str
    site_url: str | None
    category: str | None


def parse_opml(data: bytes) -> list[OpmlFeed]:
    root = ET.fromstring(data)
    body = root.find("body")
    if body is None:
        return []
    feeds: list[OpmlFeed] = []

    def walk(node: ET.Element, category: str | None) -> None:
        for outline in node.findall("outline"):
            xml_url = outline.get("xmlUrl")
            if xml_url:
                feeds.append(
                    OpmlFeed(
                        url=xml_url.strip(),
                        title=(outline.get("title") or outline.get("text") or "").strip(),
                        site_url=outline.get("htmlUrl"),
                        category=category,
                    )
                )
            else:
                title = (outline.get("title") or outline.get("text") or "").strip() or None
                walk(outline, title or category)

    walk(body, None)
    return feeds


def build_opml(categories: list[Category], feeds: list[Feed]) -> str:
    by_cat: dict[int | None, list[Feed]] = {}
    for f in feeds:
        by_cat.setdefault(f.category_id, []).append(f)

    def outline(f: Feed) -> str:
        attrs = [
            f"text={quoteattr(f.title or f.url)}",
            f"title={quoteattr(f.title or f.url)}",
            'type="rss"',
            f"xmlUrl={quoteattr(f.url)}",
        ]
        if f.site_url:
            attrs.append(f"htmlUrl={quoteattr(f.site_url)}")
        return f"      <outline {' '.join(attrs)}/>"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        "    <title>Fluxus</title>",
        "  </head>",
        "  <body>",
    ]
    for cat in sorted(categories, key=lambda c: (c.position, c.title)):
        cat_feeds = by_cat.get(cat.id, [])
        if not cat_feeds:
            continue
        lines.append(f"    <outline text={quoteattr(cat.title)} title={quoteattr(cat.title)}>")
        lines.extend(outline(f) for f in sorted(cat_feeds, key=lambda f: (f.position, f.title.lower())))
        lines.append("    </outline>")
    for f in sorted(by_cat.get(None, []), key=lambda f: (f.position, f.title.lower())):
        lines.append(outline(f).replace("      <outline", "    <outline", 1))
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)


__all__ = ["OpmlFeed", "parse_opml", "build_opml", "escape"]
