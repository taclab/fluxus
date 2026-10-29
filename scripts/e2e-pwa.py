"""Parcours E2E de la PWA : manifeste, service worker, en-têtes de la coquille, partage vers Fluxus.

    docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out \\
      fluxus-browser python /scripts/e2e-pwa.py
"""

from __future__ import annotations

from urllib.parse import quote

from e2e_common import BASE, main, shot

results: dict = {}


def run(page, ctx, errors) -> None:
    manifest = page.request.get(BASE + "/manifest.webmanifest").json()
    results["shortcuts"] = [s["url"] for s in manifest["shortcuts"]]
    results["share_target"] = manifest["share_target"]["action"]
    results["maskable"] = [i["src"] for i in manifest["icons"] if i.get("purpose") == "maskable"]
    assert results["shortcuts"] == ["/unread", "/starred"]
    assert results["share_target"] == "/partage"
    assert page.request.get(BASE + "/apple-touch-icon.png").ok

    # Coquille revalidée à chaque fois ; un fichier d'assets garde le cache par défaut.
    for path in ("/", "/sw.js", "/manifest.webmanifest"):
        assert page.request.get(BASE + path).headers.get("cache-control") == "no-cache", path

    # Service worker enregistré depuis le code (UpdateBanner), pas par un script injecté.
    scope = page.evaluate("navigator.serviceWorker.ready.then((r) => r.scope)")
    results["sw_scope"] = scope
    assert scope == BASE + "/"
    assert page.locator("[data-testid=update-banner]").count() == 0

    # Partage Android typique : titre de la page puis adresse dans `text`.
    text = quote("Un article à lire https://exemple.org/blog/flux.xml.")
    page.goto(f"{BASE}/partage?title=Blog&text={text}", wait_until="networkidle")
    field = page.get_by_placeholder("https://exemple.fr")
    field.wait_for(timeout=10000)
    results["prefilled"] = field.input_value()
    assert results["prefilled"] == "https://exemple.org/blog/flux.xml"
    shot(page, "pwa-partage")
    page.keyboard.press("Escape")
    page.wait_for_url(BASE + "/", timeout=5000)
    page.get_by_placeholder("https://exemple.fr").wait_for(state="detached", timeout=5000)
    page.wait_for_selector("[data-widget]", timeout=10000)

    # Partage sans adresse : la fenêtre s'ouvre vide plutôt que d'échouer.
    page.goto(f"{BASE}/partage?text=rien", wait_until="networkidle")
    page.get_by_placeholder("https://exemple.fr").wait_for(timeout=10000)
    assert page.get_by_placeholder("https://exemple.fr").input_value() == ""

    # Raccourci de l'icône installée.
    page.goto(f"{BASE}/unread", wait_until="networkidle")
    page.wait_for_timeout(500)
    results["js_errors"] = errors[:5]
    assert not errors, errors


if __name__ == "__main__":
    main(run, results, failure_shot="pwa-echec")
