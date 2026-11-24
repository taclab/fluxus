"""Prompts de résumé. `PROMPT_VERSION` est stocké avec chaque résumé pour pouvoir les régénérer.

Un résumé a un type (`STYLES`) : la consigne de forme change, le reste (règles, source, découpage
des longs documents) est commun. Le type « question » répond à une question libre sur le document.
"""

from dataclasses import dataclass

PROMPT_VERSION = "v2"

# Longueurs maximales des textes de l'utilisateur ajoutés aux consignes : l'estimation de
# consommation (`estimate.PROMPT_OVERHEAD_TOKENS`) les couvre à ce plafond.
MAX_INSTRUCTIONS_CHARS = 1000
MAX_QUESTION_CHARS = 500


@dataclass(frozen=True)
class Style:
    # Consigne de forme pour un article, puis pour une vidéo (transcription horodatée).
    article: str
    video: str
    # Plafond de sortie (tokens, réflexion comprise chez Anthropic) : la longueur se règle par la
    # consigne, le plafond n'évite que l'emballement.
    max_output_tokens: int = 4000


_TIMESTAMPS = "Quand un passage précis compte, cite son horodatage sous la forme **[mm:ss]**."

STYLES: dict[str, Style] = {
    "standard": Style(
        article="""Structure attendue :
### L'essentiel
Deux ou trois phrases qui donnent l'information principale et pourquoi elle compte.

### Points clés
Trois à sept puces, chacune une idée précise (chiffres, noms, dates quand ils existent).

### À retenir
Une phrase de conclusion : conséquence, prochaine étape ou ce que le lecteur peut en faire. Si l'article est une opinion, dis-le ici et résume la thèse en une ligne.""",
        video="""Structure attendue :
### L'essentiel
Deux ou trois phrases sur le sujet et la conclusion de la vidéo.

### Chapitres
Une puce par grande partie, sous la forme `**[mm:ss]** Titre court — une phrase de contenu`. Reprends les horodatages de la transcription au début de chaque partie, entre 4 et 10 chapitres selon la durée.

### À retenir
Une ou deux phrases : ce qu'il faut garder, ou les recommandations données.""",
    ),
    "oneline": Style(
        article="Réponds par une seule phrase (40 mots au plus) qui dit l'essentiel : le fait principal et pourquoi il compte. Pas de titre, pas de liste, pas de gras.",
        video="Réponds par une seule phrase (40 mots au plus) qui dit l'essentiel de la vidéo et sa conclusion. Pas de titre, pas de liste, pas de gras.",
    ),
    "bullets": Style(
        article="Réponds uniquement par une liste de cinq à dix puces, de la plus importante à la moins importante. Chaque puce tient en une phrase et porte une idée précise (chiffres, noms, dates quand ils existent). Pas de titre, pas d'introduction ni de conclusion.",
        video=f"Réponds uniquement par une liste de cinq à dix puces, dans l'ordre de la vidéo. Chaque puce tient en une phrase et porte une idée précise. {_TIMESTAMPS} Pas de titre, pas d'introduction ni de conclusion.",
    ),
    "detailed": Style(
        article="""Rédige un résumé approfondi, qui suit le fil du document : un lecteur doit pouvoir s'en contenter sans lire l'original.

Structure attendue :
### En bref
Trois ou quatre phrases : le sujet, la thèse ou l'information principale, la conclusion.

Puis une section `###` par grande partie du document, dans son ordre, avec un titre explicite. Dans chacune : les arguments, les faits, les chiffres, les exemples et les nuances, en paragraphes courts ou en puces. N'omets aucune partie importante, même tardive.

### À retenir
Trois à cinq puces : les conclusions, les limites ou points discutables, et ce que le lecteur peut en faire.""",
        video=f"""Rédige un résumé approfondi, qui suit le déroulé de la vidéo : un spectateur doit pouvoir s'en contenter sans la regarder.

Structure attendue :
### En bref
Trois ou quatre phrases : le sujet, la thèse, la conclusion.

Puis une section `###` par grande partie de la vidéo, titrée `**[mm:ss]** Titre`, avec les arguments, faits, chiffres, exemples et démonstrations. {_TIMESTAMPS}

### À retenir
Trois à cinq puces : conclusions, recommandations, limites.""",
        max_output_tokens=8000,
    ),
    "opinion": Style(
        article="""Sépare ce qui est établi de ce qui relève de l'opinion.

Structure attendue :
### Faits
Puces : les faits vérifiables rapportés (chiffres, événements, citations, sources citées).

### Opinions et interprétations
Puces : les jugements, prédictions et interprétations, en précisant qui les porte (l'auteur, une personne citée).

### Lecture critique
Deux ou trois phrases : l'orientation générale du texte, ce qui manque ou est affirmé sans preuve, le degré de confiance qu'on peut lui accorder.""",
        video=f"""Sépare ce qui est établi de ce qui relève de l'opinion dans la vidéo.

Structure attendue :
### Faits
Puces : les faits vérifiables présentés. {_TIMESTAMPS}

### Opinions et interprétations
Puces : les jugements, prédictions et interprétations, en précisant qui les porte.

### Lecture critique
Deux ou trois phrases : l'orientation générale, ce qui est affirmé sans preuve, le degré de confiance qu'on peut lui accorder.""",
    ),
    "simple": Style(
        article="Explique le document à quelqu'un d'intelligent qui ne connaît pas le domaine : phrases courtes, vocabulaire courant, chaque terme technique expliqué en quelques mots la première fois, une analogie si elle aide vraiment. Trois paragraphes courts au plus, puis une puce « Pourquoi c'est important » d'une phrase. Pas de titre.",
        video="Explique la vidéo à quelqu'un d'intelligent qui ne connaît pas le domaine : phrases courtes, vocabulaire courant, chaque terme technique expliqué en quelques mots la première fois. Trois paragraphes courts au plus, puis une puce « Pourquoi c'est important » d'une phrase. Pas de titre.",
    ),
    "question": Style(
        article="Réponds à la question du lecteur en t'appuyant uniquement sur le document. Va droit à la réponse, puis donne les éléments du document qui la justifient. Si le document ne permet pas de répondre, dis-le en une phrase et indique ce qu'il dit de plus proche.",
        video=f"Réponds à la question du lecteur en t'appuyant uniquement sur la transcription. Va droit à la réponse, puis donne les éléments qui la justifient. {_TIMESTAMPS} Si la vidéo ne permet pas de répondre, dis-le en une phrase.",
        max_output_tokens=3000,
    ),
}
DEFAULT_STYLE = "standard"


