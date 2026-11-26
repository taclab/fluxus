"""Lecture des e-mails de newsletters et signature du Worker (sans base)."""

from __future__ import annotations

from email.message import EmailMessage

from fluxus.newsletters.parse import clean_newsletter_html, parse_email
from fluxus.newsletters.service import sign, token_of, verify_signature

# Gabarit typique d'un routeur d'e-mails : texte d'aperçu masqué, tableaux de mise en page imbriqués,
# espaceurs, commentaires conditionnels Outlook, pixel de suivi, image hébergée en pièce jointe.
NEWSLETTER_HTML = """<!DOCTYPE html><html><head><style>.x{color:red}</style><title>Lettre</title></head>
<body style="margin:0">
<div style="display:none;max-height:0;overflow:hidden">Aperçu caché&zwnj;&nbsp;&zwnj;&nbsp;&#847;</div>
<!--[if mso]><table><tr><td>Outlook seulement</td></tr></table><![endif]-->
<table width="100%" cellpadding="0" bgcolor="#eeeeee"><tr><td align="center">
  <table width="600"><tr><td style="font-size:0">
    <a href="https://lettre.example/web/42">Voir la version en ligne</a>
  </td></tr>
  <tr><td><img src="https://cdn.example/banniere.jpg" width="600" alt="Bannière"></td></tr>
  <tr><td height="20">&nbsp;</td></tr>
  <tr><td><h1>Les nouvelles de la semaine</h1><p>Premier <a href="https://t.example/c/abc">sujet</a> important.</p></td>
      <td><p>Colonne de droite</p></td></tr>
  <tr><td><table><tr><th>Ville</th><th>Prix</th></tr><tr><td>Lyon</td><td>12</td></tr></table></td></tr>
  <tr><td><img src="cid:logo@x" alt="logo"><img src="https://t.example/open.gif" width="1" height="1" alt=""></td></tr>
  </table>
</td></tr></table>
<script>alert(1)</script>
</body></html>"""


