"""Harnais commun des parcours E2E Playwright (image `fluxus-browser`).

Lancement, pour n'importe quel script du dossier :

    docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out \\
      fluxus-browser python /scripts/e2e-<nom>.py

- `--network host` : l'API de développement écoute sur localhost:8000 ;
- `-u 0` : l'image tourne en `pwuser`, incapable d'écrire les captures dans `/out` ;
- `bypass_csp=True` dans le contexte : la CSP du site interdit `eval`, dont Playwright a besoin
  pour `wait_for_function` et certains `evaluate`.
Variables : `E2E_BASE` (défaut http://localhost:8000), `E2E_PASSWORD`, `E2E_OUT` (défaut /out).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE = os.environ.get("E2E_BASE", "http://localhost:8000")
PASSWORD = os.environ.get("E2E_PASSWORD", "motdepasse-test")
OUT = os.environ.get("E2E_OUT", "/out")

DEFAULT_CONTEXT = {"viewport": {"width": 1400, "height": 900}, "locale": "fr-FR", "bypass_csp": True}


def api(page: Page, method: str, path: str, **kw):
    """Appel API authentifié avec le token de la session du navigateur."""
    token = page.evaluate("localStorage.getItem('fluxus.token')")

    def send():
        return page.request.fetch(BASE + path, method=method, headers={"Authorization": f"Bearer {token}", "content-type": "application/json"}, **kw)

    try:
        return send()
    except Exception as exc:  # noqa: BLE001
        # Connexion keep-alive fermée par le serveur entre deux appels : la requête n'est pas partie.
        # Une lecture se rejoue sans risque ; une écriture, non.
        if method == "GET" and "ECONNRESET" in str(exc):
            return send()
        raise


SYNCED_JS = """() => new Promise((ok) => {
  const r = indexedDB.open('fluxus')
  r.onsuccess = () => {
    const db = r.result
    if (!db.objectStoreNames.contains('meta')) { db.close(); return ok(false) }
    const g = db.transaction('meta').objectStore('meta').get('sync.since')
    g.onsuccess = () => { ok(Boolean(g.result)); db.close() }
  }
})"""


def wait_synced(page: Page, timeout_s: int = 300) -> None:
    """Attend la fin de la première synchronisation complète (`sync.since` n'est posé qu'après une passe
    entière) : sur une copie de base réelle, un contexte neuf télécharge des milliers d'articles et les
    vues locales restent incomplètes d'ici là."""
    deadline = time.time() + timeout_s
    while not page.evaluate(SYNCED_JS):
        assert time.time() < deadline, "synchronisation initiale inachevée"
        page.wait_for_timeout(1000)


def wait_offline_ready(page: Page) -> None:
    """Avant `set_offline(True)` puis rechargement : service worker actif (sans lui, la page ne se charge
    pas hors ligne) et base locale complète."""
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    wait_synced(page)


def set_view_mode(page: Page, kind: str, view_mode: str, id_: int | None = None) -> None:
    """Présentation d'une vue (`feed`, `category`, `smart`) ou du défaut (`default`, sans `id_`).
    L'époque de synchro est obligatoire sur une instance déjà restaurée (409 sinon)."""
    epoch = api(page, "GET", "/api/sync?limit=1").json()["epoch"]
    r = api(page, "PUT", "/api/view-mode", data=json.dumps({"kind": kind, "id": id_, "view_mode": view_mode, "epoch": epoch}))
    assert r.ok, f"présentation {kind}:{id_} → {view_mode} : {r.status} {r.text()[:200]}"


def set_dashboard_layout(page: Page, layout_json: str):
    """Écrit la disposition comme le ferait un autre appareil : avec la version serveur courante."""
    version = api(page, "GET", "/api/settings").json()["dashboard_layout_version"]
    return api(page, "PATCH", "/api/settings", data=json.dumps({"values": {"dashboard_layout": layout_json}, "dashboard_layout_base_version": version}))


