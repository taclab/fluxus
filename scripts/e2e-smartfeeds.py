"""Parcours E2E des flux intelligents (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-smartfeeds.py
"""

import json
import re

from e2e_common import BASE, Restorer, api, main, shot, wait_synced

results: dict = {}
restorer = Restorer()
TEST_NAMES = {"Test sujet", "Orphelin E2E"}


def remove_test_feeds(page):
    for sf in api(page, "GET", "/api/smart-feeds").json()["items"]:
        if sf["name"] in TEST_NAMES:
            api(page, "DELETE", f"/api/smart-feeds/{sf['id']}")


def cleanup(page):
    page.context.set_offline(False)
    remove_test_feeds(page)
    restorer.restore(page)


def run(page, ctx, errors):
    # Restes d'un passage interrompu ; les flux intelligents de l'utilisateur ne sont pas touchés.
    remove_test_feeds(page)
    # Article à côté de la liste : l'en-tête de la vue (bouton Modifier) reste accessible.
    restorer.view_mode(page, "default", "compact")
    restorer.save_layout(page)
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    wait_synced(page)

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
    shot(page, "70-smartfeed-dialog")
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_url("**/smart/**", timeout=15000)
    sf = next(f for f in api(page, "GET", "/api/smart-feeds").json()["items"] if f["name"] == "Test sujet")
    results["server_matches"] = sf["match_count"]
    assert sf["match_count"] == results["preview_count"], "l'aperçu annonçait un autre nombre"
    # Les correspondances arrivent par la synchro qui suit la création.
    page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=30000)
    results["view_entries"] = page.locator("ul[data-density] [data-entry-id]").count()
    assert results["view_entries"] == min(sf["match_count"], 60), results["view_entries"]
    assert page.locator(f"nav [data-smartfeed-id='{sf['id']}']").count() == 1
    shot(page, "71-smartfeed-view")

    # --- Clic sur un article : lecteur, retour
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_selector("article h1", timeout=10000)
    assert "/smart/" in page.url and "entry=" in page.url, page.url

    # --- Modification : un mot improbable → la vue se vide après sync
    page.click("[data-testid=edit-smartfeed]")
    page.wait_for_selector("[data-testid=smartfeed-name]")
    # retire le mot existant (croix) et ajoute un mot improbable
    page.locator("button[aria-label^='Retirer']").first.click()
    page.fill("[data-testid=kw-any]", "zzqxjvwmot")
    page.keyboard.press("Enter")
    page.wait_for_function("/Aucun article|\\b0 article/.test(document.querySelector('[data-testid=smartfeed-preview]').innerText)", timeout=15000)
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_selector("ul[data-density] [data-entry-id]", state="detached", timeout=30000)
    assert api(page, "GET", f"/api/smart-feeds/{sf['id']}").json()["match_count"] == 0

    # --- « Tout résumer » sur un flux sans correspondance : réponse cohérente, rien mis en file
    # (sur une instance avec clé et worker, l'appel avec correspondances lancerait de vrais résumés).
    r = api(page, "POST", f"/api/smart-feeds/{sf['id']}/summarize-all", data=json.dumps({"unread_only": False}))
    assert r.ok, r.status
    results["summarize_all"] = r.json()
    assert r.json()["queued"] == 0

    # --- Regex invalide : message du serveur ; regex valide
    page.click("[data-testid=edit-smartfeed]")
    page.wait_for_selector("[data-testid=smartfeed-name]")
    page.click("button[role=radio]:has-text('Expression régulière')")
    page.fill("[data-testid=smartfeed-regex]", "(?P<n>abc)")
    page.wait_for_function("document.querySelector('[data-testid=smartfeed-preview]').innerText.includes('refusée')", timeout=15000)
    page.fill("[data-testid=smartfeed-regex]", f"\\m{keyword}")
    page.wait_for_function("/[1-9]\\d*\\s+article/.test(document.querySelector('[data-testid=smartfeed-preview]').innerText)", timeout=15000)
    page.click("[data-testid=smartfeed-submit]")
    page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=30000)
    results["view_after_regex"] = page.locator("ul[data-density] [data-entry-id]").count()

    # --- Widget du tableau de bord avec source « flux intelligent »
    page.goto(BASE, wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.click("[data-testid=toggle-edit]")
    page.click("[data-testid=add-widget]")
    page.select_option("[data-testid=widget-type]", "news")
    page.select_option("[data-testid=widget-source]", f"smart:{sf['id']}")
    page.click("[data-testid=widget-submit]")
    widget = page.locator("[data-widget-type=news]").last
    widget.locator("[data-entry-id]").first.wait_for(timeout=15000)
    assert "Test sujet" in widget.inner_text()
    results["widget_entries"] = widget.locator("[data-entry-id]").count()
    page.click("[data-testid=toggle-edit]")
    shot(page, "72-smartfeed-widget")

    # --- Orphelin : portée catégorie supprimée
    cat = api(page, "POST", "/api/categories", data=json.dumps({"title": "Temp E2E"})).json()
    orphan = api(page, "POST", "/api/smart-feeds", data=json.dumps({"name": "Orphelin E2E", "keywords_any": ["x"], "scope_kind": "category", "scope_id": cat["id"]})).json()
    api(page, "DELETE", f"/api/categories/{cat['id']}")
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.locator(f"nav [data-smartfeed-id='{orphan['id']}'] svg.text-amber-500").wait_for(timeout=15000)

    # --- Hors-ligne : la vue reste lisible
    page.goto(BASE + f"/smart/{sf['id']}", wait_until="load")
    page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=30000)
    ctx.set_offline(True)
    page.reload()
    page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=15000)
    results["offline_view_entries"] = page.locator("ul[data-density] [data-entry-id]").count()
    assert results["offline_view_entries"] == results["view_after_regex"]
    ctx.set_offline(False)
    assert not errors, errors[:5]



main(run, results, failure_shot="smartfeeds-echec", cleanup=cleanup)