def build_email(*, html: str | None = NEWSLETTER_HTML, text: str | None = "Version texte", headers: dict | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = '"La Lettre Tech" <bonjour@lettre.example>'
    msg["To"] = "k7f3q9x2abcd@news.example.org"
    msg["Subject"] = "Numéro 42 : les nouvelles"
    msg["Date"] = "Tue, 24 Nov 2026 07:30:00 +0100"
    msg["Message-ID"] = "<numero-42@lettre.example>"
    msg["List-Id"] = '"La Lettre" <a1b2c3.list-id.lettre.example>'
    msg["List-Unsubscribe"] = "<mailto:unsub@lettre.example?subject=stop>, <https://lettre.example/unsub?u=1>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    for key, value in (headers or {}).items():
        del msg[key]
        if value is not None:
            msg[key] = value
    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is not None:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    return msg.as_bytes()


def test_headers_and_unsubscribe():
    parsed = parse_email(build_email())
    assert parsed.subject == "Numéro 42 : les nouvelles"
    assert parsed.from_name == "La Lettre Tech"
    assert parsed.from_address == "bonjour@lettre.example"
    assert parsed.sender_domain == "lettre.example"
    # List-Id prime sur l'expéditeur : une même liste garde son flux si l'adresse d'envoi change.
    assert parsed.sender_key == "a1b2c3.list-id.lettre.example"
    assert parsed.sender_title == "La Lettre Tech"
    assert parsed.message_id == "numero-42@lettre.example"
    assert parsed.published_at is not None and parsed.published_at.isoformat() == "2026-11-24T07:30:00+01:00"
    assert parsed.unsubscribe_url == "https://lettre.example/unsub?u=1"
    assert parsed.unsubscribe_mailto == "mailto:unsub@lettre.example?subject=stop"
    assert parsed.unsubscribe_one_click


def test_newsletter_html_is_flattened_and_cleaned():
    parsed = parse_email(build_email())
    html = parsed.html
    assert "Les nouvelles de la semaine" in html and "Colonne de droite" in html
    # Masqué, Outlook seul, scripts, styles, titre du document : rien ne passe.
    for gone in ("Aperçu caché", "Outlook seulement", "alert(1)", "color:red", "<title", "style="):
        assert gone not in html, gone
    # Pixel de suivi et image en pièce jointe retirés ; l'image hébergée reste et sert de couverture.
    assert "open.gif" not in html and "cid:" not in html
    assert "https://cdn.example/banniere.jpg" in html
    assert parsed.cover == "https://cdn.example/banniere.jpg"
    # Tableaux de mise en page aplatis, tableau de données conservé.
    assert html.count("<table") == 1 and "<th>Ville</th>" in html
    # Liens de suivi laissés tels quels (les résoudre déclencherait le clic).
    assert "https://t.example/c/abc" in html
    assert parsed.view_online_url == "https://lettre.example/web/42"
    assert "‌" not in html and "͏" not in html
    assert "Premier sujet important." in parsed.text


def test_plain_text_only_email():
    raw = build_email(html=None, text="Bonjour,\n\nLire la suite : https://exemple.org/a?b=1\nMerci <3")
    parsed = parse_email(raw)
    assert '<a href="https://exemple.org/a?b=1"' in parsed.html
    assert "Merci &lt;3" in parsed.html
    assert parsed.html.count("<p>") == 2


def test_missing_headers_fall_back():
    raw = build_email(headers={"Message-ID": None, "List-Id": None, "Date": None, "From": "bonjour@lettre.example", "List-Unsubscribe": None})
    parsed = parse_email(raw)
    assert parsed.message_id.startswith("sha256:")
    assert parsed.sender_key == "bonjour@lettre.example"
    assert parsed.sender_title == "bonjour@lettre.example"  # ni nom d'expéditeur ni nom de liste : l'adresse
    assert parsed.published_at is None
    assert parsed.unsubscribe_url is None and not parsed.unsubscribe_one_click


def test_unknown_charset_does_not_crash():
    raw = build_email(html=None, text="x").replace(b'charset="utf-8"', b'charset="x-inconnu"')
    parsed = parse_email(raw)
    assert "x" in parsed.text


def test_hybrid_layout_columns_survive():
    # `font-size:0` sur le conteneur des colonnes (mise en page hybride) ne masque rien.
    html, _, _ = clean_newsletter_html('<div style="font-size:0"><div style="display:inline-block">Visible</div></div>')
    assert "Visible" in html


def test_signature_roundtrip_and_window():
    body = b"From: a@b\r\n\r\nx"
    ts = "1764000000000"
    sig = sign("secret", ts, "Abc@News.Example.org", body)
    assert verify_signature("secret", ts, "abc@news.example.org", body, sig, now_ms=1764000000000 + 60_000)
    assert not verify_signature("secret", ts, "abc@news.example.org", body + b"!", sig, now_ms=1764000000000)
    assert not verify_signature("autre", ts, "abc@news.example.org", body, sig, now_ms=1764000000000)
    assert not verify_signature("secret", ts, "zzz@news.example.org", body, sig, now_ms=1764000000000)
    assert not verify_signature("secret", ts, "abc@news.example.org", body, sig, now_ms=1764000000000 + 301_000)
    assert not verify_signature("", ts, "abc@news.example.org", body, sig, now_ms=1764000000000)


def test_signature_matches_worker_vector():
    """Même vecteur que `cloudflare/email-worker/worker.test.mjs` : Python et le Worker signent pareil."""
    assert sign("s3cr3t", "1764000000000", "k7f3q9x2abcd@news.example.org", b"Subject: test\r\n\r\nhello") == WORKER_VECTOR


WORKER_VECTOR = "22c7dc70d412d831a39876e6f568def38f2beb553c83e4648bcdc150dc0aa6fa"


def test_token_of():
    assert token_of("K7F3Q9@News.Example.org") == "k7f3q9"
    assert token_of("<k7f3q9@news.example.org>") == "k7f3q9"
