"""Prompts de résumé. `PROMPT_VERSION` est stocké avec chaque résumé pour pouvoir les régénérer."""

PROMPT_VERSION = "v1"

LANGUAGE_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand", "it": "italien"}


def system_prompt(language: str) -> str:
    lang = LANGUAGE_NAMES.get(language, language)
    return f"""Tu es l'assistant de lecture d'un lecteur de flux RSS personnel. Tu rédiges des résumés fidèles, denses et agréables à lire, en {lang}, quelle que soit la langue du document source.

Règles :
- Ne jamais inventer : si une information manque, ne la mentionne pas.
- Rester neutre et factuel ; signaler explicitement quand l'auteur donne une opinion.
- Ne pas paraphraser le titre ni répéter des évidences.
- Écrire en Markdown simple (titres de niveau 3 maximum, listes à puces, gras pour les termes clés). Pas de tableau.
- Ne pas commencer par « Cet article... » ou « Cette vidéo... »."""


def article_prompt(title: str, url: str | None, text: str) -> str:
    return f"""Résume l'article suivant.

Structure attendue :
### L'essentiel
Deux ou trois phrases qui donnent l'information principale et pourquoi elle compte.

### Points clés
Trois à sept puces, chacune une idée précise (chiffres, noms, dates quand ils existent).

### À retenir
Une phrase de conclusion : conséquence, prochaine étape ou ce que le lecteur peut en faire. Si l'article est une opinion, dis-le ici et résume la thèse en une ligne.

Titre : {title}
Source : {url or "inconnue"}

<article>
{text}
</article>"""


def video_prompt(title: str, url: str | None, transcript: str) -> str:
    return f"""Résume la vidéo suivante à partir de sa transcription horodatée. Les repères comme [12:34] indiquent le moment dans la vidéo.

Structure attendue :
### L'essentiel
Deux ou trois phrases sur le sujet et la conclusion de la vidéo.

### Chapitres
Une puce par grande partie, sous la forme `**[mm:ss]** Titre court — une phrase de contenu`. Reprends les horodatages de la transcription au début de chaque partie, entre 4 et 10 chapitres selon la durée.

### À retenir
Une ou deux phrases : ce qu'il faut garder, ou les recommandations données.

Titre : {title}
Source : {url or "inconnue"}

<transcription>
{transcript}
</transcription>"""


def chunk_prompt(title: str, index: int, total: int, text: str) -> str:
    return f"""Voici la partie {index}/{total} d'un long document intitulé « {title} ». Rédige un résumé dense de cette partie seule, en puces, sans introduction ni conclusion, en conservant chiffres, noms et repères temporels éventuels.

<partie>
{text}
</partie>"""


def merge_prompt(title: str, url: str | None, kind: str, partial_summaries: list[str]) -> str:
    joined = "\n\n".join(f"--- Partie {i + 1} ---\n{s}" for i, s in enumerate(partial_summaries))
    structure = (
        "### L'essentiel\n### Chapitres (avec horodatages **[mm:ss]**)\n### À retenir"
        if kind == "video"
        else "### L'essentiel\n### Points clés\n### À retenir"
    )
    return f"""Voici des résumés partiels des différentes parties d'un même document intitulé « {title} » (source : {url or "inconnue"}). Fusionne-les en un résumé unique et cohérent, sans redite, avec la structure :
{structure}

{joined}"""
