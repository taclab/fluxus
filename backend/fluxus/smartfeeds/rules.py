"""Traduction d'une définition de flux intelligent en condition SQL paramétrée.

Tout est évalué par PostgreSQL : une seule implémentation pour le fetcher, l'application rétroactive
et l'aperçu. Les mots-clés sont comparés sans casse ni accents (`unaccent` + ILIKE) ; la regex utilise
le moteur ARE de PostgreSQL (`~*`), non backtracking, sur le texte désaccentué.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from sqlalchemy import ColumnElement, and_, false, func, literal, not_, or_, select, true

from ..models import Entry, Feed

MAX_REGEX_LEN = 300
MAX_KEYWORDS = 50
MAX_KEYWORD_LEN = 100
FIELD_NAMES = ("title", "excerpt", "author", "url", "content")
DEFAULT_FIELDS = {"title": True, "excerpt": True, "author": True, "url": True, "content": False}

# Références arrière : seule construction coûteuse pour le moteur ARE (et inutile pour filtrer des titres).
_BACKREF_RE = re.compile(r"(?<!\\)\\[1-9]")


class SmartFeedDefinition(Protocol):
    scope_kind: str
    scope_id: int | None
    mode: str
    keywords_any: list
    keywords_all: list
    keywords_none: list
    regex: str | None
    fields: dict


class InvalidDefinition(ValueError):
    pass


def unaccent_py(text: str) -> str:
    """Même normalisation que `unaccent` côté PostgreSQL pour les mots-clés courants (latin)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_keywords(values: list | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        if not isinstance(v, str):
            continue
        v = " ".join(v.split())
        if v:
            out.append(v[:MAX_KEYWORD_LEN])
    return out


def normalize_definition(d: SmartFeedDefinition) -> dict:
    """Valide et nettoie une définition ; lève InvalidDefinition avec un message lisible."""
    if d.scope_kind not in ("all", "category", "feed"):
        raise InvalidDefinition("Portée inconnue")
    if d.scope_kind != "all" and not d.scope_id:
        raise InvalidDefinition("Cette portée demande une catégorie ou un flux")
    if d.mode not in ("keywords", "regex"):
        raise InvalidDefinition("Mode inconnu")
    fields = {name: bool((d.fields or {}).get(name, DEFAULT_FIELDS[name])) for name in FIELD_NAMES}
    if not any(fields.values()):
        raise InvalidDefinition("Choisir au moins un champ à examiner")
    any_, all_, none_ = _clean_keywords(d.keywords_any), _clean_keywords(d.keywords_all), _clean_keywords(d.keywords_none)
    regex = (d.regex or "").strip() or None
    if d.mode == "keywords":
        if not any_ and not all_:
            raise InvalidDefinition("Indiquer au moins un mot-clé dans « au moins un » ou « tous »")
        if len(any_) + len(all_) + len(none_) > MAX_KEYWORDS:
            raise InvalidDefinition(f"{MAX_KEYWORDS} mots-clés au maximum")
    else:
        if not regex:
            raise InvalidDefinition("Indiquer une expression régulière")
        if len(regex) > MAX_REGEX_LEN:
            raise InvalidDefinition(f"Expression trop longue ({MAX_REGEX_LEN} caractères au maximum)")
        if _BACKREF_RE.search(regex):
            raise InvalidDefinition("Les références arrière (\\1…\\9) ne sont pas autorisées")
    return {
        "scope_kind": d.scope_kind,
        "scope_id": d.scope_id if d.scope_kind != "all" else None,
        "mode": d.mode,
        "keywords_any": any_,
        "keywords_all": all_,
        "keywords_none": none_,
        "regex": regex,
        "fields": fields,
    }


def _columns(fields: dict) -> list[ColumnElement]:
    """Colonnes texte examinées, jamais NULL (coalesce) : sans cela « aucun » exclurait les entrées sans auteur."""
    cols: list[ColumnElement] = []
    if fields.get("title"):
        cols.append(func.coalesce(Entry.title, ""))
    if fields.get("excerpt"):
        # Le contenu du flux est du HTML nettoyé : les balises restent mais le texte est là.
        cols.append(func.coalesce(Entry.summary_html, ""))
    if fields.get("author"):
        cols.append(func.coalesce(Entry.author, ""))
    if fields.get("url"):
        cols.append(func.coalesce(Entry.url, ""))
    if fields.get("content"):
        cols.append(func.coalesce(Entry.content_text, ""))
    return cols


def scope_condition(scope_kind: str, scope_id: int | None) -> ColumnElement:
    if scope_kind == "feed":
        return Entry.feed_id == scope_id
    if scope_kind == "category":
        return Entry.feed_id.in_(select(Feed.id).where(Feed.category_id == scope_id))
    return true()


def scope_includes_feed(scope_kind: str, scope_id: int | None, feed: Feed) -> bool:
    """Filtre Python avant d'émettre une requête : inutile d'évaluer un flux hors portée."""
    if scope_kind == "feed":
        return feed.id == scope_id
    if scope_kind == "category":
        return feed.category_id == scope_id
    return True


def _keyword_match(cols: list[ColumnElement], word: str) -> ColumnElement:
    pattern = f"%{_escape_like(unaccent_py(word))}%"
    return or_(*[func.unaccent(col).ilike(pattern, escape="\\") for col in cols])


def build_condition(d: SmartFeedDefinition, *, include_scope: bool = True) -> ColumnElement:
    """Condition SQLAlchemy complète (portée + filtre) pour une définition déjà normalisée."""
    n = normalize_definition(d)
    cols = _columns(n["fields"])
    clauses: list[ColumnElement] = []
    if include_scope:
        clauses.append(scope_condition(n["scope_kind"], n["scope_id"]))
    if n["mode"] == "keywords":
        if n["keywords_any"]:
            clauses.append(or_(*[_keyword_match(cols, w) for w in n["keywords_any"]]))
        for w in n["keywords_all"]:
            clauses.append(_keyword_match(cols, w))
        if n["keywords_none"]:
            clauses.append(not_(or_(*[_keyword_match(cols, w) for w in n["keywords_none"]])))
    else:
        haystack = func.unaccent(func.concat_ws(literal(" \n "), *cols))
        clauses.append(haystack.op("~*")(n["regex"]))
    return and_(*clauses) if clauses else false()


def uses_content(d: SmartFeedDefinition) -> bool:
    return bool((d.fields or {}).get("content"))
