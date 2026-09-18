"""Parcours E2E de l'interface Fluxus (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-ui.py
"""

import json

from playwright.sync_api import sync_playwright

from e2e_common import BASE, login, new_context, shot, watch_errors

results = {}

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = new_context(browser)
    page = ctx.new_page()
    errors = watch_errors(page)

    page.goto(BASE, wait_until="networkidle")
    shot(page, "01-login")
    # La page d'accueil est le tableau de bord ; la liste classique est sur /all.
    login(page, settle_ms=0)
    page.locator("nav button:has-text('Tous les articles')").click()
    page.wait_for_url("**/all**")
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    page.wait_for_timeout(1500)
    shot(page, "02-list")
    results["feeds_in_sidebar"] = page.locator("nav button:has-text('Le Monde')").count() + page.locator("nav button:has-text('MrBeast')").count()
    results["entries_visible"] = page.locator("[data-entry-id]").count()

    # Ouvre le premier article
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_selector("article h1", timeout=10000)
    page.wait_for_timeout(800)
    results["reader_title"] = page.locator("article h1").inner_text()[:80]
    results["url_after_click"] = page.url
    shot(page, "03-reader")

    # Raccourci clavier j -> article suivant
    page.keyboard.press("j")
    page.wait_for_timeout(500)
    results["url_after_j"] = page.url
    # favori avec s
    page.keyboard.press("s")
    page.wait_for_timeout(500)
    results["starred_icon_active"] = page.locator("article button[aria-label*='Retirer des favoris']").count()

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
    page.locator("nav button:has-text('MrBeast')").first.click()
    page.wait_for_timeout(800)
    page.locator("[data-entry-id] button").first.click()
    page.wait_for_timeout(1500)
    results["youtube_iframe"] = page.locator("article iframe[src*='youtube']").count()
    shot(page, "05-youtube")

    # Favoris
    page.locator("nav button:has-text('Favoris')").click()
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
    page.goto(BASE + "/settings", wait_until="networkidle")
    page.wait_for_selector("text=Résumés", timeout=10000)
    page.wait_for_timeout(800)
    shot(page, "07-settings")

    # Mode sombre + mobile
    page.evaluate("localStorage.setItem('fluxus.theme','dark')")
    mobile = new_context(browser, viewport={"width": 390, "height": 844}, is_mobile=True, storage_state=ctx.storage_state())
    mpage = mobile.new_page()
    mpage.goto(BASE + "/all", wait_until="networkidle")
    mpage.wait_for_selector("[data-entry-id]", timeout=30000)
    mpage.wait_for_timeout(1500)
    shot(mpage, "08-mobile-list")
    mpage.locator("[data-entry-id] button").first.click()
    mpage.wait_for_selector("article h1", timeout=10000)
    mpage.wait_for_timeout(800)
    shot(mpage, "09-mobile-reader")
    results["mobile_back_button"] = mpage.locator("button[aria-label='Retour à la liste']").count()
    mpage.locator("button[aria-label='Retour à la liste']").click()
    mpage.wait_for_timeout(500)
    mpage.locator("button[aria-label='Menu']").click()
    mpage.wait_for_timeout(600)
    shot(mpage, "10-mobile-drawer")

    # Hors ligne : la liste reste lisible
    mobile.set_offline(True)
    mpage.reload()
    mpage.wait_for_timeout(2500)
    results["offline_entries"] = mpage.locator("[data-entry-id]").count()
    mpage.locator("[data-entry-id] button").first.click()
    mpage.wait_for_timeout(800)
    results["offline_reader_ok"] = mpage.locator("article h1").count()
    shot(mpage, "11-mobile-offline")

    results["js_errors"] = errors[:10]
    browser.close()

print(json.dumps(results, ensure_ascii=False, indent=1))
