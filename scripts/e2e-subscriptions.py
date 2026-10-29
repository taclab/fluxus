"""Parcours E2E de l'écran « Abonnements » (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-subscriptions.py

Filtres, édition sur place (article complet, résumés automatiques, présentation, pause), actions
groupées, présentation d'un dossier, cartes sur téléphone. Les flux et dossiers touchés retrouvent
leurs réglages d'origine à la fin, même en cas d'échec.
"""

import json

from e2e_common import BASE, api, main

results: dict = {}
original_feeds: dict[int, dict] = {}
original_categories: dict[int, dict] = {}

RESTORED = ("disabled", "extraction_mode", "auto_summary", "summary_daily_cap", "fetch_interval_fixed_min", "view_mode")


def run(page, ctx, errors):
    feeds = api(page, "GET", "/api/feeds").json()
    categories = api(page, "GET", "/api/categories").json()
    active = [f for f in feeds if not f["disabled"] and f["category_id"] is not None]
    by_cat: dict[int, list] = {}
    for f in active:
        by_cat.setdefault(f["category_id"], []).append(f)
    cat_id, cat_feeds = max(by_cat.items(), key=lambda kv: len(kv[1]))
    target, second = cat_feeds[0], cat_feeds[1]
    for f in (target, second):
        original_feeds[f["id"]] = f
    original_categories[cat_id] = next(c for c in categories if c["id"] == cat_id)

    page.goto(BASE + "/abonnements", wait_until="networkidle")
    page.wait_for_selector("[data-testid=subs-table] [data-feed-row]", timeout=30000)
    page.wait_for_timeout(800)
    results["rows"] = page.locator("[data-feed-row]").count()
    results["rows_match_feeds"] = results["rows"] == len(feeds)
    results["groups"] = page.locator("[data-testid^=subs-group-]").count()
    page.screenshot(path="/out/120-subs-table.png", full_page=False)

    row = page.locator(f"[data-feed-row='{target['id']}']")

    # 1. Recherche et filtres.
    page.fill("[data-testid=subs-search]", target["title"][:12])
    page.wait_for_timeout(300)
    results["search_keeps_target"] = row.count() == 1
    results["search_narrows"] = page.locator("[data-feed-row]").count() < len(feeds)
    page.fill("[data-testid=subs-search]", "")
    page.select_option("[data-testid=subs-status]", "paused")
    page.wait_for_timeout(300)
    results["paused_filter_hides_active"] = row.count() == 0
    page.select_option("[data-testid=subs-status]", "all")
    page.select_option("[data-testid=subs-folder]", str(cat_id))
    page.wait_for_timeout(300)
    results["folder_filter"] = page.locator("[data-feed-row]").count() == len([f for f in feeds if f["category_id"] == cat_id])
    page.select_option("[data-testid=subs-folder]", "any")
    page.wait_for_timeout(300)

    # 2. Édition sur place.
    row.locator("select[aria-label^='Article complet']").select_option("http")
    page.wait_for_timeout(900)
    results["inline_extraction"] = api(page, "GET", f"/api/feeds/{target['id']}").json()["extraction_mode"]
    row.locator("[data-testid=subs-auto-summary]").click()
    page.wait_for_timeout(900)
    results["inline_auto_summary"] = api(page, "GET", f"/api/feeds/{target['id']}").json()["auto_summary"]
    cap = row.locator("input[aria-label^='Quota quotidien']")
    cap.fill("4")
    cap.press("Enter")
    page.wait_for_timeout(900)
    results["inline_cap"] = api(page, "GET", f"/api/feeds/{target['id']}").json()["summary_daily_cap"]
    row.locator("select[aria-label^='Intervalle']").select_option("180")
    page.wait_for_timeout(900)
    got = api(page, "GET", f"/api/feeds/{target['id']}").json()
    results["inline_interval"] = got["fetch_interval_fixed_min"]
    row.locator("select[aria-label^='Présentation de']").select_option("magazine")
    page.wait_for_timeout(900)
    results["inline_view_mode"] = api(page, "GET", f"/api/feeds/{target['id']}").json()["view_mode"]
    row.locator("[data-testid=subs-toggle-pause]").click()
    page.wait_for_timeout(900)
    results["inline_pause"] = api(page, "GET", f"/api/feeds/{target['id']}").json()["disabled"]
    results["row_shows_paused"] = "En pause" in row.inner_text()
    page.screenshot(path="/out/121-subs-inline.png")

    # 3. Présentation du dossier : visible dans la vue d'un flux qui hérite.
    group = page.locator(f"[data-testid=subs-group-{cat_id}]")
    group.locator("select").select_option("list")
    page.wait_for_timeout(900)
    results["folder_view_mode"] = next(c for c in api(page, "GET", "/api/categories").json() if c["id"] == cat_id)["view_mode"]

    # 4. Actions groupées : sélection de deux flux, reprise, puis présentation héritée.
    row.locator("input[type=checkbox]").first.click()
    page.locator(f"[data-feed-row='{second['id']}'] input[type=checkbox]").first.click()
    page.wait_for_selector("[data-testid=subs-bulk]")
    results["bulk_count"] = page.locator("[data-testid=subs-bulk]").inner_text().split("\n")[0]
    page.screenshot(path="/out/122-subs-bulk.png")
    page.click("[data-testid=bulk-pause]")
    page.wait_for_timeout(1000)
    both = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["bulk_pause"] = both[target["id"]]["disabled"] and both[second["id"]]["disabled"]
    page.click("[data-testid=bulk-resume]")
    page.wait_for_timeout(1000)
    page.select_option("[data-testid=bulk-view]", "inherit")
    page.wait_for_timeout(1000)
    both = {f["id"]: f for f in api(page, "GET", "/api/feeds").json()}
    results["bulk_resume"] = not both[target["id"]]["disabled"] and not both[second["id"]]["disabled"]
    results["bulk_inherit"] = both[target["id"]]["view_mode"] is None and both[second["id"]]["view_mode"] is None
    results["bulk_select_reset"] = page.locator("[data-testid=bulk-view]").input_value() == ""
    # Désabonnement : confirmation dans l'application (pas `window.confirm`), annulée ici.
    page.click("[data-testid=bulk-delete]")
    page.wait_for_selector("[data-testid=confirm-ok]", timeout=5000)
    results["unsubscribe_confirm"] = page.locator("[role=dialog]").inner_text().split("\n")[0]
    page.get_by_role("button", name="Annuler").click()
    page.wait_for_timeout(500)
    ids = {f["id"] for f in api(page, "GET", "/api/feeds").json()}
    results["unsubscribe_cancel_keeps"] = target["id"] in ids and second["id"] in ids and page.locator("[role=dialog]").count() == 0

    # 5. La vue du flux suit : présentation héritée du dossier (Liste).
    page.goto(BASE + f"/feed/{target['id']}", wait_until="networkidle")
    page.wait_for_selector("ul[data-density]", state="attached", timeout=30000)
    page.wait_for_timeout(500)
    results["feed_view_inherits_folder"] = page.locator("ul[data-density]").get_attribute("data-density")

    # 6. Téléphone : cartes, un appui ouvre la fenêtre du flux.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + "/abonnements", wait_until="networkidle")
    page.wait_for_selector("[data-testid=subs-cards] li button", timeout=30000)
    results["mobile_table_hidden"] = not page.locator("[data-testid=subs-table]").is_visible()
    page.screenshot(path="/out/123-subs-mobile.png")
    page.locator("[data-testid=subs-cards] li button").first.click()
    page.wait_for_selector("[role=dialog]", timeout=10000)
    results["mobile_opens_dialog"] = "Modifier le flux" in page.locator("[role=dialog]").inner_text()
    page.screenshot(path="/out/124-subs-dialog.png")
    page.set_viewport_size({"width": 1400, "height": 900})

    results["js_errors"] = errors[:5]


def cleanup(page):
    for fid, f in original_feeds.items():
        body = {k: f[k] for k in RESTORED}
        body["view_mode"] = body["view_mode"] or "inherit"
        body["fetch_interval_fixed_min"] = body["fetch_interval_fixed_min"] or 0
        api(page, "PUT", f"/api/feeds/{fid}", data=json.dumps(body))
    for cid, c in original_categories.items():
        api(page, "PUT", "/api/view-mode", data=json.dumps({"kind": "category", "id": cid, "view_mode": c["view_mode"] or "inherit"}))


main(run, results, failure_shot="129-subs-failure", settle_ms=2000, cleanup=cleanup)
