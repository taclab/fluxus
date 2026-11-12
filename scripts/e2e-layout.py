"""Parcours E2E de la présentation par vue (flux → dossier → défaut) et de la fenêtre de lecture
(Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-layout.py

L'instance est remise dans son état d'origine à la fin (présentations des flux et dossiers touchés,
défaut global, état lu des articles ouverts), même en cas d'échec.
"""

import json

from e2e_common import BASE, api, main, set_view_mode

results: dict = {}
# Vues modifiées par le parcours : {("feed", id): valeur d'origine ou None}, et défaut d'origine.
touched_modes: dict[tuple[str, int], str | None] = {}
original_default: dict[str, str] = {}
initial_read: dict[int, bool] = {}


def entry_param(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    return (parse_qs(urlparse(url).query).get("entry") or [None])[0]


def run(page, ctx, errors):
    original_default["value"] = api(page, "GET", "/api/settings").json()["values"].get("default_view_mode", "compact")
    feeds = api(page, "GET", "/api/feeds").json()
    categories = api(page, "GET", "/api/categories").json()
    stats = {s["feed_id"]: s for s in api(page, "GET", "/api/feeds/stats").json()}

    # Flux actifs avec des articles récents ; un dossier avec au moins deux tels flux.
    lively = sorted((f for f in feeds if not f["disabled"] and stats.get(f["id"], {}).get("entries_30d", 0) > 3), key=lambda f: -stats[f["id"]]["entries_30d"])
    by_cat: dict[int, list] = {}
    for f in lively:
        if f["category_id"] is not None:
            by_cat.setdefault(f["category_id"], []).append(f)
    cat_id, cat_feeds = next((c, fs) for c, fs in by_cat.items() if len(fs) >= 3)
    feed_a, feed_b, feed_c = cat_feeds[:3]
    for f in (feed_a, feed_b, feed_c):
        touched_modes[("feed", f["id"])] = f["view_mode"]
    touched_modes[("category", cat_id)] = next(c["view_mode"] for c in categories if c["id"] == cat_id)
    # Point de départ connu : aucune présentation propre sur ces vues, défaut « compacte ».
    for kind, id_ in (("feed", feed_a["id"]), ("feed", feed_b["id"]), ("feed", feed_c["id"]), ("category", cat_id)):
        set_view_mode(page, kind, "inherit", id_)
    set_view_mode(page, "default", "compact")

    def open_view(path: str):
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=30000)
        page.wait_for_timeout(600)

    def density() -> str | None:
        return page.locator("ul[data-density]").get_attribute("data-density")

    def choose(variant: str) -> str | None:
        page.click("[data-testid=density-menu]")
        page.click(f"[role=menu] [data-density={variant}]")
        page.wait_for_timeout(700)
        return density()

    def source_label() -> str:
        page.click("[data-testid=density-menu]")
        text = page.locator("[data-testid=view-mode-source]").inner_text()
        page.keyboard.press("Escape")
        page.mouse.click(5, 5)
        return text

    # 1. Rechargement complet pour récupérer les valeurs initialisées ci-dessus.
    open_view(f"/feed/{feed_a['id']}")
    results["start_compact"] = density() == "compact"
    results["start_inline_reader_pane"] = page.locator("section").count() >= 1

    # 2. Menu unifié : six présentations en deux groupes.
    page.click("[data-testid=density-menu]")
    results["menu_items"] = page.locator("[role=menu] [role=menuitemradio]").count()
    results["menu_groups"] = page.locator("[role=menu] [role=group]").count()
    page.screenshot(path="/out/110-view-menu.png")
    page.mouse.click(5, 5)

    # 3. Flux A en Liste, flux B en Magazine : mémorisés côté serveur et après rechargement.
    results["feed_a_list"] = choose("list") == "list"
    list_box = page.locator("[data-entry-id]").first.locator("xpath=ancestor::section[1]").bounding_box()
    nav_box = page.locator("nav").first.bounding_box()
    results["full_width_list"] = list_box is not None and list_box["width"] > 1400 - nav_box["width"] - 20
    results["feed_a_source"] = source_label()
    page.screenshot(path="/out/111-feed-list.png")
    open_view(f"/feed/{feed_b['id']}")
    results["feed_b_still_default"] = density() == "compact"
    results["feed_b_magazine"] = choose("magazine") == "magazine"
    page.screenshot(path="/out/112-feed-magazine.png")
    open_view(f"/feed/{feed_a['id']}")
    results["feed_a_persists"] = density() == "list"
    page.reload(wait_until="networkidle")
    page.wait_for_selector("ul[data-density] [data-entry-id]", timeout=30000)
    results["feed_a_persists_reload"] = density() == "list"
    by_id = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["server_feed_a"] = by_id[feed_a["id"]]["view_mode"]
    results["server_feed_b"] = by_id[feed_b["id"]]["view_mode"]

    # 4. Dossier en Cartes : hérité par le flux C (sans présentation propre), pas par A.
    open_view(f"/category/{cat_id}")
    results["category_cards"] = choose("cards") == "cards"
    open_view(f"/feed/{feed_c['id']}")
    results["feed_c_inherits_cards"] = density() == "cards"
    results["feed_c_source"] = source_label()
    open_view(f"/feed/{feed_a['id']}")
    results["feed_a_keeps_list"] = density() == "list"

    # 5. Retour à l'héritage sur A : suit le dossier.
    page.click("[data-testid=density-menu]")
    page.click("[data-testid=view-mode-inherit]")
    page.wait_for_timeout(700)
    results["feed_a_inherit_cards"] = density() == "cards"
    results["server_feed_a_after_inherit"] = api(page, "GET", f"/api/feeds/{feed_a['id']}").json()["view_mode"]

    # 6. Vue transversale : le menu règle le défaut global.
    open_view("/all")
    results["all_source"] = source_label()
    results["all_grid"] = choose("grid") == "grid"
    results["server_default"] = api(page, "GET", "/api/settings").json()["values"]["default_view_mode"]
    ys = page.locator("[data-entry-id]").evaluate_all("els => els.slice(0, 6).map(e => Math.round(e.getBoundingClientRect().top))")
    results["grid_cards_per_row"] = max(ys.count(y) for y in set(ys))
    page.screenshot(path="/out/113-all-grid.png")

    # 7. Fenêtre de lecture (pleine largeur) : ouverture, flèches, Échap, focus.
    ids = page.locator("[data-entry-id]").evaluate_all("els => els.map(e => Number(e.dataset.entryId))")
    for i in ids[:3]:
        initial_read.setdefault(i, api(page, "GET", f"/api/entries/{i}").json()["read"])
    page.locator(f"[data-entry-id='{ids[0]}'] [data-entry-focus]").click()
    page.wait_for_selector("[data-testid=reader-modal] article h1", timeout=15000)
    results["modal_opens"] = entry_param(page.url) == str(ids[0])
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(700)
    results["arrow_right_next"] = entry_param(page.url) == str(ids[1])
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(700)
    results["arrow_left_prev"] = entry_param(page.url) == str(ids[0])
    # Fenêtre de lecture ouverte : ↓ fait défiler l'article, j / k changent d'article.
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(300)
    results["arrow_down_keeps_article"] = entry_param(page.url) == str(ids[0])
    page.keyboard.press("j")
    page.wait_for_function("id => new URL(location.href).searchParams.get('entry') === id", arg=str(ids[1]), timeout=5000)
    page.keyboard.press("k")
    page.wait_for_function("id => new URL(location.href).searchParams.get('entry') === id", arg=str(ids[0]), timeout=5000)
    results["j_k_change_article"] = True
    # Aide des raccourcis : une fenêtre de l'application (plus d'alert), qu'Échap ferme seule.
    native_dialogs: list[str] = []
    page.on("dialog", lambda d: (native_dialogs.append(d.message), d.dismiss()))
    page.keyboard.press("?")
    page.wait_for_selector("[data-testid=shortcuts-help]", timeout=5000)
    results["help_is_in_app"] = native_dialogs == []
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-testid=shortcuts-help]", state="detached", timeout=5000)
    results["escape_closes_help_only"] = page.locator("[data-testid=reader-modal]").count() == 1 and entry_param(page.url) == str(ids[0])
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    results["escape_closes"] = page.locator("[data-testid=reader-modal]").count() == 0
    results["focus_on_card"] = page.evaluate("document.activeElement?.closest('[data-entry-id]')?.dataset.entryId") == str(ids[0])

    # 8. Flux en Cartes (colonnes) : l'article s'ouvre à côté, pas de fenêtre.
    open_view(f"/feed/{feed_c['id']}?entry={ids[0]}")
    page.wait_for_selector("article h1", timeout=15000)
    results["split_no_modal"] = page.locator("[data-testid=reader-modal]").count() == 0

    # 9. Téléphone : présentation pleine largeur → fenêtre plein écran.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + f"/all?entry={ids[0]}", wait_until="networkidle")
    page.wait_for_selector("[data-testid=reader-modal] article h1", timeout=15000)
    box = page.locator("[data-testid=reader-modal] article").bounding_box()
    results["mobile_fullscreen"] = box is not None and box["width"] >= 385
    page.click("[data-testid=reader-modal] button[aria-label='Retour à la liste']")
    page.wait_for_timeout(500)
    results["mobile_menu_visible"] = page.locator("[data-testid=density-menu]").is_visible()
    page.set_viewport_size({"width": 1400, "height": 900})

    # 10. Paramètres : sélecteur du défaut.
    page.goto(BASE + "/settings", wait_until="networkidle")
    page.wait_for_selector("[data-testid=default-view-grid]", timeout=30000)
    results["settings_shows_grid"] = page.locator("[data-testid=default-view-grid]").get_attribute("aria-checked") == "true"
    page.click("[data-testid=default-view-titles]")
    page.wait_for_timeout(700)
    results["settings_sets_titles"] = api(page, "GET", "/api/settings").json()["values"]["default_view_mode"] == "titles"
    page.screenshot(path="/out/114-settings-default.png")

    # 11. Hors ligne : le choix est appliqué tout de suite puis envoyé au retour du réseau.
    open_view(f"/feed/{feed_b['id']}")
    ctx.set_offline(True)
    results["offline_choice_applied"] = choose("titles") == "titles"
    ctx.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(4000)
    results["offline_choice_sent"] = api(page, "GET", f"/api/feeds/{feed_b['id']}").json()["view_mode"]

    # 12. Reprise des anciennes préférences d'appareil (pleine largeur + magazine) quand le serveur a le défaut d'origine.
    set_view_mode(page, "default", "compact")
    page.evaluate("localStorage.setItem('fluxus.listLayout', 'full'); localStorage.setItem('fluxus.fullDensity', 'magazine')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(4000)
    results["legacy_migrated"] = api(page, "GET", "/api/settings").json()["values"]["default_view_mode"]
    results["legacy_keys_removed"] = page.evaluate("localStorage.getItem('fluxus.listLayout') === null && localStorage.getItem('fluxus.fullDensity') === null")

    results["js_errors"] = errors[:5]


def cleanup(page):
    for (kind, id_), value in touched_modes.items():
        set_view_mode(page, kind, value or "inherit", id_)
    if "value" in original_default:
        set_view_mode(page, "default", original_default["value"])
    for entry_id, was_read in initial_read.items():
        api(page, "PATCH", f"/api/entries/{entry_id}", data=json.dumps({"read": was_read}))


main(run, results, failure_shot="119-layout-failure", settle_ms=2000, cleanup=cleanup)
