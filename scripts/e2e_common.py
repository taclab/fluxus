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
from collections.abc import Callable

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE = os.environ.get("E2E_BASE", "http://localhost:8000")
PASSWORD = os.environ.get("E2E_PASSWORD", "motdepasse-test")
OUT = os.environ.get("E2E_OUT", "/out")

DEFAULT_CONTEXT = {"viewport": {"width": 1400, "height": 900}, "locale": "fr-FR", "bypass_csp": True}


def api(page: Page, method: str, path: str, **kw):
    """Appel API authentifié avec le token de la session du navigateur."""
    token = page.evaluate("localStorage.getItem('fluxus.token')")
    return page.request.fetch(BASE + path, method=method, headers={"Authorization": f"Bearer {token}", "content-type": "application/json"}, **kw)


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
