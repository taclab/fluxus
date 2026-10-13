"""Parcours E2E de la sauvegarde complète (Playwright, image fluxus-browser).

À lancer sur une instance jetable (l'import remplace tout son contenu), par exemple la copie de
`CLAUDE.md` sur :8001 :

docker run --rm --network host -u 0 -e E2E_BASE=http://localhost:8001 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-backup.py

Export depuis Paramètres, lecture de l'en-tête, refus avec un mauvais mot de passe, import du même
fichier, resynchronisation de l'appareil (cache vidé par le changement d'époque).
"""

import gzip
import json

from e2e_common import BASE, PASSWORD, api, main, new_context, watch_errors

results: dict = {}


def run(page, ctx, errors):
    before = api(page, "GET", "/api/sync?limit=1").json()
    feeds_before = len(api(page, "GET", "/api/feeds").json())

    page.goto(BASE + "/settings", wait_until="networkidle")
    page.wait_for_selector("[data-testid=backup-export]", timeout=30000)
    page.locator("[data-testid=backup-export]").scroll_into_view_if_needed()
    page.screenshot(path="/out/130-backup-section.png")
    with page.expect_download(timeout=120000) as dl:
        page.click("[data-testid=backup-export]")
    path = "/out/e2e-backup.jsonl.gz"
    dl.value.save_as(path)
    results["download_name"] = dl.value.suggested_filename
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        text = fh.read()
    results["header_format"] = header["format"]
    results["header_feeds"] = header["counts"]["feeds"] == feeds_before
    results["no_secrets"] = "llm_api_key" not in text and "_password_hash" not in text

    # Choix du fichier → fenêtre de confirmation avec les comptes.
    page.set_input_files("[data-testid=backup-file]", path)
    page.wait_for_selector("[data-testid=backup-counts]", timeout=10000)
    results["dialog_counts"] = page.locator("[data-testid=backup-counts]").inner_text()
    results["confirm_disabled_initially"] = page.locator("[data-testid=backup-confirm]").is_disabled()
    page.screenshot(path="/out/131-backup-dialog.png")

    # Mauvais mot de passe : refus, rien ne change.
    page.fill("[data-testid=backup-password]", "mauvais-mot-de-passe")
    page.fill("[data-testid=backup-confirm-word]", "remplacer")
    page.click("[data-testid=backup-confirm]")
    page.wait_for_timeout(1500)
    results["wrong_password_keeps_dialog"] = page.locator("[data-testid=backup-counts]").count() == 1
    results["wrong_password_epoch_unchanged"] = api(page, "GET", "/api/sync?limit=1").json()["epoch"] == before["epoch"]

    # Second appareil : un changement fait hors ligne avant l'import ne doit pas atterrir sur la base importée.
    device_b = new_context(page.context.browser, storage_state=ctx.storage_state())
    pb = device_b.new_page()
    errors_b = watch_errors(pb)
    pb.goto(BASE + "/all", wait_until="networkidle")
    pb.wait_for_selector("[data-entry-id]", timeout=60000)
    pb.wait_for_timeout(1500)
    target = int(pb.locator("[data-entry-id]").first.get_attribute("data-entry-id"))
    read_before = api(page, "GET", f"/api/entries/{target}").json()["read"]
    device_b.set_offline(True)
    pb.locator(f"[data-entry-id='{target}'] button[aria-label^='Marquer']").first.click()
    pb.wait_for_timeout(500)
    results["device_b_outbox"] = pb.evaluate(
        """() => new Promise((ok) => { const r = indexedDB.open('fluxus'); r.onsuccess = () => {
             const c = r.result.transaction('outbox').objectStore('outbox').count(); c.onsuccess = () => ok(c.result) } })"""
    )

    # Import réel.
    page.fill("[data-testid=backup-password]", PASSWORD)
    page.click("[data-testid=backup-confirm]")
    page.wait_for_selector("[data-testid=backup-counts]", state="detached", timeout=120000)
    page.wait_for_timeout(3000)
    after = api(page, "GET", "/api/sync?limit=1").json()
    results["epoch_changed"] = after["epoch"] != before["epoch"]
    results["feeds_after"] = len(api(page, "GET", "/api/feeds").json()) == feeds_before
    results["local_epoch"] = page.evaluate(
        """() => new Promise((ok) => { const r = indexedDB.open('fluxus'); r.onsuccess = () => {
             const tx = r.result.transaction('meta'); const g = tx.objectStore('meta').get('sync.epoch');
             g.onsuccess = () => ok(g.result?.value ?? null) } })"""
    ) == after["epoch"]

    # Le second appareil revient en ligne : son changement en attente est abandonné, pas appliqué.
    device_b.set_offline(False)
    pb.evaluate("window.dispatchEvent(new Event('online'))")
    pb.wait_for_timeout(6000)
    results["device_b_change_not_applied"] = api(page, "GET", f"/api/entries/{target}").json()["read"] == read_before
    results["device_b_outbox_cleared"] = pb.evaluate(
        """() => new Promise((ok) => { const r = indexedDB.open('fluxus'); r.onsuccess = () => {
             const c = r.result.transaction('outbox').objectStore('outbox').count(); c.onsuccess = () => ok(c.result) } })"""
    ) == 0
    results["device_b_errors"] = errors_b[:3]
    device_b.close()

    # L'appareil a rechargé ses données : la liste s'affiche.
    page.goto(BASE + "/all", wait_until="networkidle")
    page.wait_for_selector("[data-entry-id]", timeout=60000)
    results["list_repopulated"] = page.locator("[data-entry-id]").count() > 0

    results["js_errors"] = errors[:5]


main(run, results, failure_shot="139-backup-failure", settle_ms=2000)
