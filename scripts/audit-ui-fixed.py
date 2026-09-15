"""Preuve des correctifs 10, 11, 12 et 16 de l'audit du 15/09/2026, sur l'instance d'audit isolée (jamais :8000).

Préparer la base comme dans docs/AUDIT-2026-09-15.md (audit-ui-seed.py), lancer l'API sur 18009 avec
FLUXUS_STATIC_DIR=web/dist, puis exécuter dans l'image fluxus-browser (--network host).
Les attentes sont l'inverse de scripts/audit-ui.py : le comportement corrigé.
"""
import asyncio
import json

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:18009"
PASSWORD = "audit-password-only"
LAYOUT_X = '{"version": 1, "updatedAt": "2026-09-15T12:00:00Z", "widgets": [{"id": "x", "type": "stats", "width": 6}]}'


async def main():
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--no-proxy-server"])
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        status = await (await ctx.request.get(BASE + "/api/auth/status")).json()
        if status.get("setup_required"):
            await ctx.request.post(BASE + "/api/auth/setup", data={"password": PASSWORD})
        login = await ctx.request.post(BASE + "/api/auth/login", data={"password": PASSWORD})
        token = (await login.json())["access_token"]
        headers = {"Authorization": "Bearer " + token}
        feeds = await (await ctx.request.get(BASE + "/api/feeds", headers=headers)).json()
        feed = next(f for f in feeds if f["title"] == "Audit UI 130")
        await ctx.request.post(BASE + "/api/entries/mark-read", headers=headers, data={"feed_id": feed["id"], "read": False})
        await ctx.add_init_script("localStorage.setItem('fluxus.token', " + json.dumps(token) + ")")

        # 10. Tout marquer comme lu : 130 non lus, 60 affichés → 0 restant côté serveur et localement
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto(BASE + f"/feed/{feed['id']}")
        await page.wait_for_selector("[data-entry-id]")
        await page.wait_for_timeout(2500)  # synchronisation initiale
        before = await page.locator("[data-entry-id]").count()
        header_count = await page.locator("h1 span").inner_text()
        await page.locator("[data-testid=mark-all-read]").click()
        await page.wait_for_timeout(1500)
        remaining = await (await ctx.request.get(BASE + f"/api/entries?feed={feed['id']}&status=unread&limit=200", headers=headers)).json()
        local_unread = await page.evaluate("""async () => { const req = indexedDB.open('fluxus'); return await new Promise(res => { req.onsuccess = () => { const tx = req.result.transaction('entries'); const c = tx.objectStore('entries').index('readFlag').count(0); c.onsuccess = () => res(c.result) } }) }""")
        results["mark_all_read"] = {"visible_before": before, "header_scope_count": header_count, "unread_remaining_server": len(remaining["items"]), "unread_remaining_local": local_unread}
        assert before == 60 and header_count == "130" and len(remaining["items"]) == 0 and local_unread == 0
        await ctx.request.post(BASE + "/api/entries/mark-read", headers=headers, data={"feed_id": feed["id"], "read": False})

        # 11. Recherche : 100 puis « Charger plus » → 130 ; l'état lu vient de la base locale
        await page.goto(BASE + "/search?q=Auditneedle")
        await page.wait_for_selector("[data-entry-id]")
        # l'état lu vient de la base locale : on attend que la synchronisation ait rapatrié la remise à « non lu »
        await page.wait_for_selector("[data-entry-id] [aria-label='Non lu']", timeout=20000)
        await page.wait_for_timeout(800)
        first_page = await page.locator("[data-entry-id]").count()
        load_more = page.get_by_role("button", name="Charger plus", exact=True)
        results["search"] = {"first_page": first_page, "load_more_buttons": await load_more.count()}
        await load_more.click()
        await page.wait_for_function("document.querySelectorAll('[data-entry-id]').length > 100", timeout=10000)
        results["search"]["after_load_more"] = await page.locator("[data-entry-id]").count()
        results["search"]["load_more_after"] = await load_more.count()
        assert first_page == 100 and results["search"]["after_load_more"] == 130 and results["search"]["load_more_after"] == 0
        # ouvrir un résultat le marque lu (base locale) : la pastille « Non lu » de sa ligne disparaît
        first_row = page.locator("[data-entry-id]").first
        unread_marker = first_row.locator("[aria-label='Non lu']")
        markers_before = await page.locator("[data-entry-id] [aria-label='Non lu']").count()
        assert await unread_marker.count() == 1
        await first_row.locator("button").click()
        await page.wait_for_selector("article h1")
        await page.wait_for_timeout(600)
        markers_after = await page.locator("[data-entry-id] [aria-label='Non lu']").count()
        results["search"]["unread_markers_before_open"] = markers_before
        results["search"]["unread_markers_after_open"] = markers_after
        assert await unread_marker.count() == 0 and markers_after == markers_before - 1

        # 12. Réponse d'extraction retardée : le favori posé pendant l'attente est conservé
        remote = await (await ctx.request.get(BASE + "/api/entries?q=Auditneedle&limit=200", headers=headers)).json()
        entry_id = remote["items"][5]["id"]
        await page.goto(BASE + f"/feed/{feed['id']}?entry={entry_id}")
        await page.wait_for_selector("article h1")
        await page.wait_for_timeout(400)
        original = await (await ctx.request.get(BASE + f"/api/entries/{entry_id}", headers=headers)).json()
        original.update(content_html="<p>Article complet synthétique</p>", extraction_status="done")
        release = asyncio.Event()
        entered = asyncio.Event()

        async def delayed_content(route):
            entered.set()
            await release.wait()
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(original))

        await page.route("**/fetch-content", delayed_content)
        await page.get_by_role("button", name="Récupérer l’article complet (f)", exact=True).click()
        await entered.wait()
        await page.get_by_role("button", name="Ajouter aux favoris (s)", exact=True).click()
        await page.wait_for_timeout(300)
        assert await page.get_by_role("button", name="Retirer des favoris (s)", exact=True).count() == 1
        release.set()
        await page.wait_for_timeout(700)
        kept = await page.get_by_role("button", name="Retirer des favoris (s)", exact=True).count() == 1
        content_shown = "Article complet synthétique" in await page.locator("article").inner_text()
        results["extraction_keeps_local_star"] = {"star_kept": kept, "content_applied": content_shown}
        assert kept and content_shown
        await page.unroute("**/fetch-content")

        # 16. Disposition : un autre appareil écrit pendant que celui-ci édite → 409, adoption serveur, toast
        await page.goto(BASE + "/")
        await page.wait_for_selector("[data-widget]")
        await page.wait_for_timeout(1200)
        settings = await (await ctx.request.get(BASE + "/api/settings", headers=headers)).json()
        other = await ctx.request.patch(BASE + "/api/settings", headers=headers, data={"values": {"dashboard_layout": LAYOUT_X}, "dashboard_layout_base_version": settings["dashboard_layout_version"]})
        assert other.status == 200
        await page.locator("[data-testid=toggle-edit]").click()
        await page.locator("[data-testid=add-widget]").click()
        await page.select_option("[data-testid=widget-type]", "starred")
        await page.locator("[data-testid=widget-submit]").click()
        await page.wait_for_timeout(2500)  # délai d'écriture 1 s + requête
        toast_seen = await page.get_by_text("Disposition mise à jour depuis un autre appareil").count() >= 1
        widgets_after = await page.locator("[data-widget]").count()
        server_layout = (await (await ctx.request.get(BASE + "/api/settings", headers=headers)).json())["values"]["dashboard_layout"]
        results["dashboard_conflict"] = {"toast": toast_seen, "widgets_after": widgets_after, "server_kept_other_device": server_layout == LAYOUT_X}
        assert toast_seen and widgets_after == 1 and server_layout == LAYOUT_X
        # une nouvelle sauvegarde depuis la bonne base passe et incrémente
        await page.locator("[data-testid=add-widget]").click()
        await page.select_option("[data-testid=widget-type]", "starred")
        await page.locator("[data-testid=widget-submit]").click()
        await page.wait_for_timeout(2500)
        after = await (await ctx.request.get(BASE + "/api/settings", headers=headers)).json()
        results["dashboard_conflict"]["version_after_resave"] = after["dashboard_layout_version"]
        results["dashboard_conflict"]["server_widgets_after_resave"] = len(json.loads(after["values"]["dashboard_layout"])["widgets"])
        assert after["dashboard_layout_version"] == settings["dashboard_layout_version"] + 2 and results["dashboard_conflict"]["server_widgets_after_resave"] == 2

        results["uncaught_page_errors"] = errors
        assert not errors
        print(json.dumps(results, indent=2, ensure_ascii=False))
        await browser.close()


asyncio.run(main())
