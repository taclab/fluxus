"""Parcours E2E de la disposition « pleine largeur + fenêtre de lecture » (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts/e2e-layout.py:/out/e2e-layout.py:ro -v /tmp/shots:/out fluxus-browser python /out/e2e-layout.py
"""

import json
import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE", "http://localhost:8000")
PASSWORD = "motdepasse-test"
results: dict = {}


def api(page, method, path, **kw):
    token = page.evaluate("localStorage.getItem('fluxus.token')")
    return page.request.fetch(BASE + path, method=method, headers={"Authorization": f"Bearer {token}", "content-type": "application/json"}, **kw)


def entry_param(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    return (parse_qs(urlparse(url).query).get("entry") or [None])[0]


def run(page, ctx, errors):
    touched: set[int] = set()
    # état lu initial des articles que le test va ouvrir, pour les restaurer
    initial_read: dict[int, bool] = {}

    def remember(entry_id: int):
        if entry_id not in initial_read:
            initial_read[entry_id] = api(page, "GET", f"/api/entries/{entry_id}").json()["read"]
        touched.add(entry_id)

    # 1. Paramètres → pleine largeur
    page.goto(BASE + "/settings", wait_until="networkidle")
    page.wait_for_selector("[data-testid=layout-full]", timeout=30000)
    page.click("[data-testid=layout-full]")
    page.wait_for_timeout(300)
    results["settings_radio_checked"] = page.locator("[data-testid=layout-full]").get_attribute("aria-checked") == "true"
    page.goto(BASE + "/all", wait_until="networkidle")
    page.wait_for_selector("nav [data-feed-id]", timeout=30000)
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    page.wait_for_timeout(800)
    list_box = page.locator("[data-entry-id]").first.locator("xpath=ancestor::section[1]").bounding_box()
    nav_box = page.locator("nav").first.bounding_box()
    results["list_full_width"] = list_box is not None and list_box["width"] > 1400 - nav_box["width"] - 20
    results["no_inline_reader"] = page.locator("article h1").count() == 0

    # 2. Présentations : grille par défaut, puis magazine, liste, titres
    results["default_variant"] = page.locator("ul[data-density]").get_attribute("data-density")
    ys = page.locator("[data-entry-id]").evaluate_all("els => els.slice(0, 6).map(e => Math.round(e.getBoundingClientRect().top))")
    results["grid_cards_per_row"] = max(ys.count(y) for y in set(ys))
    results["grid_covers"] = page.locator("ul[data-density=grid] img").count()
    page.screenshot(path="/out/100-layout-grid.png")

    def choose(variant):
        page.click("[data-testid=density-menu]")
        page.click(f"[role=menu] [data-density={variant}]")
        page.wait_for_timeout(300)
        return page.locator("ul[data-density]").get_attribute("data-density")

    results["variant_magazine"] = choose("magazine") == "magazine"
    ys = page.locator("[data-entry-id]").evaluate_all("els => els.slice(0, 4).map(e => Math.round(e.getBoundingClientRect().top))")
    results["magazine_one_per_row"] = len(set(ys)) == 4
    page.screenshot(path="/out/101-layout-magazine.png")
    results["variant_list"] = choose("list") == "list"
    results["variant_titles"] = choose("titles") == "titles"
    results["titles_row_height"] = page.locator("[data-entry-id]").first.bounding_box()["height"]
    page.screenshot(path="/out/102-layout-titles.png")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("ul[data-density]", timeout=30000)
    results["variant_persists"] = page.locator("ul[data-density]").get_attribute("data-density") == "titles"
    results["split_density_untouched"] = page.evaluate("localStorage.getItem('fluxus.density')") in (None, "compact", "cards")
    choose("grid")

    # 3. Clic sur une carte → fenêtre
    ids = page.locator("[data-entry-id]").evaluate_all("els => els.map(e => Number(e.dataset.entryId))")
    for i in ids[:3]:
        remember(i)
    page.locator(f"[data-entry-id='{ids[0]}'] [role=button]").click()
    page.wait_for_selector("[data-testid=reader-modal] article h1", timeout=15000)
    results["modal_opens"] = True
    results["modal_url_entry"] = entry_param(page.url) == str(ids[0])
    results["modal_position"] = page.locator("[data-testid=modal-position]").inner_text()
    page.screenshot(path="/out/103-layout-modal.png")

    # 4. Navigation : flèches et boutons
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(700)
    results["arrow_right_next"] = entry_param(page.url) == str(ids[1])
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(700)
    results["arrow_left_prev"] = entry_param(page.url) == str(ids[0])
    results["prev_disabled_at_start"] = page.locator("[data-testid=modal-prev]").first.is_disabled()
    page.click("[data-testid=modal-next]")
    page.wait_for_timeout(700)
    results["button_next"] = entry_param(page.url) == str(ids[1])
    page.click("[data-testid=modal-prev]")
    page.wait_for_timeout(700)
    results["button_prev"] = entry_param(page.url) == str(ids[0])
    results["title_matches_list"] = page.locator("[data-testid=reader-modal] article h1").inner_text().strip()[:30] in page.locator(f"[data-entry-id='{ids[0]}']").inner_text()

    # 6. Raccourcis actifs dans la fenêtre : s (favori) puis s
    starred_before = api(page, "GET", f"/api/entries/{ids[0]}").json()["starred"]
    page.keyboard.press("s")
    page.wait_for_timeout(1200)
    results["shortcut_star_in_modal"] = api(page, "GET", f"/api/entries/{ids[0]}").json()["starred"] != starred_before
    page.keyboard.press("s")
    page.wait_for_timeout(1200)

    # 5. Échap ferme, URL sans entry, défilement conservé, focus sur la carte
    scroller = page.locator("[data-entry-id]").first.locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]")
    scroller.evaluate("el => el.scrollTo(0, 200)")
    page.wait_for_timeout(200)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    results["escape_closes"] = page.locator("[data-testid=reader-modal]").count() == 0
    results["escape_url_clean"] = entry_param(page.url) is None
    results["scroll_kept"] = scroller.evaluate("el => el.scrollTop") >= 150
    results["focus_on_card"] = page.evaluate(f"document.activeElement?.closest('[data-entry-id]')?.dataset.entryId") == str(ids[0])

    # 7. Lien profond
    page.goto(BASE + f"/all?entry={ids[2]}", wait_until="networkidle")
    page.wait_for_selector("[data-testid=reader-modal] article h1", timeout=15000)
    results["deeplink_opens_modal"] = True
    page.keyboard.press("Escape")

    # 8. Téléphone : fenêtre plein écran, retour ferme
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + f"/all?entry={ids[0]}", wait_until="networkidle")
    page.wait_for_selector("[data-testid=reader-modal] article h1", timeout=15000)
    box = page.locator("[data-testid=reader-modal] article").bounding_box()
    results["mobile_fullscreen"] = box is not None and box["width"] >= 385
    page.screenshot(path="/out/104-layout-mobile.png")
    page.click("[data-testid=reader-modal] button[aria-label='Retour à la liste']")
    page.wait_for_timeout(500)
    results["mobile_back_closes"] = page.locator("[data-testid=reader-modal]").count() == 0
    page.set_viewport_size({"width": 1400, "height": 900})

    # 9. Retour en colonnes
    page.goto(BASE + "/settings", wait_until="networkidle")
    page.click("[data-testid=layout-split]")
    page.goto(BASE + f"/all?entry={ids[0]}", wait_until="networkidle")
    page.wait_for_selector("article h1", timeout=15000)
    results["split_inline_reader"] = page.locator("[data-testid=reader-modal]").count() == 0 and page.locator("article h1").count() == 1
    results["split_variant_menu"] = page.locator("[data-testid=density-menu]").count() == 1

    # Restauration : état lu d'origine des articles ouverts
    for entry_id, was_read in initial_read.items():
        api(page, "PATCH", f"/api/entries/{entry_id}", data=json.dumps({"read": was_read}))
    results["js_errors"] = errors[:5]


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="fr-FR", bypass_csp=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.goto(BASE, wait_until="networkidle")
    page.fill("input[type=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("[data-widget]", timeout=60000)
    page.wait_for_timeout(2000)
    try:
        run(page, ctx, errors)
    except Exception as exc:  # noqa: BLE001
        results["exception"] = repr(exc)[:300]
        results["url_at_failure"] = page.url
        results["js_errors"] = errors[:5]
        page.screenshot(path="/out/109-layout-failure.png")
        print(json.dumps(results, ensure_ascii=False, indent=1))
        browser.close()
        raise SystemExit(1)
    finally:
        # ne pas laisser l'instance de l'utilisateur en pleine largeur
        page.evaluate("localStorage.setItem('fluxus.listLayout', 'split')")
    browser.close()

print(json.dumps(results, ensure_ascii=False, indent=1))
