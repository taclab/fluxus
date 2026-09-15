"""Parcours synthétiques sur l'instance d'audit : jamais sur :8000.

Exécuter audit-ui-seed.py d'abord ; lancer dans l'image fluxus-browser, --network host.
"""
import asyncio
import json

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:18009"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--no-proxy-server"])
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        login = await ctx.request.post(BASE + "/api/auth/login", data={"password": "audit-password-only"})
        token = (await login.json())["access_token"]
        headers = {"Authorization": "Bearer " + token}
        feeds = await (await ctx.request.get(BASE + "/api/feeds", headers=headers)).json()
        feed = next(f for f in feeds if f["title"] == "Audit UI 130")
        await ctx.request.post(BASE + "/api/entries/mark-read", headers=headers, data={"feed_id": feed["id"], "read": False})
        await ctx.add_init_script("localStorage.setItem('fluxus.token', " + json.dumps(token) + ")")
        await page.goto(BASE + f"/feed/{feed['id']}")
        await page.wait_for_selector("[data-entry-id]")
        await page.wait_for_timeout(500)
        before = await page.locator("[data-entry-id]").count()
        await page.get_by_role("button", name="Tout marquer comme lu", exact=True).click()
        await page.wait_for_timeout(700)
        remaining = await (await ctx.request.get(BASE + f"/api/entries?feed={feed['id']}&status=unread&limit=200", headers=headers)).json()
        assert before == 60 and len(remaining["items"]) == 70
        results = {"mark_all_read": {"visible": before, "unread_remaining": len(remaining["items"])}}

        await page.goto(BASE + "/search?q=Auditneedle")
        await page.wait_for_timeout(1000)
        count = await page.locator("[data-entry-id]").count()
        remote = await (await ctx.request.get(BASE + "/api/entries?q=Auditneedle&limit=200", headers=headers)).json()
        results["search"] = {"displayed": count, "server_matches": len(remote["items"]),
            "load_more_buttons": await page.get_by_role("button", name="Charger plus", exact=True).count()}
        assert count == 100 and len(remote["items"]) == 130

        entry_id = remote["items"][0]["id"]
        await page.goto(BASE + f"/feed/{feed['id']}?entry={entry_id}")
        await page.wait_for_selector("article h1")
        await page.wait_for_timeout(400)
        response = await ctx.request.get(BASE + f"/api/entries/{entry_id}", headers=headers)
        original = await response.json()
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
        await page.wait_for_timeout(500)
        lost = await page.get_by_role("button", name="Ajouter aux favoris (s)", exact=True).count() == 1
        results["extraction_response_reverts_new_star_locally"] = lost
        assert lost

        await page.set_viewport_size({"width": 390, "height": 844})
        await ctx.set_offline(True)
        await page.reload()
        await page.wait_for_selector("article h1")
        results["mobile_offline_reader"] = await page.locator("article h1").inner_text()
        results["uncaught_page_errors"] = errors
        assert not errors
        print(json.dumps(results, indent=2, ensure_ascii=False))
        await browser.close()


asyncio.run(main())
