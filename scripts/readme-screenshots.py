"""Captures du README (`docs/screenshots/`), en anglais et en thème sombre (Playwright, image fluxus-browser).

À lancer sur une instance de démonstration jetable, jamais sur des données personnelles : base vide,
dossiers Tech, Science, Design, Videos et flux anglophones (voir docs/DEVELOPMENT.md).

docker run --rm --network host -u 0 -e E2E_BASE=http://localhost:8001 -v $PWD/scripts:/scripts:ro \\
  -v $PWD/docs/screenshots:/out fluxus-browser python /scripts/readme-screenshots.py

2880×1800 (1440×900 en densité 2), JPEG.
"""

from e2e_common import BASE, OUT, api, main, wait_synced, wait_until

results: dict = {}

# Images visibles seulement : celles d'un carrousel hors cadre restent en chargement différé.
IMAGES_LOADED = """() => [...document.images].filter((i) => {
  const r = i.getBoundingClientRect()
  return r.width > 0 && r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0
}).every((i) => i.complete)"""


def capture(page, name: str) -> None:
    page.wait_for_function(IMAGES_LOADED, timeout=30000)
    page.mouse.move(0, 899)  # pas de survol parasite
    page.screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=85)
    results[name] = True


def run(page, ctx, errors):
    wait_synced(page)
    page.evaluate("localStorage.setItem('fluxus.theme', 'dark')")
    page.reload(wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    assert page.evaluate("document.documentElement.lang") == "en"
    assert page.evaluate("document.documentElement.classList.contains('dark')")
    capture(page, "dashboard")

    science = next(c for c in api(page, "GET", "/api/categories").json() if c["title"] == "Science")
    page.goto(f"{BASE}/category/{science['id']}", wait_until="load")
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    # Premier article (hors tête de liste, pour montrer la sélection) dont l'image s'affiche en haut du
    # lecteur : un article qui commence par un menu ou un long texte se prête mal à la vitrine.
    for i in range(1, 12):
        page.locator("[data-entry-id]").nth(i).click()
        wait_until(lambda: "entry=" in page.url, "ouverture d'un article")
        page.wait_for_selector("article .prose-reader", timeout=30000)
        page.wait_for_timeout(300)
        top = page.evaluate("() => { const img = document.querySelector('article .prose-reader img'); return img ? img.getBoundingClientRect().top : 9999 }")
        if top < 450:
            results["reader_entry"] = i
            break
    else:
        raise AssertionError("aucun article illustré en tête dans ce dossier")
    capture(page, "reader")
    assert not errors, errors[:5]


main(
    run,
    results,
    failure_shot="readme-failure",
    locale="en-US",
    color_scheme="dark",
    viewport={"width": 1440, "height": 900},
    device_scale_factor=2,
)
