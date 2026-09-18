"""Parcours E2E du glisser-déposer de la barre latérale (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-sidebar.py
"""

import json

from e2e_common import BASE, api, login, main, new_context

HOLD_MS = 450
results: dict = {}


def center(loc):
    b = loc.bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def long_drag(page, source, target, offset_y=0):
    """Clic long sur `source`, puis déplacement progressif jusqu'à `target` (+offset_y), relâchement."""
    target.scroll_into_view_if_needed()
    source.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    sx, sy = center(source)
    tx, ty = center(target)
    ty += offset_y
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.wait_for_timeout(HOLD_MS)
    page.mouse.move(sx + 2, sy + 2)
    page.wait_for_timeout(80)
    steps = 14
    for i in range(1, steps + 1):
        page.mouse.move(sx + (tx - sx) * i / steps, sy + (ty - sy) * i / steps)
        page.wait_for_timeout(35)
    page.wait_for_timeout(250)
    page.mouse.up()
    page.wait_for_timeout(1500)


def nav_feed_order(page, category_id):
    return page.locator(f"nav [data-category-id='{category_id}'] [data-feed-id]").evaluate_all("els => els.map(e => Number(e.dataset.feedId))")


def nav_category_order(page):
    return page.locator("nav [data-category-id]").evaluate_all("els => els.map(e => Number(e.dataset.categoryId))")


