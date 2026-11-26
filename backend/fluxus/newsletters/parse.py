"""Lecture d'un e-mail brut (MIME) reçu pour une newsletter : en-têtes utiles et HTML lisible.

Module pur (sans base ni réseau) : rien de ce qui est lu ici ne déclenche de requête. Le HTML des
newsletters est fait pour les messageries (tableaux de mise en page, styles en ligne, texte d'aperçu
masqué, pixels de suivi) : on le ramène à un flux de blocs avant le nettoyage commun (`sanitize_html`).
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

import lxml.html
from lxml import etree

from ..extract.clean import html_to_text, sanitize_html

MAX_HTML_CHARS = 2_000_000
MAX_TITLE_CHARS = 1000
BLOCK_TAGS = frozenset({"div"})


@dataclass
class ParsedEmail:
    subject: str
    from_name: str
    from_address: str
    # Clé de l'expéditeur : identifiant de liste (`List-Id`) s'il existe, sinon l'adresse d'envoi.
    sender_key: str
    # Nom à donner au flux d'une nouvelle newsletter.
    sender_title: str
    message_id: str
    published_at: datetime | None
    html: str
    text: str
    view_online_url: str | None = None
    unsubscribe_url: str | None = None
    unsubscribe_mailto: str | None = None
    unsubscribe_one_click: bool = False
    cover: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def sender_domain(self) -> str | None:
        _, _, domain = self.from_address.rpartition("@")
        return domain.lower() or None


def _header(msg: EmailMessage, name: str) -> str:
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 - en-tête mal formé : on fait sans
        return ""
    return " ".join(str(value).split()) if value is not None else ""


def _list_id(raw: str) -> tuple[str, str]:
    """`"Nom" <id.domaine>` → (id, nom)."""
    m = re.search(r"<([^<>\s]+)>", raw)
    if not m:
        return "", ""
    name = raw[: m.start()].strip().strip('"').strip()
    return m.group(1).lower()[:300], name


def _date(msg: EmailMessage) -> datetime | None:
    raw = _header(msg, "Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _unsubscribe(msg: EmailMessage) -> tuple[str | None, str | None, bool]:
    raw = _header(msg, "List-Unsubscribe")
    https = mailto = None
    for target in re.findall(r"<([^<>]+)>", raw):
        target = target.strip()
        if target.lower().startswith("https://") and https is None and len(target) <= 2048:
            https = target
        elif target.lower().startswith("mailto:") and mailto is None and len(target) <= 2048:
            mailto = target
    one_click = "one-click" in _header(msg, "List-Unsubscribe-Post").lower()
    return https, mailto, bool(https and one_click)


def _part_text(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, AssertionError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    return content if isinstance(content, str) else ""


# Éléments masqués : texte d'aperçu (« preheader »), blocs réservés à une autre taille d'écran.
# Pas `font-size:0` : les mises en page hybrides le posent sur le conteneur des colonnes visibles.
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all|max-height\s*:\s*0(?![.\d])|"
    r"(?<![-\w])opacity\s*:\s*0(?![.\d])",
    re.IGNORECASE,
)
_TINY_STYLE = re.compile(r"(?<![-\w])(?:width|height)\s*:\s*[0-3]px", re.IGNORECASE)
# Caractères invisibles de remplissage du texte d'aperçu.
_INVISIBLE = re.compile("[​‌‍͏⁠﻿­ ㅤ]")
_DROP_TAGS = ("script", "style", "head", "title", "meta", "link", "noscript", "template", "svg", "form", "button", "input", "select", "textarea")
_LAYOUT_TAGS = {"table": "div", "tbody": "div", "thead": "div", "tfoot": "div", "tr": "div", "td": "div", "th": "div", "center": "div"}
_KEEP_EMPTY = {"img", "br", "hr", "video", "audio", "iframe", "source"}
_VIEW_ONLINE = re.compile(
    r"\b(view|read|open|see)\b.{0,25}\b(online|browser|web)\b|"
    r"\b(voir|lire|afficher|consulter|ouvrir)\b.{0,35}\b(en ligne|navigateur|version web|sur le web|web)\b|"
    r"\bversion (en ligne|web)\b|\bim browser\b|\bonline (ansehen|lesen)\b",
    re.IGNORECASE,
)


def _is_tiny(img: etree._Element) -> bool:
    for attr in ("width", "height"):
        value = (img.get(attr) or "").strip().lower().removesuffix("px")
        if value.isdigit() and int(value) <= 3:
            return True
    return bool(_TINY_STYLE.search(img.get("style") or ""))


def _has_visible_content(el: etree._Element) -> bool:
    if _INVISIBLE.sub("", el.text_content() or "").strip():
        return True
    return any(child.tag in _KEEP_EMPTY for child in el.iter() if isinstance(child.tag, str))


def _nearest_table(el: etree._Element) -> etree._Element | None:
    parent = el.getparent()
    while parent is not None and parent.tag != "table":
        parent = parent.getparent()
    return parent


def _unwrap_layout_tables(root: etree._Element) -> None:
    """Les newsletters se mettent en page par tableaux imbriqués : on les aplatit en blocs (la lecture
    se fait alors de haut en bas, sur mobile comme sur grand écran). Un tableau de données (avec ses
    propres cellules d'en-tête `th`) reste un tableau."""
    groups: list[list[etree._Element]] = []
    for table in root.iter("table"):
        own = [el for el in table.iterdescendants("tbody", "thead", "tfoot", "tr", "td", "th") if _nearest_table(el) is table]
        if any(el.tag == "th" for el in own):
            continue
        groups.append([table, *own])
    for group in groups:
        for el in group:
            el.tag = _LAYOUT_TAGS[el.tag]
            for attr in ("colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "cellpadding", "cellspacing", "border"):
                el.attrib.pop(attr, None)


def clean_newsletter_html(raw_html: str) -> tuple[str, str | None, str | None]:
    """HTML d'e-mail → (HTML nettoyé, lien « voir en ligne », image de couverture)."""
    raw_html = raw_html[:MAX_HTML_CHARS]
    try:
        doc = lxml.html.document_fromstring(raw_html)
    except (etree.ParserError, ValueError):
        return sanitize_html(raw_html), None, None
    body = doc.find("body")
    root = body if body is not None else doc

    for el in list(root.iter(*_DROP_TAGS)):
        el.drop_tree()
    for el in list(root.iter()):
        # Commentaires (dont les commentaires conditionnels d'Outlook) et instructions de traitement.
        if not isinstance(el.tag, str):
            if el.getparent() is not None:
                el.drop_tree()
        elif el is not root and (_HIDDEN_STYLE.search(el.get("style") or "") or el.get("hidden") is not None):
            el.drop_tree()

    view_online = None
    cover = None
    for img in list(root.iter("img")):
        src = (img.get("src") or "").strip()
        if not src.lower().startswith(("http://", "https://")) or _is_tiny(img):
            img.drop_tree()
            continue
        width = (img.get("width") or "").strip().removesuffix("px")
        if cover is None and len(src) <= 2048 and (not width.isdigit() or int(width) >= 200):
            cover = src
    for a in root.iter("a"):
        href = (a.get("href") or "").strip()
        if href.lower().startswith(("http://", "https://")) and len(href) <= 2048 and _VIEW_ONLINE.search(a.text_content() or ""):
            view_online = href
            break

    _unwrap_layout_tables(root)

    # Blocs vides (espaceurs, cellules de marge) : du dernier au premier, pour remonter les imbrications.
    for el in reversed(list(root.iter("div", "p", "span", "font", "a", "strong", "b", "em", "i", "u"))):
        if el.getparent() is not None and not _has_visible_content(el):
            el.drop_tree()

    for text_holder in root.iter():
        if not isinstance(text_holder.tag, str):
            continue
        if text_holder.text:
            text_holder.text = _INVISIBLE.sub("", text_holder.text)
        if text_holder.tail:
            text_holder.tail = _INVISIBLE.sub("", text_holder.tail)

    inner = (root.text or "") + "".join(etree.tostring(child, encoding="unicode", method="html") for child in root)
    # Les cellules aplaties restent des blocs : sans eux, deux colonnes de texte se colleraient.
    return sanitize_html(inner, extra_tags=BLOCK_TAGS), view_online, cover


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def text_to_html(text: str) -> str:
    """Corps texte brut → paragraphes, liens cliquables."""
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    out = []
    for p in paragraphs:
        escaped = html_lib.escape(p.strip())
        linked = _URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)
        out.append("<p>" + linked.replace("\n", "<br>") + "</p>")
    return sanitize_html("".join(out))


def parse_email(raw: bytes) -> ParsedEmail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)

    subject = _header(msg, "Subject")[:MAX_TITLE_CHARS]
    from_name, from_address = parseaddr(_header(msg, "From"))
    from_address = from_address.lower()[:300]
    from_name = from_name.strip()[:300]
    list_id, list_name = _list_id(_header(msg, "List-Id"))
    sender_key = list_id or from_address or "inconnu"
    sender_title = (from_name or list_name or from_address or "Newsletter")[:500]

    message_id = _header(msg, "Message-ID").strip("<> ")[:900]
    if not message_id:
        message_id = "sha256:" + hashlib.sha256(raw).hexdigest()

    view_online = cover = None
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is not None and body.get_content_type() == "text/html":
        html, view_online, cover = clean_newsletter_html(_part_text(body))
    elif body is not None:
        html = text_to_html(_part_text(body))
    else:
        html = ""
    unsubscribe_url, unsubscribe_mailto, one_click = _unsubscribe(msg)

    return ParsedEmail(
        subject=subject,
        from_name=from_name,
        from_address=from_address,
        sender_key=sender_key,
        sender_title=sender_title,
        message_id=message_id,
        published_at=_date(msg),
        html=html,
        text=html_to_text(html),
        view_online_url=view_online,
        unsubscribe_url=unsubscribe_url,
        unsubscribe_mailto=unsubscribe_mailto,
        unsubscribe_one_click=one_click,
        cover=cover,
    )
