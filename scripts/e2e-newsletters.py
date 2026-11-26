"""Parcours E2E des newsletters par e-mail (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -e E2E_BASE=http://localhost:8001 -v $PWD/scripts:/scripts:ro \\
  -v /tmp/shots:/out fluxus-browser python /scripts/e2e-newsletters.py

Instance jetable uniquement : le parcours génère un nouveau secret du Worker (l'ancien ne peut pas
être rétabli). Il joue le rôle de l'Email Worker : mail brut signé posté à /api/inbound/email.
- Paramètres → Newsletters : domaine, secret (affiché une fois), adresse de tri, adresse dédiée.
- Réception : flux créé pour l'expéditeur, article lisible (blocs aplatis, pas de pixel ni de texte
  d'aperçu), lien de désinscription, pas de bouton « article complet ».
- Écran Abonnements : le flux est signalé comme newsletter.
- Révocation depuis les paramètres : le mail suivant est refusé (404).
"""

import hashlib
import hmac
import time
from email.message import EmailMessage

from e2e_common import BASE, api, main, shot, wait_synced, wait_until

results: dict = {}
created: dict = {"feeds": set(), "addresses_before": None, "domain": None}

HTML = """<html><body>
<div style="display:none">Texte d'aperçu caché</div>
<table width="600"><tr><td><a href="https://lettre-e2e.example/web/1">Voir la version en ligne</a></td></tr>
<tr><td><h1>Les nouvelles E2E</h1><p>Un paragraphe de la lettre.</p></td><td><p>Colonne de droite</p></td></tr>
<tr><td><img src="https://lettre-e2e.example/open.gif" width="1" height="1"></td></tr></table>
</body></html>"""


def build(message_id: str, sender: str = '"La Lettre E2E" <bonjour@lettre-e2e.example>') -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = f"Numéro {message_id}"
    msg["Message-ID"] = f"<{message_id}@lettre-e2e.example>"
    msg["List-Unsubscribe"] = "<https://lettre-e2e.example/unsub?u=1>"
    msg.set_content("Version texte")
    msg.add_alternative(HTML, subtype="html")
    return msg.as_bytes()


def deliver(page, secret: str, to: str, raw: bytes):
    """Ce que fait l'Email Worker (même calcul que `newsletters/service.sign`)."""
    ts = str(int(time.time() * 1000))
    payload = f"{ts}.{to.lower()}.{hashlib.sha256(raw).hexdigest()}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return page.request.post(
        BASE + "/api/inbound/email",
        data=raw,
        headers={"Content-Type": "message/rfc822", "X-Fluxus-Timestamp": ts, "X-Fluxus-To": to, "X-Fluxus-Signature": sig},
    )


def config(page) -> dict:
    return api(page, "GET", "/api/newsletters").json()


