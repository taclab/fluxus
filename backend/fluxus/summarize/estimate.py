"""Découpage d'une source longue et estimation de ce qu'une génération consommera, avant l'appel.

Sert à la réservation du budget des résumés automatiques et à la ligne `LlmUsage` posée avant tout
appel : l'estimation doit couvrir tout ce qui est envoyé et produit hors sortie finale.
"""

from __future__ import annotations

# Au-delà de cette taille (en caractères), on découpe et on fusionne des résumés partiels.
CHUNK_THRESHOLD = 250_000
CHUNK_SIZE = 120_000
# Plafond de sortie d'un résumé partiel (mode découpé).
CHUNK_OUTPUT_TOKENS = 2000
# Consignes et enrobage d'un appel : règles, consigne du type de résumé, consignes personnelles et
# question du lecteur à leur longueur maximale (`prompts.MAX_INSTRUCTIONS_CHARS`, `MAX_QUESTION_CHARS`),
# dans l'écriture la plus coûteuse (idéogrammes : 1,5 token par caractère). Vérifié par les tests.
PROMPT_OVERHEAD_TOKENS = 3000


def chunks(text: str, size: int = CHUNK_SIZE) -> list[str]:
    parts: list[str] = []
    while text:
        if len(text) <= size:
            parts.append(text)
            break
        cut = text.rfind("\n", size - 5000, size)
        if cut == -1:
            cut = size
        parts.append(text[:cut])
        text = text[cut:]
    return parts


def text_tokens(text: str) -> int:
    """Estimation prudente du nombre de tokens d'un texte.

    Écritures latines : environ 3 caractères par token. Grec, cyrillique, hébreu, arabe, écritures
    indiennes : compté 1 token par caractère. Chinois, japonais, coréen : 1,5 token par caractère
    (un idéogramme coûte souvent plus d'un token ; `len // 3` les sous-estimait d'un facteur 3 à 5).
    """
    wide = sum(1 for c in text if c >= "⺀")
    mid = sum(1 for c in text if "Ͱ" <= c < "⺀")
    return (len(text) - wide - mid) // 3 + mid + wide * 3 // 2


def estimate_input_tokens(text: str) -> int:
    """Tout ce qu'une génération consomme hors sortie finale.

    Mode découpé : chaque partie ajoute ses consignes et un résumé partiel, produit (sortie) puis relu
    par la fusion (entrée) ; ces appels intermédiaires comptent ici pour que la réservation les couvre.
    """
    base = text_tokens(text) + PROMPT_OVERHEAD_TOKENS
    if len(text) <= CHUNK_THRESHOLD:
        return base
    parts = len(chunks(text))
    return base + parts * (PROMPT_OVERHEAD_TOKENS + 2 * CHUNK_OUTPUT_TOKENS)
