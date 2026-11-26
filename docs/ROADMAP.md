# Feuille de route

Chantiers demandés, pas encore commencés. Chaque entrée garde l'intention et les contraintes déjà
repérées ; la conception détaillée se fait au démarrage du chantier. Un chantier livré sort d'ici et
passe dans `CHANGELOG.md`.

## 1. Lettre récapitulative par e-mail (quotidienne ou hebdomadaire)

**Intention** : recevoir par e-mail, chaque jour ou chaque semaine, l'essentiel de ce qui compte,
construit à partir des résumés.

**Pistes et contraintes**
- Envoi SMTP configurable (hôte, port, identifiant, mot de passe dans `SECRET_KEYS`, expéditeur,
  destinataire) dans les réglages ; bouton « envoyer un test ».
- Contenu : sélection (flux intelligents choisis, favoris, articles résumés de la période), un
  résumé court par article et le lien. Une synthèse globale par le LLM est possible mais doit passer
  par `summarize_entry` ou un équivalent qui tient le journal `LlmUsage` et le budget : c'est une
  génération automatique, donc opt-in explicite et plafonnée comme les résumés automatiques.
- Planification : job récurrent (`@job_handler`, idempotent par période : une lettre par jour ou
  semaine, même après redémarrage) ; fuseau horaire de l'utilisateur à régler.
- HTML d'e-mail sobre (styles en ligne), version texte, lien vers l'article dans Fluxus.

## 2. Newsletters par e-mail : suites

La réception est livrée en 1.0.7 (`CHANGELOG.md`, `docs/NEWSLETTERS.md`). Restent :
- désinscription « en un clic » (POST RFC 8058 quand `List-Unsubscribe-Post` le permet) ;
- images servies par le serveur, pour ne pas signaler l'ouverture d'un numéro à l'expéditeur ;
- suggestion du flux RSS quand la newsletter en a un (Substack, Ghost, Beehiiv…) ;
- relève IMAP pour les instances sans Cloudflare (`ingest_message` est déjà indépendant du transport).

## 3. Intégration YouTube plus forte : « À regarder plus tard » comme flux

**Intention** : retrouver sa liste « À regarder plus tard » dans Fluxus, comme un flux.

**Pistes et contraintes**
- L'API YouTube Data ne donne plus accès à la playlist « À regarder plus tard » (`WL`) depuis 2016,
  même avec OAuth, et il n'existe pas de flux RSS pour elle.
- Alternatives à évaluer :
  - une playlist ordinaire (privée ou non listée) utilisée comme « à regarder » : flux RSS
    `feeds/videos.xml?playlist_id=…` si elle est publique ou non listée, sinon API avec OAuth ;
  - lecture de `WL` avec les cookies du compte (yt-dlp) : fragile et contraire aux conditions de
    YouTube, à écarter sauf demande explicite ;
  - dans l'autre sens, « regarder plus tard » côté Fluxus : marquer une vidéo depuis Fluxus (favori
    dédié ou étiquette), sans dépendre de YouTube.
- Autres pistes du même chantier : import des abonnements YouTube (OPML depuis Google Takeout),
  chapitres et durée des vidéos dans la liste.

## 4. Repérage des articles sponsorisés

**Intention** : une icône distingue les contenus sponsorisés ou en partenariat.

**Pistes et contraintes**
- Heuristiques sans LLM d'abord, au moment de la récupération : mentions explicites (« article
  sponsorisé », « en partenariat avec », « contenu partenaire », « sponsored », « paid partnership »,
  « #ad »), liens `rel="sponsored"`, catégories RSS dédiées, préfixes de titre (« [Sponsorisé] »).
  Résultat stocké sur l'article (champ + raison), synchronisé comme le reste (`updated_at`).
- Vidéos : mention de promotion payante dans la page ou la description ; segments sponsorisés via
  l'API publique SponsorBlock (par identifiant de vidéo, appel par `safe_get`).
- Classification par le LLM seulement en option, jamais automatique sans opt-in (budget).
- Option d'affichage : icône seule, ou masquer les sponsorisés d'un flux.

## 5. Popularité des articles

**Intention** : un indicateur de popularité par article, éventuellement via un service dédié.

**Pistes et contraintes**
- Sources gratuites interrogeables par URL : Hacker News (API Algolia : points, commentaires),
  Reddit (recherche par URL, limites de débit), Lobsters ; Bluesky et Mastodon possibles mais plus
  coûteux à agréger. Services commerciaux (SharedCount…) payants.
- Calcul en tâche de fond, limité aux articles récents et aux flux choisis, avec cache et
  rafraîchissement espacé ; tout appel par `fetcher/http.safe_get`.
- Affichage : badge discret (score, lien vers la discussion), tri « populaires » d'une vue,
  éventuellement condition de flux intelligent (`smartfeeds/rules.build_condition`).