class Restorer:
    """Remise en état de l'instance après un parcours : chaque modification mémorise d'abord la valeur
    d'origine, `restore` (à passer en `cleanup` de `main`) la rétablit.

    Un parcours ne doit pas dépendre des présentations de l'instance : en pleine largeur (« Grille »…),
    l'article s'ouvre dans une fenêtre qui recouvre la barre latérale et les en-têtes de vue."""

    def __init__(self) -> None:
        self.view_modes: dict[tuple[str, int | None], str] = {}
        self.layout: str | None = None

    def view_mode(self, page: Page, kind: str, view_mode: str, id_: int | None = None) -> None:
        """Force la présentation d'une vue (`feed`, `category`, `smart`) ou du défaut (`default`)."""
        if (kind, id_) not in self.view_modes:
            if kind == "default":
                current = api(page, "GET", "/api/settings").json()["values"].get("default_view_mode") or "compact"
            elif kind == "category":
                current = next(c["view_mode"] for c in api(page, "GET", "/api/categories").json() if c["id"] == id_)
            else:
                current = api(page, "GET", f"/api/{'feeds' if kind == 'feed' else 'smart-feeds'}/{id_}").json()["view_mode"]
            self.view_modes[(kind, id_)] = current or "inherit"
        set_view_mode(page, kind, view_mode, id_)

    def save_layout(self, page: Page) -> None:
        """Mémorise la disposition du tableau de bord avant de la modifier."""
        if self.layout is None:
            self.layout = api(page, "GET", "/api/settings").json()["values"].get("dashboard_layout") or ""

    def restore(self, page: Page) -> None:
        for (kind, id_), value in self.view_modes.items():
            set_view_mode(page, kind, value, id_)
        if self.layout is not None:
            set_dashboard_layout(page, self.layout)


def watch_errors(page: Page, errors: list[str] | None = None) -> list[str]:
    """Collecte les exceptions et les `console.error` de la page ; les échecs de chargement de ressources
    (favicons absents, mode hors-ligne volontaire) ne sont pas des erreurs applicatives."""
    out = [] if errors is None else errors
    page.on("pageerror", lambda e: out.append(str(e)))
    page.on("console", lambda m: out.append(f"console.error: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    return out


def new_context(browser: Browser, **overrides) -> BrowserContext:
    return browser.new_context(**{**DEFAULT_CONTEXT, **overrides})


def login(page: Page, settle_ms: int = 1500) -> None:
    """Connexion par mot de passe, puis attente du tableau de bord (page d'accueil)."""
    page.goto(BASE, wait_until="networkidle")
    page.fill("input[type=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("[data-widget]", timeout=60000)
    if settle_ms:
        page.wait_for_timeout(settle_ms)


def shot(page: Page, name: str) -> None:
    page.screenshot(path=f"{OUT}/{name}.png")


def main(
    run: Callable[[Page, BrowserContext, list[str]], None],
    results: dict,
    *,
    failure_shot: str,
    settle_ms: int = 1500,
    cleanup: Callable[[Page], None] | None = None,
    **context_overrides,
) -> None:
    """Navigateur, connexion, exécution de `run`, capture et code de sortie 1 en cas d'exception,
    impression des résultats en JSON. `cleanup` s'exécute toujours (remise en état de l'instance)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = new_context(browser, **context_overrides)
        page = ctx.new_page()
        errors = watch_errors(page)
        login(page, settle_ms)
        failed = False
        try:
            run(page, ctx, errors)
        except Exception as exc:  # noqa: BLE001
            failed = True
            results["exception"] = repr(exc)[:300]
            results["url_at_failure"] = page.url
            results["js_errors"] = errors[:5]
            page.screenshot(path=f"{OUT}/{failure_shot}.png")
        finally:
            if cleanup is not None:
                cleanup(page)
        browser.close()
    print(json.dumps(results, ensure_ascii=False, indent=1))
    if failed:
        raise SystemExit(1)
