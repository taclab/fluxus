"""Parcours « lecture » : correctifs 7, 9 et 12 de l'audit du 12/11/2026.

- « Tout marquer comme lu » ne marque que ce que l'appareil connaît : un article redevenu non lu sur
  le serveur après la dernière synchronisation (comme un article tout juste arrivé) reste non lu ;
- un vieil article ouvert, donc marqué lu, survit à la purge de la synchronisation suivante ;
- après un changement d'adresse du serveur, la connexion aboutit sans ressaisir le mot de passe.

Le parcours rétablit ce qu'il modifie (états lu/non lu, réglage de rétention).
"""

import json

from e2e_common import BASE, PASSWORD, api, main, new_context, shot, wait_synced

results: dict = {}
state: dict = {"unread": [], "reread": None, "opened": None, "prefetch": None}

DAY = 24 * 3600 * 1000

# Lectures de la base locale, par clés uniquement là où c'est possible.
LOCAL_JS = """(q) => new Promise((ok) => {
  const r = indexedDB.open('fluxus')
  r.onsuccess = () => {
    const db = r.result
    const out = []
    const now = Date.now()
    const store = db.transaction('entries').objectStore('entries')
    const req = q.kind === 'feedUnread'
      ? store.index('[feed_id+readFlag+publishedTs]').openKeyCursor(IDBKeyRange.bound([q.feed, 0, -Infinity], [q.feed, 0, Infinity]))
      : q.kind === 'get'
        ? store.openCursor(IDBKeyRange.only(q.id))
        : store.index('[readFlag+publishedTs]').openCursor(IDBKeyRange.bound([q.read, -Infinity], [q.read, now - q.olderThanDays * 86400000]))
    req.onsuccess = () => {
      const c = req.result
      if (!c) { db.close(); return ok(out) }
      if (q.kind === 'feedUnread') out.push(c.primaryKey)
      else if (q.kind === 'get') out.push({ id: c.value.id, read: c.value.read })
      else if (!c.value.starred && (q.read === 0 || now - c.value.updatedTs > 86400000)) out.push({ id: c.value.id, feed_id: c.value.feed_id })
      if (out.length >= (q.limit || 100000)) { db.close(); return ok(out) }
      c.continue()
    }
  }
})"""

META_JS = """(key) => new Promise((ok) => {
  const r = indexedDB.open('fluxus')
  r.onsuccess = () => {
    const g = r.result.transaction('meta').objectStore('meta').get(key)
    g.onsuccess = () => { ok(g.result ? g.result.value : null); r.result.close() }
  }
})"""


def last_sync(page):
    return page.evaluate(META_JS, "sync.lastSyncAt")


def wait_sync_idle(page, quiet_ms: int = 3000) -> None:
    """Attend qu'aucune passe ne se termine pendant `quiet_ms` : un chargement de page en lance une."""
    seen, since = last_sync(page), 0
    while since < quiet_ms:
        page.wait_for_timeout(500)
        current = last_sync(page)
        seen, since = (current, 0) if current != seen else (seen, since + 500)


def run_sync_pass(page) -> None:
    """Déclenche une passe complète (pull puis purge) et attend qu'elle soit terminée."""
    wait_sync_idle(page)
    before = last_sync(page)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    for _ in range(240):
        if last_sync(page) != before:
            break
        page.wait_for_timeout(500)
    assert last_sync(page) != before, "la synchronisation ne s'est pas terminée"
    wait_sync_idle(page, 1500)


def local(page, **query):
    return page.evaluate(LOCAL_JS, query)


def server_read(page, entry_id: int) -> bool:
    return api(page, "GET", f"/api/entries/{entry_id}").json()["read"]


def patch_read(page, entry_id: int, read: bool) -> None:
    r = api(page, "PATCH", f"/api/entries/{entry_id}", data=json.dumps({"read": read}))
    assert r.ok, f"PATCH entrée {entry_id} : {r.status}"


def login_after_address_change(browser) -> None:
    """Constat 12 : l'adresse saisie diffère de l'adresse mémorisée, la page se recharge."""
    ctx = new_context(browser)
    page = ctx.new_page()
    page.goto(BASE, wait_until="load")
    page.evaluate("localStorage.setItem('fluxus.serverUrl', 'http://localhost:9')")
    page.reload(wait_until="load")
    page.click("text=Changer l’adresse du serveur")
    page.fill("input[type=url]", BASE)
    page.fill("input[type=password]", PASSWORD)
    page.wait_for_selector("button[type=submit]:not([disabled])", timeout=15000)
    page.click("button[type=submit]")
    page.wait_for_selector("[data-widget]", timeout=60000)
    results["connexion_après_changement_d_adresse"] = True
    assert page.evaluate("sessionStorage.getItem('fluxus.login.handoff')") is None, "mot de passe resté dans le stockage de session"
    ctx.close()


