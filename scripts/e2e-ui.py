"""Parcours E2E de l'interface Fluxus (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-ui.py
"""

from e2e_common import BASE, Restorer, api, main, new_context, shot, wait_offline_ready

results: dict = {}
restorer = Restorer()


def run(page, ctx, errors):
    # Article à côté de la liste : défaut « compacte », flux YouTube sans présentation propre.
    youtube = next(f for f in api(page, "GET", "/api/feeds").json() if f["is_youtube"] and f["unread_count"] > 0)
    restorer.view_mode(page, "default", "compact")
    restorer.view_mode(page, "feed", "inherit", youtube["id"])
    if youtube["category_id"] is not None:
        restorer.view_mode(page, "category", "inherit", youtube["category_id"])

    # La page d'accueil est le tableau de bord ; la liste classique est sur /all.
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.locator("nav button:has-text('Tous les articles')").click()
    page.wait_for_url("**/all**")
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    page.wait_for_timeout(1500)
    shot(page, "02-list")
    results["entries_visible"] = page.locator("[data-entry-id]").count()
    assert results["entries_visible"] > 0

    # Ouvre le premier article
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_selector("article h1", timeout=10000)
    page.wait_for_timeout(800)
    results["reader_title"] = page.locator("article h1").inner_text()[:80]
    assert "entry=" in page.url, page.url
    shot(page, "03-reader")

    # Raccourci clavier j -> article suivant ; favori avec s
    url = page.url
    page.keyboard.press("j")
    page.wait_for_timeout(500)
    assert page.url != url, "j n'a pas changé d'article"
    page.keyboard.press("s")
    page.wait_for_timeout(500)
    assert page.locator("article button[aria-label*='Retirer des favoris']").count() == 1
    page.keyboard.press("s")  # remise en état

    # Article complet (f) puis résumé (r) -> pas de clé API : message d'erreur attendu
    page.keyboard.press("f")
    page.wait_for_timeout(6000)
    results["full_content_toggle"] = page.locator("article button[aria-label*='contenu du flux']").count()
    page.keyboard.press("r")
    page.wait_for_selector("aside:has-text('Résumé')", timeout=10000)
    page.wait_for_timeout(2500)
    results["summary_panel_text"] = page.locator("aside").inner_text()[:200]
    shot(page, "04-summary-error")

    # Vue YouTube
    # Par l'URL : le flux peut être dans un dossier replié (le clic dans la barre est couvert par e2e-sidebar).
    page.goto(f"{BASE}/feed/{youtube['id']}", wait_until="load")
    page.wait_for_selector("[data-entry-id]", timeout=10000)
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_selector("article iframe[src*='youtube']", timeout=10000)
    shot(page, "05-youtube")

    # Favoris
    page.locator("nav button:has-text('Favoris')").click()
    page.wait_for_url("**/starred**")
    page.wait_for_timeout(600)
    results["starred_list_count"] = page.locator("[data-entry-id]").count()

    # Recherche
    page.locator("button[aria-label='Rechercher']").click()
    page.fill("[data-search-input]", "france")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    results["search_results"] = page.locator("[data-entry-id]").count()
    shot(page, "06-search")

    # Paramètres
    page.goto(BASE + "/settings", wait_until="load")
    page.wait_for_selector("text=Résumés", timeout=10000)
    page.wait_for_timeout(800)
    shot(page, "07-settings")

    # Mode sombre + mobile. Contexte neuf : synchronisation complète, d'où `load` et non `networkidle`.
    page.evaluate("localStorage.setItem('fluxus.theme','dark')")
    mobile = new_context(page.context.browser, viewport={"width": 390, "height": 844}, is_mobile=True, storage_state=ctx.storage_state())
    mpage = mobile.new_page()
    mpage.goto(BASE + "/all", wait_until="load")
    mpage.wait_for_selector("[data-entry-id]", timeout=60000)
    mpage.wait_for_timeout(1500)
    shot(mpage, "08-mobile-list")
    mpage.locator("[data-entry-id] button").first.click()
    mpage.wait_for_selector("article h1", timeout=10000)
    mpage.wait_for_timeout(800)
    shot(mpage, "09-mobile-reader")
    mpage.locator("button[aria-label='Retour à la liste']").click()
    mpage.wait_for_timeout(500)
    mpage.locator("button[aria-label='Menu']").click()
    mpage.wait_for_timeout(600)
    shot(mpage, "10-mobile-drawer")

    # Hors ligne : la liste reste lisible
    wait_offline_ready(mpage)
    mobile.set_offline(True)
    mpage.reload()
    mpage.wait_for_selector("[data-entry-id]", timeout=15000)
    results["offline_entries"] = mpage.locator("[data-entry-id]").count()
    mpage.locator("[data-entry-id] button").first.click()
    mpage.wait_for_selector("article h1", timeout=10000)
    shot(mpage, "11-mobile-offline")
    mobile.close()

    results["js_errors"] = errors[:10]


main(run, results, failure_shot="ui-echec", cleanup=restorer.restore)
