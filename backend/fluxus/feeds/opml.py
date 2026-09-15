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
    # La racine mélange dossiers et flux sans dossier, dans l'ordre de la barre latérale (position, titre).
    # `position` peut être None sur des objets non persistés (défaut SQL non appliqué) : traité comme 0.
    root: list[tuple[int, str, Category | Feed]] = [(c.position or 0, c.title.lower(), c) for c in categories]
    root += [(f.position or 0, (f.title or f.url).lower(), f) for f in by_cat.get(None, [])]
    root.sort(key=lambda item: (item[0], item[1]))
    for _, _, item in root:
        if isinstance(item, Feed):
            lines.append(outline(item).replace("      <outline", "    <outline", 1))
            continue
        cat_feeds = by_cat.get(item.id, [])
        if not cat_feeds:
            continue
        lines.append(f"    <outline text={quoteattr(item.title)} title={quoteattr(item.title)}>")
        lines.extend(outline(f) for f in sorted(cat_feeds, key=lambda f: (f.position or 0, f.title.lower())))
        lines.append("    </outline>")
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)


__all__ = ["OpmlFeed", "parse_opml", "build_opml", "escape"]