def mark_all_read(page) -> None:
    """Constat 7."""
    # Un flux avec quelques non lus connus de l'appareil (sous le seuil de confirmation) et un article lu.
    feed_id = unread = reread = None
    for feed in sorted(api(page, "GET", "/api/feeds").json(), key=lambda f: f["unread_count"]):
        if not 2 <= feed["unread_count"] <= 40:
            continue
        ids = local(page, kind="feedUnread", feed=feed["id"])
        already_read = api(page, "GET", f"/api/entries?feed={feed['id']}&status=read&limit=1").json()["items"]
        if len(ids) >= 2 and already_read:
            feed_id, unread, reread = feed["id"], ids, already_read[0]["id"]
            break
    assert feed_id is not None, "aucun flux avec 2 à 40 non lus connus de l'appareil et un article lu"

    page.goto(f"{BASE}/feed/{feed_id}", wait_until="load")
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    wait_sync_idle(page)  # la passe lancée par le chargement est finie : l'appareil ne relira rien avant le clic

    # Un article lu de ce flux redevient non lu sur le serveur, à l'insu de l'appareil : pour lui,
    # c'est la même situation qu'un article arrivé depuis la dernière synchronisation.
    state["unread"], state["reread"] = unread, reread
    patch_read(page, reread, False)

    page.click("button[aria-label='Tout marquer comme lu']")
    for _ in range(60):
        if not local(page, kind="feedUnread", feed=feed_id):
            break
        page.wait_for_timeout(250)
    assert not local(page, kind="feedUnread", feed=feed_id), "des articles connus sont restés non lus localement"

    results["marqués_sur_le_serveur"] = sum(server_read(page, i) for i in unread)
    assert results["marqués_sur_le_serveur"] == len(unread), "le serveur n'a pas marqué tous les articles connus"
    results["article_inconnu_resté_non_lu"] = not server_read(page, reread)
    assert results["article_inconnu_resté_non_lu"], "un article que l'appareil ne connaissait pas comme non lu a été marqué lu"
    shot(page, "reading-mark-all")


def old_article_survives_retention(page) -> None:
    """Constat 9."""
    values = api(page, "GET", "/api/settings").json()["values"]
    state["prefetch"] = values.get("sync_prefetch_days") or "30"
    candidates = local(page, kind="old", read=0, olderThanDays=3, limit=1)
    assert candidates, "aucun article non lu de plus de 3 jours sur l'appareil"
    entry = candidates[0]
    state["opened"] = entry["id"]
    purgeable_before = len(local(page, kind="old", read=1, olderThanDays=3))

    # Rétention d'un jour : tout article lu de plus d'un jour devient purgeable à la prochaine passe.
    r = api(page, "PATCH", "/api/settings", data=json.dumps({"values": {"sync_prefetch_days": "1"}}))
    assert r.ok, f"réglage de rétention : {r.status}"

    page.goto(f"{BASE}/feed/{entry['feed_id']}?entry={entry['id']}", wait_until="load")
    page.wait_for_selector("article h1", timeout=15000)
    title = page.locator("article h1").inner_text()
    for _ in range(40):
        if local(page, kind="get", id=entry["id"])[0]["read"]:
            break
        page.wait_for_timeout(250)
    assert local(page, kind="get", id=entry["id"])[0]["read"], "l'article ouvert n'a pas été marqué lu"

    run_sync_pass(page)

    results["purge_exécutée"] = {"purgeables_avant": purgeable_before, "après": len(local(page, kind="old", read=1, olderThanDays=3))}
    assert results["purge_exécutée"]["après"] == 0, "la purge n'a pas tourné : le contrôle ne prouverait rien"
    results["article_ouvert_conservé"] = bool(local(page, kind="get", id=entry["id"])) and page.locator("article h1").inner_text() == title
    assert results["article_ouvert_conservé"], "l'article ouvert a disparu du lecteur après la synchronisation"
    assert page.locator("text=Article introuvable sur cet appareil").count() == 0
    shot(page, "reading-retention")


def run(page, ctx, errors) -> None:
    login_after_address_change(ctx.browser)
    wait_synced(page)
    mark_all_read(page)
    old_article_survives_retention(page)
    results["erreurs_js"] = errors[:5]
    assert not errors, f"erreurs JavaScript : {errors[:3]}"


def cleanup(page) -> None:
    if state["prefetch"] is not None:
        api(page, "PATCH", "/api/settings", data=json.dumps({"values": {"sync_prefetch_days": state["prefetch"]}}))
    if state["unread"]:
        api(page, "POST", "/api/entries/mark-read", data=json.dumps({"entry_ids": state["unread"], "read": False}))
    if state["reread"] is not None:
        patch_read(page, state["reread"], True)
    if state["opened"] is not None:
        patch_read(page, state["opened"], False)


main(run, results, failure_shot="reading-failure", cleanup=cleanup)
