"""Parcours E2E de la langue de l'interface (Playwright, image fluxus-browser).

docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-i18n.py

La langue est une préférence de l'appareil (localStorage) : rien n'est modifié sur l'instance.
- Paramètres → Apparence : passage à l'anglais, `<html lang>` suit, plus aucun libellé français
  d'interface sur les écrans principaux (le contenu des articles et les noms des flux, eux, restent
  tels quels : on ne cherche que des libellés de l'interface).
- Raccourci « / » (recherche) en anglais.
- Écran de connexion d'un navigateur anglais : anglais d'office, bascule en français possible.
"""

from e2e_common import BASE, PASSWORD, main, new_context, shot, wait_synced, wait_until

results: dict = {}

# Libellés d'interface français qui ne doivent plus apparaître une fois l'anglais choisi.
FRENCH_UI = [
    "Tableau de bord",
    "Tous les articles",
    "Non lus",
    "Paramètres",
    "Abonnements",
    "Ajouter un widget",
    "Flux intelligents",
    "Derniers favoris",
    "Résumés récents",
    "Vue d’ensemble",
    "Fil d’actualité",
    "Apparence",
    "Thème",
    "Hors-ligne",
    "Sauvegarde",
    "Tout marquer comme lu",
    "à l'instant",
    "il y a ",
]


# Contenu tiers (articles de la liste et du lecteur, widgets d'articles) : il reste dans sa langue.
CONTENT_JS = """() => {
  const body = document.body.cloneNode(true)
  body.querySelectorAll('[data-entry-id], li article, article .prose-reader').forEach((n) => n.remove())
  return body.textContent
}"""


def french_left(page) -> list[str]:
    text = page.evaluate(CONTENT_JS)
    return [w for w in FRENCH_UI if w in text]


def run(page, ctx, errors):
    wait_synced(page)

    # --- Choix de l'anglais dans les paramètres
    page.goto(BASE + "/settings", wait_until="load")
    page.wait_for_selector("[data-testid=interface-language]")
    page.select_option("[data-testid=interface-language]", "en")
    page.wait_for_function("document.documentElement.lang === 'en'", timeout=10000)
    page.get_by_text("Appearance", exact=True).wait_for(timeout=10000)
    assert page.evaluate("localStorage.getItem('fluxus.locale')") == "en"
    results["settings_left"] = french_left(page)
    shot(page, "90-i18n-settings")

    # --- Tableau de bord
    page.goto(BASE, wait_until="load")
    page.wait_for_selector("[data-widget]", timeout=30000)
    page.get_by_text("Dashboard", exact=True).first.wait_for(timeout=10000)
    results["dashboard_left"] = french_left(page)
    shot(page, "91-i18n-dashboard")

    # --- Liste, lecteur et raccourci « / »
    page.goto(BASE + "/all", wait_until="load")
    page.wait_for_selector("[data-entry-id]", timeout=30000)
    page.locator("[data-entry-id]").first.click()
    wait_until(lambda: "entry=" in page.url, "ouverture d'un article")
    page.wait_for_selector("article", timeout=15000)
    results["reader_left"] = french_left(page)
    shot(page, "92-i18n-reader")
    page.keyboard.press("Escape")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("/")
    page.wait_for_selector("[data-search-input]", timeout=5000)
    results["slash_opens_search"] = True

    # --- Abonnements
    page.goto(BASE + "/abonnements", wait_until="load")
    page.wait_for_selector("[data-testid=subs-search]", timeout=30000)
    results["subscriptions_left"] = french_left(page)
    shot(page, "93-i18n-subscriptions")

    for key in ("settings_left", "dashboard_left", "reader_left", "subscriptions_left"):
        assert not results[key], (key, results[key])

    # --- Connexion depuis un navigateur anglais, puis bascule en français
    other = new_context(ctx.browser, locale="en-US")
    try:
        login_page = other.new_page()
        login_page.goto(BASE, wait_until="load")
        login_page.get_by_role("button", name="Sign in").wait_for(timeout=15000)
        assert login_page.evaluate("document.documentElement.lang") == "en"
        shot(login_page, "94-i18n-login-en")
        login_page.get_by_role("button", name="Français").click()
        login_page.get_by_role("button", name="Se connecter").wait_for(timeout=10000)
        login_page.fill("input[type=password]", PASSWORD)
        login_page.get_by_role("button", name="Se connecter").click()
        login_page.wait_for_selector("[data-widget]", timeout=60000)
        assert login_page.evaluate("document.documentElement.lang") == "fr"
        results["login_switch"] = True
    finally:
        other.close()

    assert not errors, errors[:5]


main(run, results, failure_shot="99-i18n-failure")