def run(page, ctx, errors):
    wait_synced(page)
    created["domain"] = config(page)["domain"]
    before = {a["id"] for a in config(page)["addresses"]}
    created["addresses_before"] = before

    # --- Paramètres
    page.goto(BASE + "/settings#newsletters", wait_until="load")
    section = page.locator("[data-testid=newsletters]")
    section.wait_for(timeout=30000)
    page.fill("[data-testid=newsletter-domain]", "news.e2e.example")
    section.get_by_role("button", name="Enregistrer").click()
    wait_until(lambda: config(page)["domain"] == "news.e2e.example", "enregistrement du domaine")

    page.click("[data-testid=newsletter-secret]")
    if page.locator("[data-testid=confirm-ok]").count():
        page.click("[data-testid=confirm-ok]")
    page.wait_for_selector("[data-testid=newsletter-secret-value]", timeout=10000)
    secret = page.input_value("[data-testid=newsletter-secret-value]")
    assert len(secret) >= 40
    page.get_by_role("button", name="J’ai copié le secret").click()
    page.get_by_text("Secret configuré").wait_for(timeout=10000)

    page.fill("[data-testid=newsletter-label]", "E2E tri")
    page.click("[data-testid=newsletter-create]")
    wait_until(lambda: len(config(page)["addresses"]) == len(before) + 1, "création de l'adresse de tri")
    page.wait_for_function("document.querySelector('[data-testid=newsletter-label]').value === ''")
    page.fill("[data-testid=newsletter-label]", "E2E dédiée")
    page.check("[data-testid=newsletter-dedicated]")
    page.click("[data-testid=newsletter-create]")
    wait_until(lambda: len(config(page)["addresses"]) == len(before) + 2, "création des deux adresses")
    mine = [a for a in config(page)["addresses"] if a["id"] not in before]
    sorting = next(a for a in mine if a["label"] == "E2E tri")
    dedicated = next(a for a in mine if a["label"] == "E2E dédiée")
    assert sorting["email"] == f"{sorting['token']}@news.e2e.example" and sorting["feed_id"] is None
    assert dedicated["feed_id"]
    created["feeds"].add(dedicated["feed_id"])
    page.locator(f"[data-newsletter-address='{sorting['token']}']").wait_for(timeout=10000)
    shot(page, "95-newsletters-settings")

    # --- Réception (rôle du Worker)
    r = deliver(page, secret, sorting["email"], build(f"e2e-{int(time.time())}"))
    assert r.status == 202, r.text()
    body = r.json()
    assert body["status"] == "created"
    created["feeds"].add(body["feed_id"])
    r = deliver(page, "mauvais-secret", sorting["email"], build("e2e-faux"))
    assert r.status == 401
    r = deliver(page, secret, dedicated["email"], build(f"e2e-d-{int(time.time())}", sender="Autre <x@ailleurs-e2e.example>"))
    assert r.status == 202 and r.json()["feed_id"] == dedicated["feed_id"]

    # --- Lecture
    page.goto(f"{BASE}/feed/{body['feed_id']}", wait_until="load")
    page.wait_for_selector(f"[data-entry-id='{body['entry_id']}']", timeout=60000)
    page.locator(f"[data-entry-id='{body['entry_id']}']").click()
    prose = page.locator("article .prose-reader")
    prose.wait_for(timeout=15000)
    text = prose.inner_text()
    assert "Les nouvelles E2E" in text and "Colonne de droite" in text, text
    assert "Texte d'aperçu caché" not in text
    assert prose.locator("img").count() == 0  # pixel de suivi retiré
    assert page.get_attribute("[data-testid=reader-unsubscribe]", "href") == "https://lettre-e2e.example/unsub?u=1"
    assert page.locator("article").get_by_role("link", name="Lire sur lettre-e2e.example").count() == 1
    results["reader_ok"] = True
    shot(page, "96-newsletters-reader")

    # --- Abonnements
    page.goto(BASE + "/abonnements", wait_until="load")
    row = page.locator(f"[data-feed-row='{body['feed_id']}']")
    row.wait_for(timeout=30000)
    assert "Newsletter reçue par e-mail" in row.inner_text()
    assert "La Lettre E2E" in row.inner_text()

    # --- Révocation
    page.goto(BASE + "/settings#newsletters", wait_until="load")
    item = page.locator(f"[data-newsletter-address='{sorting['token']}']")
    item.wait_for(timeout=30000)
    item.get_by_role("button", name="Révoquer").click()
    page.click("[data-testid=confirm-ok]")
    item.wait_for(state="detached", timeout=10000)
    r = deliver(page, secret, sorting["email"], build("e2e-apres-revocation"))
    assert r.status == 404
    results["revoked"] = True
    assert not errors, errors[:5]


def cleanup(page):
    # Toute adresse apparue pendant le parcours, même si une étape a échoué avant de la noter.
    if created["addresses_before"] is not None:
        for a in config(page)["addresses"]:
            if a["id"] not in created["addresses_before"]:
                if a["feed_id"]:
                    created["feeds"].add(a["feed_id"])
                api(page, "DELETE", f"/api/newsletters/addresses/{a['id']}")
    for feed_id in created["feeds"]:
        api(page, "DELETE", f"/api/feeds/{feed_id}")
    if created["domain"] is not None:
        api(page, "PATCH", "/api/settings", data={"values": {"newsletter_domain": created["domain"]}})


main(run, results, failure_shot="99-newsletters-failure", cleanup=cleanup)