def style_or_default(style: str | None) -> str:
    return style if style in STYLES else DEFAULT_STYLE

LANGUAGE_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand", "it": "italien"}


def system_prompt(language: str, instructions: str = "") -> str:
    lang = LANGUAGE_NAMES.get(language, language)
    reader = instructions.strip()[:MAX_INSTRUCTIONS_CHARS]
    persona = f"\n\nConsignes personnelles du lecteur (à suivre tant qu'elles ne contredisent pas les règles) :\n{reader}" if reader else ""
    return f"""Tu es l'assistant de lecture d'un lecteur de flux RSS personnel. Tu rédiges des résumés fidèles, denses et agréables à lire, en {lang}, quelle que soit la langue du document source.

Règles :
- Ne jamais inventer : si une information manque, ne la mentionne pas.
- Rester neutre et factuel ; signaler explicitement quand l'auteur donne une opinion.
- Ne pas paraphraser le titre ni répéter des évidences.
- Écrire en Markdown simple (titres de niveau 3 maximum, listes à puces, gras pour les termes clés). Pas de tableau.
- Ne pas commencer par « Cet article... » ou « Cette vidéo... ».{persona}"""


def _question_block(question: str | None) -> str:
    return f"\n\nQuestion du lecteur : {question.strip()[:MAX_QUESTION_CHARS]}" if question else ""


def article_prompt(title: str, url: str | None, text: str, style: str = DEFAULT_STYLE, question: str | None = None) -> str:
    st = STYLES[style_or_default(style)]
    return f"""Résume l'article suivant.

{st.article}{_question_block(question)}

Titre : {title}
Source : {url or "inconnue"}

<article>
{text}
</article>"""


def video_prompt(title: str, url: str | None, transcript: str, style: str = DEFAULT_STYLE, question: str | None = None) -> str:
    st = STYLES[style_or_default(style)]
    return f"""Résume la vidéo suivante à partir de sa transcription horodatée. Les repères comme [12:34] indiquent le moment dans la vidéo.

{st.video}{_question_block(question)}

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


def merge_prompt(title: str, url: str | None, kind: str, partial_summaries: list[str], style: str = DEFAULT_STYLE, question: str | None = None) -> str:
    joined = "\n\n".join(f"--- Partie {i + 1} ---\n{s}" for i, s in enumerate(partial_summaries))
    st = STYLES[style_or_default(style)]
    form = st.video if kind == "video" else st.article
    return f"""Voici des résumés partiels des différentes parties d'un même document intitulé « {title} » (source : {url or "inconnue"}). Fusionne-les en un résultat unique et cohérent, sans redite, en suivant cette consigne :

{form}{_question_block(question)}

{joined}"""