def run(page, ctx, errors):
    cats = api(page, "GET", "/api/categories").json()
    feeds = api(page, "GET", "/api/feeds").json()
    by_cat: dict = {}
    for f in feeds:
        by_cat.setdefault(f["category_id"], []).append(f)
    # deux dossiers d'au moins deux flux, dépliés
    rich = [c for c in cats if len(by_cat.get(c["id"], [])) >= 2][:2]
    assert len(rich) == 2, "il faut deux dossiers d'au moins deux flux"
    cat_a, cat_b = rich
    page.evaluate("localStorage.setItem('fluxus.collapsed', '[]')")
    page.reload(wait_until="networkidle")
    page.wait_for_selector("nav [data-feed-id]", timeout=30000)
    page.wait_for_timeout(1000)
    initial_state = {"cats": [(c["id"], c["position"]) for c in cats], "feeds": [(f["id"], f["category_id"], f["position"]) for f in feeds]}

    # 1. Clic court : la vue s'ouvre, aucun glisser
    first_feed = page.locator("nav [data-feed-id]").first
    first_feed_id = int(first_feed.get_attribute("data-feed-id"))
    first_feed.click()
    page.wait_for_timeout(800)
    results["short_click_opens_view"] = f"/feed/{first_feed_id}" in page.url
    results["short_click_no_reorder"] = nav_feed_order(page, cat_a["id"]) == [f["id"] for f in sorted(by_cat[cat_a["id"]], key=lambda f: (f["position"], f["title"]))]

    # 2. Réordonner deux flux dans le dossier A : le 2e passe en 1er
    order = nav_feed_order(page, cat_a["id"])
    a0, a1 = order[0], order[1]
    long_drag(page, page.locator(f"nav [data-feed-id='{a1}']"), page.locator(f"nav [data-feed-id='{a0}']"), offset_y=-4)
    new_order = nav_feed_order(page, cat_a["id"])
    results["feed_reorder_dom"] = new_order[:2] == [a1, a0]
    api_feeds = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["feed_reorder_api"] = api_feeds[a1]["position"] < api_feeds[a0]["position"] and api_feeds[a1]["category_id"] == cat_a["id"]
    page.screenshot(path="/out/80-sidebar-reorder.png")

    # 3. Déplacer un flux de A vers l'en-tête du dossier B : change de dossier, en tête
    moved = a0
    header_b = page.locator(f"nav [data-category-id='{cat_b['id']}'] button:has-text('{cat_b['title']}')").first
    long_drag(page, page.locator(f"nav [data-feed-id='{moved}']"), header_b)
    results["move_to_folder_dom"] = nav_feed_order(page, cat_b["id"])[:1] == [moved]
    api_feeds = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["move_to_folder_api"] = api_feeds[moved]["category_id"] == cat_b["id"] and api_feeds[moved]["position"] <= min(f["position"] for f in api_feeds.values() if f["category_id"] == cat_b["id"])
    results["source_folder_shrunk"] = moved not in nav_feed_order(page, cat_a["id"])

    # 4. Sortir ce flux à la racine, entre deux dossiers : dépôt sur la partie haute de l'en-tête du 2e dossier
    root_before = page.locator("nav [data-root-item]").evaluate_all("els => els.map(e => e.dataset.rootItem)")
    second_cat = [r for r in root_before if r.startswith("cat:")][1]
    hdr2 = page.locator(f"nav [data-root-item='{second_cat}'] button").nth(1)
    box = hdr2.bounding_box()
    long_drag(page, page.locator(f"nav [data-feed-id='{moved}']"), hdr2, offset_y=-box["height"] * 0.32)
    root_after = page.locator("nav [data-root-item]").evaluate_all("els => els.map(e => e.dataset.rootItem)")
    results["root_feed_between_folders_dom"] = f"feed:{moved}" in root_after and root_after.index(f"feed:{moved}") == root_after.index(second_cat) - 1
    api_feeds = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    api_cats = {c["id"]: c for c in api(page, "GET", "/api/categories").json()}
    first_cat_id = int([r for r in root_before if r.startswith("cat:")][0].split(":")[1])
    second_cat_id = int(second_cat.split(":")[1])
    results["root_feed_between_folders_api"] = api_feeds[moved]["category_id"] is None and api_cats[first_cat_id]["position"] < api_feeds[moved]["position"] < api_cats[second_cat_id]["position"]
    page.screenshot(path="/out/81-sidebar-root-feed.png")

    # 4b. Un dossier se déplace au-dessus de ce flux racine (les deux sont des éléments de la racine)
    title2 = api_cats[second_cat_id]["title"]
    long_drag(page, page.locator(f"nav [data-root-item='{second_cat}'] button:has-text('{title2}')").first, page.locator(f"nav [data-root-item='feed:{moved}']"), offset_y=-4)
    root_after = page.locator("nav [data-root-item]").evaluate_all("els => els.map(e => e.dataset.rootItem)")
    results["folder_over_root_feed_dom"] = root_after.index(second_cat) == root_after.index(f"feed:{moved}") - 1

    # 4c. Le flux racine rentre dans un dossier par le bas de son en-tête
    hdr1 = page.locator(f"nav [data-root-item='cat:{first_cat_id}'] button").nth(1)
    box = hdr1.bounding_box()
    long_drag(page, page.locator(f"nav [data-root-item='feed:{moved}']"), hdr1, offset_y=box["height"] * 0.25)
    api_feeds = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["root_feed_into_folder_api"] = api_feeds[moved]["category_id"] == first_cat_id and api_feeds[moved]["position"] <= min(f["position"] for f in api_feeds.values() if f["category_id"] == first_cat_id)
    results["no_uncategorized_zone"] = page.locator("[data-testid=uncategorized-zone]").count() == 0

    # 5. Réordonner les dossiers : le 2e passe devant le 1er
    corder = nav_category_order(page)
    c0, c1 = corder[0], corder[1]
    title1 = next(c["title"] for c in cats if c["id"] == c1)
    title0 = next(c["title"] for c in cats if c["id"] == c0)
    long_drag(page, page.locator(f"nav [data-category-id='{c1}'] button:has-text('{title1}')").first, page.locator(f"nav [data-category-id='{c0}'] button:has-text('{title0}')").first, offset_y=-6)
    results["category_reorder_dom"] = nav_category_order(page)[:2] == [c1, c0]
    api_cats = {c["id"]: c for c in api(page, "GET", "/api/categories").json()}
    results["category_reorder_api"] = api_cats[c1]["position"] < api_cats[c0]["position"]

    # 6. Flux intelligents : deux définitions, permutation
    for sf in api(page, "GET", "/api/smart-feeds").json()["items"]:
        api(page, "DELETE", f"/api/smart-feeds/{sf['id']}")
    s1 = api(page, "POST", "/api/smart-feeds", data=json.dumps({"name": "E2E Ordre 1", "keywords_any": ["zzqx1"]})).json()
    s2 = api(page, "POST", "/api/smart-feeds", data=json.dumps({"name": "E2E Ordre 2", "keywords_any": ["zzqx2"]})).json()
    page.reload(wait_until="networkidle")
    page.wait_for_selector("nav [data-smartfeed-id]", timeout=30000)
    page.wait_for_timeout(1000)
    long_drag(page, page.locator(f"nav [data-smartfeed-id='{s2['id']}']"), page.locator(f"nav [data-smartfeed-id='{s1['id']}']"), offset_y=-4)
    sf_dom = page.locator("nav [data-smartfeed-id]").evaluate_all("els => els.map(e => Number(e.dataset.smartfeedId))")
    results["smart_reorder_dom"] = sf_dom[:2] == [s2["id"], s1["id"]]
    sf_api = [s["id"] for s in api(page, "GET", "/api/smart-feeds").json()["items"]]
    results["smart_reorder_api"] = sf_api[:2] == [s2["id"], s1["id"]]

    # 7. Persistance après rechargement
    page.reload(wait_until="networkidle")
    page.wait_for_selector("nav [data-feed-id]", timeout=30000)
    page.wait_for_timeout(1500)
    results["persist_categories"] = nav_category_order(page)[:2] == [c1, c0]
    results["persist_feed_in_folder"] = page.locator(f"nav [data-category-id='{first_cat_id}'] [data-feed-id='{moved}']").count() == 1
    page.screenshot(path="/out/82-sidebar-after.png")

    # 8. Mobile (tactile via CDP) : le tiroir défile au geste rapide, l'appui long saisit et réordonne
    mctx = new_context(ctx.browser, viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    mpage = mctx.new_page()
    mpage.on("pageerror", lambda e: errors.append(str(e)))
    login(mpage, settle_ms=0)
    mpage.evaluate("localStorage.setItem('fluxus.collapsed', '[]')")
    mpage.goto(BASE + "/all", wait_until="networkidle")
    mpage.wait_for_timeout(800)
    mpage.locator("button[aria-label='Menu']").first.click()
    mpage.wait_for_timeout(700)
    cdp = mctx.new_cdp_session(mpage)

    def touch(kind, points):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": 1} for x, y in points]})

    scroller = mpage.locator("nav .overflow-y-auto").first
    fx, fy = center(mpage.locator("nav [data-feed-id]").nth(2))
    touch("touchStart", [(fx, fy)])
    for i in range(1, 8):
        touch("touchMove", [(fx, fy - 30 * i)])
        mpage.wait_for_timeout(16)
    touch("touchEnd", [])
    mpage.wait_for_timeout(600)
    results["mobile_touch_scrolls"] = scroller.evaluate("el => el.scrollTop") > 0
    scroller.evaluate("el => (el.scrollTop = 0)")
    mpage.wait_for_timeout(300)
    mcat = mpage.locator("nav [data-category-id]").first
    mids = mcat.locator("[data-feed-id]").evaluate_all("els => els.map(e => e.dataset.feedId)")
    sx, sy = center(mcat.locator(f"[data-feed-id='{mids[1]}']"))
    tx, ty = center(mcat.locator(f"[data-feed-id='{mids[0]}']"))
    touch("touchStart", [(sx, sy)])
    mpage.wait_for_timeout(500)
    for i in range(1, 11):
        touch("touchMove", [(sx + (tx - sx) * i / 10, sy + (ty - sy) * i / 10 - 3)])
        mpage.wait_for_timeout(40)
    mpage.wait_for_timeout(300)
    mpage.screenshot(path="/out/83-sidebar-mobile-drag.png")
    touch("touchEnd", [])
    mpage.wait_for_timeout(1500)
    results["mobile_touch_drag_reorders"] = mcat.locator("[data-feed-id]").evaluate_all("els => els.slice(0, 2).map(e => e.dataset.feedId)") == [mids[1], mids[0]]
    results["mobile_no_dialog_on_long_press"] = mpage.locator("[role=dialog]").count() == 0
    mctx.close()

    # Restauration de l'état initial (dossiers, flux) et nettoyage
    api(page, "POST", "/api/subscriptions/reorder", data=json.dumps({
        "categories": [{"id": i, "position": p} for i, p in initial_state["cats"]],
        "feeds": [{"id": i, "category_id": c, "position": p} for i, c, p in initial_state["feeds"]],
    }))
    for sid in (s1["id"], s2["id"]):
        api(page, "DELETE", f"/api/smart-feeds/{sid}")
    results["js_errors"] = errors[:5]


main(run, results, failure_shot="89-sidebar-failure", settle_ms=2000, viewport={"width": 1400, "height": 1000})
