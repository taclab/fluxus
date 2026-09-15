"""Parcours E2E des flux intelligents (Playwright, image fluxus-browser, --network host, -u 0).

docker run --rm --network host -u 0 -v $PWD/scripts/e2e-smartfeeds.py:/out/e2e-smartfeeds.py:ro -v /tmp/shots:/out fluxus-browser python /out/e2e-smartfeeds.py
"""

import json
import re

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PASSWORD = "motdepasse-test"
results: dict = {}


def api(page, method, path, **kw):
    token = page.evaluate("localStorage.getItem('fluxus.token')")
    return page.request.fetch(BASE + path, method=method, headers={"Authorization": f"Bearer {token}", "content-type": "application/json"}, **kw)


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    # bypass_csp : la CSP du site interdit eval, dont Playwright a besoin pour wait_for_function
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="fr-FR", bypass_csp=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)

    page.goto(BASE, wait_until="networkidle")
    page.fill("input[type=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("[data-widget]", timeout=60000)
    page.wait_for_timeout(1500)

    # Nettoyage des flux intelligents d'un passage précédent
    for sf in api(page, "GET", "/api/smart-feeds").json()["items"]:
        api(page, "DELETE", f"/api/smart-feeds/{sf['id']}")

    # Un mot très fréquent dans les titres pour garantir des correspondances
    titles = [i["title"] for i in api(page, "GET", "/api/entries?limit=200").json()["items"]]
    words: dict = {}
    for t in titles:
        for w in re.findall(r"[a-z]{6,}", t.lower()):  # sans accent : le mode regex cherche dans le texte désaccentué
            words[w] = words.get(w, 0) + 1
    keyword = max(words, key=words.get)
    results["keyword"] = keyword

    # --- Création via le dialogue, avec aperçu
    page.click("[data-testid=new-smartfeed]")
    page.fill("[data-testid=smartfeed-name]", "Test sujet")
    page.fill("[data-testid=kw-any]", keyword)
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('[data-testid=smartfeed-preview]').innerText.match(/\\d+ articles?/)", timeout=15000)
    preview_text = page.locator("[data-testid=smartfeed-preview]").inner_text()
    results["preview_count"] = int(re.search(r"(\d+)\s+article", preview_text).group(1))
    page.screenshot(path="/out/70-smartfeed-dialog.png")
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_url("**/smart/**", timeout=15000)
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    page.wait_for_timeout(1000)
    results["view_entries"] = page.locator("[data-entry-id]").count()
    results["sidebar_item"] = page.locator("nav [data-smartfeed-id]").count()
    sidebar_count_text = page.locator("nav [data-smartfeed-id] span").last.inner_text()
    results["sidebar_count"] = sidebar_count_text
    page.screenshot(path="/out/71-smartfeed-view.png")

    sf = api(page, "GET", "/api/smart-feeds").json()["items"][0]
    results["server_matches"] = sf["match_count"]

    # --- Clic sur un article : lecteur, retour
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_selector("article h1", timeout=10000)
    results["reader_url_ok"] = "/smart/" in page.url and "entry=" in page.url

    # --- Modification : un mot improbable → la vue se vide après sync
    page.click("[data-testid=edit-smartfeed]")
    page.wait_for_selector("[data-testid=smartfeed-name]")
    # retire le mot existant (croix) et ajoute un mot improbable
    page.locator("button[aria-label^='Retirer']").first.click()
    page.fill("[data-testid=kw-any]", "zzqxjvwmot")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    results["preview_zero"] = "0" in page.locator("[data-testid=smartfeed-preview]").inner_text()
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_timeout(3000)
    results["view_after_edit"] = page.locator("[data-entry-id]").count()
    results["server_after_edit"] = api(page, "GET", f"/api/smart-feeds/{sf['id']}").json()["match_count"]

    # --- Regex invalide : message du serveur ; regex valide
    page.click("[data-testid=edit-smartfeed]")
    page.wait_for_selector("[data-testid=smartfeed-name]")
    page.click("button[role=radio]:has-text('Expression régulière')")
    page.fill("[data-testid=smartfeed-regex]", "(?P<n>abc)")
    page.wait_for_timeout(1500)
    results["regex_error_shown"] = "refusée" in page.locator("[data-testid=smartfeed-preview]").inner_text()
    page.fill("[data-testid=smartfeed-regex]", f"\\m{keyword}")
    page.wait_for_timeout(1500)
    results["regex_preview_ok"] = bool(re.search(r"[1-9]\d*\s+article", page.locator("[data-testid=smartfeed-preview]").inner_text()))
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_timeout(3000)
    results["view_after_regex"] = page.locator("[data-entry-id]").count()

    # --- Widget du tableau de bord avec source « flux intelligent »
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.click("[data-testid=toggle-edit]")
    page.click("[data-testid=add-widget]")
    page.select_option("[data-testid=widget-type]", "news")
    page.select_option("[data-testid=widget-source]", f"smart:{sf['id']}")
    page.click("[data-testid=widget-submit]")
    page.wait_for_timeout(800)
    widget = page.locator("[data-widget-type=news]").last
    results["widget_title_ok"] = "Test sujet" in widget.inner_text()
    results["widget_entries"] = widget.locator("[data-entry-id]").count()
    page.click("[data-testid=toggle-edit]")
    page.screenshot(path="/out/72-smartfeed-widget.png")

    # --- summarize-all sans clé valide : la réponse est cohérente (aucune erreur JS)
    r = api(page, "POST", f"/api/smart-feeds/{sf['id']}/summarize-all", data=json.dumps({"unread_only": False}))
    results["summarize_all_status"] = r.status
    results["summarize_all_body_keys"] = sorted(r.json().keys())

    # --- Orphelin : portée catégorie supprimée
    cat = api(page, "POST", "/api/categories", data=json.dumps({"title": "Temp E2E"})).json()
    orphan = api(page, "POST", "/api/smart-feeds", data=json.dumps({"name": "Orphelin E2E", "keywords_any": ["x"], "scope_kind": "category", "scope_id": cat["id"]})).json()
    api(page, "DELETE", f"/api/categories/{cat['id']}")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.wait_for_timeout(2500)
    results["orphan_marker"] = page.locator(f"nav [data-smartfeed-id='{orphan['id']}'] svg.text-amber-500").count() == 1

    # --- Hors-ligne : la vue reste lisible
    page.goto(BASE + f"/smart/{sf['id']}", wait_until="networkidle")
    page.wait_for_timeout(1500)
    ctx.set_offline(True)
    page.reload()
    page.wait_for_timeout(2500)
    results["offline_view_entries"] = page.locator("[data-entry-id]").count()
    ctx.set_offline(False)

    # nettoyage
    api(page, "DELETE", f"/api/smart-feeds/{orphan['id']}")
    results["js_errors"] = errors[:5]
    browser.close()

print(json.dumps(results, ensure_ascii=False, indent=1))
