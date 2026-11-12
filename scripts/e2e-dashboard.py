"""Parcours E2E du tableau de bord (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-dashboard.py
"""

import json
import re
import time

from e2e_common import (
    BASE,
    Restorer,
    api,
    main,
    new_context,
    set_dashboard_layout,
    shot,
    wait_offline_ready,
    wait_synced,
)

results: dict = {}
restorer = Restorer()


def widget_ids(page):
    return page.eval_on_selector_all("[data-widget]", "els => els.map(e => e.dataset.widgetId)")


def run(page, ctx, errors):
    # Repart d'une disposition serveur vide ; article à côté de la liste pour le retour au tableau de bord.
    restorer.save_layout(page)
    restorer.view_mode(page, "default", "compact")
    set_dashboard_layout(page, "")
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=60000)
    wait_synced(page)
    page.wait_for_timeout(1500)

    categories = api(page, "GET", "/api/categories").json()
    results["categories"] = len(categories)
    results["news_widgets"] = page.locator("[data-widget-type=news]").count()
    results["stats_widget"] = page.locator("[data-widget-type=stats]").count()
    results["youtube_thumbs"] = page.locator("[data-widget-type=youtube] img[src*='i.ytimg.com']").count()
    results["dexie_version"] = page.evaluate("""() => new Promise(res => { const r = indexedDB.open('fluxus'); r.onsuccess = () => { res(r.result.version); r.result.close() } })""")
    shot(page, "40-dashboard-default")

    # Clic sur un article d'un widget catégorie → lecteur en contexte, retour → dashboard
    first_cat_widget = page.locator("[data-widget-type=news]:has([data-entry-id])").first
    first_cat_widget.locator("[data-entry-id]").first.click()
    page.wait_for_selector("article h1", timeout=10000)
    results["entry_url"] = page.url
    results["entry_url_ok"] = bool(re.search(r"/(category|feed|all)(/\d+)?\?entry=\d+", page.url))
    page.go_back()
    page.wait_for_selector("[data-widget]", timeout=10000)
    results["back_to_dashboard"] = page.url.rstrip("/") == BASE

    # Tous les articles → /all
    page.locator("nav button:has-text('Tous les articles')").click()
    page.wait_for_url("**/all**")
    page.wait_for_selector("[data-entry-id]", timeout=10000)
    results["all_url"] = page.url
    page.keyboard.press("h")
    page.wait_for_selector("[data-widget]", timeout=10000)
    results["h_shortcut_home"] = page.url.rstrip("/") == BASE

    # Tout marquer lu sur un widget
    # Un widget « non lus » se remplit avec les non-lus suivants : on vérifie que les articles affichés ont changé
    # et que ceux marqués sont bien lus côté serveur.
    # Le widget est choisi une fois, puis visé par son identifiant : « le deuxième widget qui a des
    # articles » ne désigne plus le même tant que les widgets finissent de se remplir.
    wid = page.locator("[data-widget-type=news]:has([data-entry-id])").nth(1).get_attribute("data-widget-id")
    w = page.locator(f"[data-widget-id='{wid}']")
    shown = page.eval_on_selector_all(f"[data-widget-id='{wid}'] [data-entry-id]", "els => els.map(e => Number(e.dataset.entryId))")
    w.locator("button[aria-label='Tout marquer comme lu']").click()
    for _ in range(40):  # attend l'état serveur, pas un délai fixe
        if all(api(page, "GET", f"/api/entries/{i}").json()["read"] for i in shown[:3]):
            break
        page.wait_for_timeout(250)
    shown_after = page.eval_on_selector_all(f"[data-widget-id='{wid}'] [data-entry-id]", "els => els.map(e => Number(e.dataset.entryId))")
    read_flags = [api(page, "GET", f"/api/entries/{i}").json()["read"] for i in shown[:3]]
    results["mark_all_read"] = {"shown": len(shown), "still_shown": len(set(shown) & set(shown_after)), "server_read": read_flags}

    # Mode personnalisation : menu Descendre, largeur pleine, retirer, ajouter
    page.click("[data-testid=toggle-edit]")
    page.wait_for_timeout(300)
    order0 = widget_ids(page)
    first = page.locator("[data-widget]").nth(1)  # deuxième widget (le premier est stats)
    first_id = first.get_attribute("data-widget-id")
    first.locator("button[aria-label='Options du widget']").click()
    page.locator("[role=menu] button:has-text('Descendre')").click()
    page.wait_for_timeout(300)
    order1 = widget_ids(page)
    results["menu_move_down"] = order0.index(first_id) == 1 and order1.index(first_id) == 2
    target = page.locator(f"[data-widget-id='{first_id}']")
    target.locator("button[aria-label='Options du widget']").click()
    page.locator("[role=menu] button[data-width='6']").click()
    page.wait_for_timeout(300)
    results["width_full"] = "md:col-span-6" in (page.locator(f"[data-widget-id='{first_id}']").locator("xpath=..").get_attribute("class") or "")
    with page.expect_request(lambda r: r.method == "PATCH" and "/api/settings" in r.url, timeout=5000):
        pass
    page.wait_for_timeout(800)
    shot(page, "41-dashboard-edit")

    # Ajouter un widget flux
    page.click("[data-testid=add-widget]")
    page.select_option("[data-testid=widget-type]", "news")
    feeds = api(page, "GET", "/api/feeds").json()
    feed_id = next(f["id"] for f in feeds if f["unread_count"] > 0)
    page.select_option("[data-testid=widget-source]", f"feed:{feed_id}")
    page.click("[data-testid=widget-submit]")
    page.wait_for_timeout(500)
    n_after_add = page.locator("[data-widget]").count()
    results["add_widget"] = n_after_add == len(order1) + 1

    # Retirer le widget stats
    stats = page.locator("[data-widget-type=stats]").first
    stats.locator("button[aria-label='Options du widget']").click()
    page.locator("[role=menu] button:has-text('Retirer')").click()
    page.wait_for_timeout(1500)
    results["remove_widget"] = page.locator("[data-widget-type=stats]").count() == 0

    # Drag & drop souris : déplacer le 3e widget en première position (les deux sont visibles à l'écran)
    # Fenêtre haute le temps du drag : la poignée et la cible doivent être toutes deux à l'écran.
    page.set_viewport_size({"width": 1400, "height": 2600})
    page.evaluate("document.querySelector('[data-widget]').closest('.overflow-y-auto').scrollTo(0, 0)")
    page.wait_for_timeout(300)
    ids = widget_ids(page)
    moved = ids[2]
    src = page.locator(f"[data-widget-id='{moved}'] [data-dnd-handle]")
    box = src.bounding_box()
    dst = page.locator(f"[data-widget-id='{ids[0]}']").bounding_box()
    page.evaluate("""() => { window.__ann = []; new MutationObserver(() => document.querySelectorAll('[id^=DndLiveRegion]').forEach(el => el.textContent && window.__ann.push(el.textContent))).observe(document.body, {subtree: true, childList: true, characterData: true}) }""")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 15, box["y"] + 15, steps=4)
    page.mouse.move(dst["x"] + 30, dst["y"] + 20, steps=25)
    page.wait_for_timeout(300)
    page.mouse.up()
    page.wait_for_timeout(500)
    ids2 = widget_ids(page)
    results["mouse_drag"] = {"before_index": 2, "after_index": ids2.index(moved), "announcements": page.evaluate("window.__ann.slice(-2)")}
    page.set_viewport_size({"width": 1400, "height": 900})

    # Drag clavier : saisir le 2e widget, flèche gauche, déposer
    handle = page.locator(f"[data-widget-id='{ids2[1]}'] [data-dnd-handle]")
    handle.focus()
    page.keyboard.press("Space")
    page.wait_for_timeout(150)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(150)
    page.keyboard.press("Space")
    page.wait_for_timeout(500)
    ids3 = widget_ids(page)
    results["keyboard_drag"] = {"before_index": 1, "after_index": ids3.index(ids2[1])}

    # Persistance : reload + serveur
    page.click("[data-testid=toggle-edit]")
    page.wait_for_timeout(1500)
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.wait_for_timeout(800)
    results["persist_reload"] = widget_ids(page) == ids3
    server_layout = json.loads(api(page, "GET", "/api/settings").json()["values"]["dashboard_layout"])
    results["persist_server"] = [w["id"] for w in server_layout["widgets"]] == ids3
    results["server_widget_count"] = len(server_layout["widgets"])

    # Conflit : le serveur a une version plus récente → elle gagne au reload
    remote = dict(server_layout)
    remote["widgets"] = list(reversed(server_layout["widgets"]))
    remote["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    # Un autre appareil connaît la version serveur courante et l'envoie (sinon 409, constat 16 de l'audit du 15/09).
    results["remote_patch_status"] = set_dashboard_layout(page, json.dumps(remote)).status
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.wait_for_timeout(1200)
    results["conflict_server_wins"] = widget_ids(page) == [w["id"] for w in remote["widgets"]]

    # Lecture seule : version future
    set_dashboard_layout(page, json.dumps({**remote, "version": 99}))
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.wait_for_timeout(1000)
    results["readonly_banner"] = page.locator("text=version plus récente").count() > 0
    results["readonly_edit_disabled"] = page.locator("[data-testid=toggle-edit]").is_disabled()
    # retour à une version normale
    set_dashboard_layout(page, json.dumps(remote))

    # Mobile + thème sombre + hors-ligne
    page.evaluate("localStorage.setItem('fluxus.theme','dark')")
    # Contexte neuf : synchronisation complète, d'où `load` et non `networkidle`.
    mobile = new_context(page.context.browser, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, storage_state=ctx.storage_state())
    mp = mobile.new_page()
    mp.goto(BASE, wait_until="load")
    mp.wait_for_selector("[data-widget]", timeout=60000)
    mp.wait_for_timeout(1500)
    widths = mp.eval_on_selector_all("[data-widget]", "els => els.map(e => Math.round(e.getBoundingClientRect().width))")
    results["mobile_full_width"] = all(w >= 340 for w in widths)
    shot(mp, "42-dashboard-mobile-dark")
    wait_offline_ready(mp)
    mobile.set_offline(True)
    mp.reload()
    mp.wait_for_selector("[data-widget]", timeout=15000)
    results["offline_widgets"] = mp.locator("[data-widget]").count()
    mp.click("[data-testid=toggle-edit]")
    mp.locator("[data-widget]").nth(0).locator("button[aria-label='Options du widget']").click()
    mp.locator("[role=menu] button:has-text('Descendre')").click()
    mp.wait_for_timeout(500)
    offline_order = widget_ids(mp)
    mobile.set_offline(False)
    mp.wait_for_timeout(3000)
    server_after = json.loads(api(mp, "GET", "/api/settings").json()["values"]["dashboard_layout"])
    results["offline_change_pushed"] = [w["id"] for w in server_after["widgets"]] == offline_order

    mobile.close()

    failed = [k for k, v in results.items() if v is False]
    assert not failed, f"contrôles en échec : {failed}"
    assert results["mark_all_read"]["server_read"] == [True] * len(results["mark_all_read"]["server_read"])
    assert results["mouse_drag"]["after_index"] == 0 and results["keyboard_drag"]["after_index"] == 0
    assert not errors, errors[:10]


main(run, results, failure_shot="dashboard-echec", settle_ms=0, cleanup=restorer.restore)
