# Fluxus

Lecteur de flux RSS auto-hébergé, dans l'esprit d'Inoreader, pensé pour une seule personne et
ses appareils. Il apporte ce que les lecteurs open source classiques n'offrent pas ensemble :

- **Article complet** récupéré depuis le site (HTTP + repli navigateur Chromium pour les sites JavaScript).
- **Résumé LLM à la demande** des articles, en streaming, jamais automatique.
- **Résumé de vidéos YouTube** à partir des sous-titres, avec chapitres horodatés cliquables.
- **Flux intelligents** : des recherches enregistrées par sujet à travers tous vos flux, un dossier ou un flux, avec résumés automatiques plafonnés.
- **Tableau de bord** en page d'accueil : widgets configurables (nouveautés par catégorie ou flux, vidéos YouTube, favoris, résumés, statistiques), glisser-déposer, trois largeurs, disposition partagée entre appareils.
- **Interface web** trois volets, raccourcis clavier, thème clair / sombre, recherche plein texte.
- **Application Android** (Capacitor) avec lecture **hors-ligne** et synchronisation des états lu / favori.
- Gestion des flux et catégories, découverte de flux depuis l'URL d'un site, import / export OPML.

Quatre conteneurs Docker, PostgreSQL comme seule dépendance, aucune file externe.
Pour comprendre comment c'est construit : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Sommaire

1. [Installation](#installation)
2. [Premiers pas](#premiers-pas)
3. [Configuration](#configuration)
4. [Utilisation](#utilisation)
5. [Application Android](#application-android)
6. [Exploitation](#exploitation)
7. [Développement](#développement)
8. [Dépannage](#dépannage)

---

## Installation

Prérequis : Docker avec le plugin Compose, un reverse proxy HTTPS (Caddy, Traefik, Nginx…) déjà en place.

```bash
git clone <ce dépôt> fluxus && cd fluxus
cp .env.example .env
$EDITOR .env          # mot de passe Postgres, FLUXUS_SECRET_KEY, ports
docker compose up -d --build
```

Le premier build prend quelques minutes (front Node, dépendances Python, image Chromium).
Ensuite l'application répond sur `http://<hôte>:8000` (port `FLUXUS_HTTP_PORT`).

Raccordez ce port à votre reverse proxy en HTTPS. L'API ne gère pas TLS. Renseignez
`FORWARDED_ALLOW_IPS` avec l'adresse du proxy pour que la limitation des tentatives de connexion
voie la vraie adresse des clients (par défaut `*`, acceptable sur un réseau Compose privé ; un
plafond global de 20 échecs par minute s'applique dans tous les cas).

Le démarrage **refuse** un `FLUXUS_SECRET_KEY` d'exemple ou de moins de 32 caractères, et Compose
exige `POSTGRES_PASSWORD` et `DATABASE_URL` dans `.env`.

Exemple Caddy :

```
fluxus.mondomaine.fr {
    reverse_proxy 127.0.0.1:8000
}
```

## Premiers pas

1. Ouvrez l'interface : elle demande de **créer le mot de passe** (compte unique).
   L'accueil est le **tableau de bord** ; il se remplit tout seul d'un widget par catégorie.
2. Ajoutez des flux avec le bouton **+** en haut de la barre latérale. Vous pouvez coller l'URL
   d'un site (les flux sont découverts automatiquement), d'un flux RSS/Atom ou d'une chaîne YouTube.
   Ou importez un fichier OPML depuis *Paramètres → Abonnements*.
3. Dans *Paramètres → Résumés*, choisissez le fournisseur LLM et saisissez la clé API.
4. Ouvrez un article, cliquez sur ✨ ou tapez `r` : le résumé s'écrit en direct.

Les flux sont rafraîchis en arrière-plan par le worker (intervalle adaptatif, 10 min à 24 h selon
l'activité du flux). L'interface se synchronise au chargement, au retour au premier plan et toutes
les 5 minutes ; le bouton ↻ force une synchronisation.

## Configuration

### Fichier `.env`

| Variable | Rôle | Défaut |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compte PostgreSQL créé au premier démarrage | `fluxus` / `change-me` / `fluxus` |
| `DATABASE_URL` | Chaîne SQLAlchemy utilisée par l'API et le worker (hôte `postgres` = service Compose) | |
| `FLUXUS_SECRET_KEY` | Signe les JWT et chiffre les clés LLM stockées. **32 caractères aléatoires minimum, ne jamais changer ensuite** (les clés stockées deviendraient illisibles). | |
| `FLUXUS_HTTP_PORT` | Port publié sur l'hôte pour l'API et le front | `8000` |
| `FLUXUS_PG_PORT` | Port hôte de Postgres, utile en développement | `5433` |
| `FLUXUS_BROWSER_URL` | URL interne du service de rendu | `http://browser:3000` |
| `FORWARDED_ALLOW_IPS` | IP(s) du reverse proxy autorisées à fournir `X-Forwarded-For` | `*` |

Variables optionnelles, préfixe `FLUXUS_` (voir `backend/fluxus/config.py`) : `ACCESS_TOKEN_HOURS`
(durée des sessions web, 168 h), `DEFAULT_FETCH_INTERVAL_MIN` (30), `MIN_FETCH_INTERVAL_MIN` (10),
`MAX_FETCH_INTERVAL_MIN` (1440), `WORKER_CONCURRENCY` (6), `HTTP_TIMEOUT_SECONDS` (20), `LOG_LEVEL`.

### Réglages dans l'interface

Stockés en base (table `settings`), modifiables dans *Paramètres* :

| Section | Réglages |
|---|---|
| Résumés | fournisseur (`anthropic` ou `openai` et compatibles), clé API (chiffrée), modèle, effort de raisonnement, langue des résumés, URL de base OpenAI ; modèle dédié et budget quotidien des résumés automatiques |
| Lecture | combinaison de polices (Éditorial, Littéraire, Journal, Moderne, Lisible, Système), taille du texte, largeur de colonne ; propre à l'appareil, aussi via le bouton Aa du lecteur |
| Apparence | thème clair / sombre / automatique (aussi via le bouton en bas de la barre latérale), réinitialisation du tableau de bord |
| Abonnements | import / export OPML, rafraîchissement global |
| Hors-ligne | horizon de conservation en jours (première sync d'un appareil et ménage local), synchroniser, resynchroniser tout |
| Appareils | liste et révocation des tokens de l'application mobile |
| Sécurité | changement de mot de passe (invalide les autres sessions web ; option de déconnexion des appareils mobiles) |

Par flux (double-clic ou clic droit sur un flux) : titre, catégorie, URL, mode d'extraction de
l'article complet, pause. Modes : *À la demande* (extraction à l'ouverture, HTTP puis navigateur),
*Toujours en HTTP* et *Toujours via le navigateur* (chaque nouvel article est extrait en arrière-plan,
lisible hors-ligne), *Jamais*.

### Fournisseur LLM

- **Anthropic** (défaut) : modèle `claude-opus-5` recommandé, `claude-sonnet-5` moins cher,
  `claude-haiku-4-5` le plus rapide. L'effort `low` suffit pour un résumé.
- **OpenAI** : `gpt-4.1-mini` par défaut. Le champ *URL de base* permet d'utiliser toute API
  compatible (Mistral, Groq, un Ollama distant…).

Les résumés coûtent uniquement quand vous cliquez. Le compteur de tokens cumulés est affiché dans
*Paramètres → Résumés*.

## Utilisation

### Tableau de bord

Page d'accueil (`/`, touche `h`). Sans configuration, il affiche une vue d'ensemble (non lus, lus
aujourd'hui, favoris, non lus par catégorie, flux en erreur), un carrousel « Fil d'actualité »,
un widget « Nouveautés » en mode magazine par catégorie, une grille des dernières vidéos YouTube,
les derniers favoris et les derniers résumés. Les articles sans image reçoivent un visuel de
substitution coloré portant l'icône du flux.
Cette disposition automatique suit vos catégories jusqu'à votre première personnalisation.

**Personnaliser** (en haut à droite) active le mode édition :

- **glisser** un widget par sa poignée ⋮⋮ (souris, doigt avec appui long, ou clavier : Espace,
  flèches, Espace) ;
- menu ⋯ de chaque widget : **Monter / Descendre**, **largeur** ⅓ ½ 1, **Configurer**, **Retirer** ;
- **Ajouter un widget** : type, source (une catégorie, un flux ou tout), nombre d'éléments,
  affichage liste ou cartes, non lus seulement, titre personnalisé.

| Widget | Contenu |
|---|---|
| Nouveautés | derniers articles d'une catégorie, d'un flux ou de tout ; bouton « tout marquer lu » ; quatre affichages : **carrousel** (grandes cartes horizontales), **magazine** (vignette, titre, source, extrait), cartes, liste ; favori et lu/non lu directement sur chaque article |
| Vidéos YouTube | miniatures des dernières vidéos non vues, toutes chaînes ou une catégorie |
| Favoris | derniers articles mis en favori |
| Résumés récents | derniers articles résumés, avec les premières lignes du résumé |
| Statistiques | non lus, lus aujourd'hui, favoris, résumés et tokens, barres par catégorie, flux en erreur |

Cliquer un article ouvre le lecteur dans le contexte de sa catégorie (la liste correspondante à
gauche, `j`/`k` disponibles) ; le bouton retour ramène au tableau de bord. Sur mobile, chaque
widget occupe toute la largeur et se réordonne par le menu ⋯.

La disposition est enregistrée sur le serveur, donc identique sur le web et sur Android, et mise en
cache localement pour le hors-ligne. *Paramètres → Apparence → Réinitialiser le tableau de bord*
revient à la disposition automatique.

### Flux intelligents

Un flux intelligent est une **vue par sujet** : « tout ce qui parle de X dans mes flux ». Il apparaît
dans la barre latérale avec son compteur de non-lus, dans le tableau de bord comme source de widget,
et fonctionne hors-ligne.

Créez-en un avec « + nouveau » dans la barre latérale, ou depuis le lecteur avec la baguette (portée
préremplie sur le flux de l'article). L'éditeur propose :

- une **portée** : tous les flux, une catégorie ou un flux ;
- un filtre par **mots-clés** (au moins un de, tous ces mots, aucun de), insensible à la casse et aux
  accents, ou une **expression régulière** en mode expert (moteur PostgreSQL, texte désaccentué,
  pas de références arrière, quantificateurs ≤ 255) ;
- les **champs examinés** : titre, extrait, auteur, adresse ; le contenu complet en option, qui ne
  s'applique qu'aux articles déjà extraits ;
- un **aperçu en direct** du nombre d'articles correspondants et de huit exemples ;
- le **résumé automatique** des nouveaux articles, avec un quota par jour pour ce flux intelligent.

Rien n'est masqué ni supprimé : un flux intelligent ne fait que regrouper. Les correspondances sont
calculées à l'arrivée de chaque article, recalculées quand vous modifiez la définition, et
propagées à tous vos appareils par la synchronisation.

**Résumés automatiques.** Ils utilisent un modèle dédié, économique, réglé dans *Paramètres →
Résumés*, et s'arrêtent au **budget quotidien de tokens** (200 000 par défaut, 0 pour désactiver,
jour en UTC). La consommation du jour est affichée dans les réglages. Dans la vue d'un flux
intelligent, ✨ « Résumer les non résumés » lance en une fois les résumés manquants, dans la limite
du budget restant ; les jobs sont étalés pour ne pas gêner le rafraîchissement des flux.

### Interface

Trois volets : **abonnements** (catégories dépliables, compteurs de non-lus, point rouge sur un flux
en erreur), **liste d'articles** (vue compacte ou cartes, filtre « non lus », recherche, tout marquer
lu), **lecteur** (contenu du flux ou article complet, vidéo YouTube intégrée, panneau de résumé).

Sur tablette la barre latérale devient un tiroir ; sur téléphone un seul volet est affiché à la fois.
L'URL encode la vue courante (`/feed/12?entry=345&unread=1`) : le bouton retour et les favoris du
navigateur fonctionnent.

### Organiser la barre latérale

Un **clic long** (souris ou doigt, environ un tiers de seconde) saisit un dossier, un flux ou un flux
intelligent ; il suffit ensuite de le déposer :

- un dossier au-dessus ou au-dessous d'un autre élément de la racine (dossier ou flux sans dossier) ;
- un flux entre deux flux, dans son dossier ou dans un autre ;
- un flux sur l'en-tête d'un dossier pour l'y placer en tête (un dossier replié s'ouvre après un
  court survol) ;
- un flux sur le **bord supérieur** d'un en-tête de dossier pour le sortir de tout dossier et le
  poser juste avant, au même niveau que les dossiers ; les flux sans dossier se déplacent ensuite
  comme des dossiers, avant, entre ou après eux ;
- un flux intelligent au-dessus ou au-dessous d'un autre.

L'ordre est **manuel et prioritaire** : un flux ajouté, importé ou changé de dossier arrive en tête
de son dossier (ou de la liste, s'il n'a pas de dossier), les flux d'un dossier supprimé prennent sa
place, et l'ordre est le même sur tous vos appareils et dans l'export OPML. Un clic court
reste un clic, un défilement au doigt reste un défilement ; la réorganisation exige d'être en ligne.

### Raccourcis clavier

| Touche | Action | Touche | Action |
|---|---|---|---|
| `h` | tableau de bord | | |
| `j` / `k` ou ↓ / ↑ | article suivant / précédent | `f` | article complet : récupérer, ou basculer flux ↔ complet |
| `m` | lu / non lu | `r` | résumer |
| `s` | favori | `u` | filtre « non lus » |
| `v` | ouvrir l'original | `/` | rechercher |
| `Échap` | retour à la liste | `?` | rappel des raccourcis |

### Typographie de lecture

Le bouton **Aa** du lecteur, ou *Paramètres → Lecture*, propose six combinaisons de polices
embarquées avec l'application (donc identiques hors-ligne et sur Android) : Éditorial (Source
Serif 4 + Inter, par défaut), Littéraire (Literata), Journal (Lora + Montserrat), Moderne (Inter),
Lisible (Atkinson Hyperlegible) et Système. S'y ajoutent quatre tailles de texte et trois largeurs
de colonne. Le choix est mémorisé par appareil et appliqué avant le premier rendu.

### Résumés

Le bouton ✨ résume l'article complet si disponible, sinon récupère l'article puis le résume, sinon
se rabat sur le contenu du flux. Pour une vidéo YouTube, il récupère les sous-titres (français puis
anglais puis premier disponible) et produit des chapitres ; cliquer sur un horodatage déplace la vidéo.
Un résumé est stocké : le rouvrir ne coûte rien, ↻ le régénère. Sans sous-titres, l'application le dit
et n'essaie pas de transcrire l'audio.

### Recherche

En ligne, la recherche est faite par PostgreSQL (plein texte sur titre, auteur et contenu, syntaxe
`websearch` : `"expression exacte" -mot`). Hors-ligne, elle se limite aux titres et extraits présents
sur l'appareil.

### Hors-ligne

Toutes les lectures de l'interface se font dans une base locale (IndexedDB). Les N derniers jours
d'articles (30 par défaut, réglable dans *Paramètres → Hors-ligne*) et tous les favoris, avec leur
contenu complet, sont conservés sur chaque appareil. Marquer lu ou favori
hors-ligne est mis en file et rejoué au retour du réseau ; en cas de conflit la modification la plus
récente gagne. Ajouter un flux, récupérer un article complet ou résumer nécessite le réseau.

## Application Android

Le front est empaqueté avec Capacitor. Aucun SDK Android n'est nécessaire sur la machine, le build
s'exécute dans un conteneur :

```bash
scripts/build-apk.sh            # debug  -> dist/app-debug.apk (installable tel quel)
scripts/build-apk.sh release    # release non signé -> dist/app-release-unsigned.apk
```

Le premier appel construit l'image de build (JDK + SDK Android, quelques minutes) ; les suivants
prennent une à deux minutes grâce au cache Gradle.

Sur le téléphone : installez l'APK, saisissez l'adresse HTTPS du serveur et le mot de passe.
L'application obtient un **token d'appareil** longue durée, révocable dans *Paramètres → Appareils*.
Le bouton retour matériel navigue dans l'application. La synchronisation a lieu à l'ouverture et au
retour au premier plan ; il n'y a pas encore de synchronisation planifiée quand l'appli est fermée.

Signature d'une release : `apksigner sign --ks ma-cle.jks dist/app-release-unsigned.apk`.

## Exploitation

```bash
docker compose ps                        # état des quatre services
docker compose logs -f worker            # rafraîchissements, erreurs de flux
docker compose logs -f api               # requêtes HTTP
docker compose pull && docker compose up -d --build   # mise à jour
```

Les migrations de schéma s'appliquent automatiquement au démarrage du conteneur `api`
(`alembic upgrade head`).

Sauvegarde : la base contient tout (flux, articles, résumés, réglages, mot de passe).

```bash
docker compose exec -T postgres pg_dump -U fluxus fluxus | gzip > fluxus-$(date +%F).sql.gz
gunzip -c fluxus-2026-09-14.sql.gz | docker compose exec -T postgres psql -U fluxus fluxus   # restauration
```

Remise à zéro complète : `docker compose down -v` (supprime le volume Postgres).

Ressources : environ 450 Mo de RAM au repos pour l'ensemble (api et worker plafonnés à 768 Mo,
`browser` à 1 Go). Les journaux tournent sur 3 fichiers de 10 Mo par service. Le navigateur est
sur un réseau Compose séparé, sans accès à Postgres, et refuse de rendre toute adresse privée.

## Développement

Voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) pour la carte du code et les flux de données.

```bash
docker compose up -d postgres                       # la base seule, publiée sur :5433

# Backend — Python 3.12 géré par uv
cd backend && uv sync --all-extras
export DATABASE_URL=postgresql+asyncpg://fluxus:<mdp>@localhost:5433/fluxus
export FLUXUS_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')   # 32 caractères minimum, non placeholder
uv run alembic upgrade head
uv run uvicorn fluxus.main:app --reload --port 8000
uv run python -m fluxus.worker                      # second terminal

# Front — Node 22 (web/.nvmrc)
cd web && npm install
npm run dev                                          # http://localhost:5173, /api proxifié vers :8000
npm run gen:api                                      # régénère src/api/schema.d.ts depuis l'OpenAPI
```

La documentation interactive de l'API est servie sur `/api/docs`.

Qualité :

```bash
cd backend
uv run ruff check fluxus tests
uv run pytest tests/ --ignore=tests/test_api.py                    # unitaires, sans base
DATABASE_URL=postgresql+asyncpg://fluxus:<mdp>@localhost:5433/fluxus_test uv run pytest tests/test_api.py   # intégration
cd ../web && npm run typecheck && npm run lint
```

Tests de bout en bout de l'interface (Playwright dans l'image `fluxus-browser`, contre une pile qui
tourne sur :8000) :

```bash
# -u 0 : l'image tourne en utilisateur non privilégié, les captures doivent pouvoir s'écrire dans /out
docker run --rm --network host -u 0 -v $PWD/scripts/e2e-ui.py:/out/e2e-ui.py:ro -v /tmp/shots:/out fluxus-browser python /out/e2e-ui.py
docker run --rm --network host -u 0 -v $PWD/scripts/e2e-dashboard.py:/out/e2e-dashboard.py:ro -v /tmp/shots:/out fluxus-browser python /out/e2e-dashboard.py
docker run --rm --network host -u 0 -v $PWD/scripts/e2e-smartfeeds.py:/out/e2e-smartfeeds.py:ro -v /tmp/shots:/out fluxus-browser python /out/e2e-smartfeeds.py
```

Nouvelle migration après modification de `backend/fluxus/models.py` :
`uv run alembic revision --autogenerate -m "description"`, puis relire le fichier généré.

## Dépannage

| Symptôme | Piste |
|---|---|
| Un flux a un point rouge | Survolez-le : HTTP 403/404, XML invalide, domaine disparu. Le worker réessaie avec un recul exponentiel (jusqu'à 24 h). |
| « Aucune clé API configurée » | *Paramètres → Résumés*, saisir la clé puis Enregistrer. |
| « Modèle inconnu » | Le modèle ne correspond pas au fournisseur choisi (ex. `claude-…` avec OpenAI). |
| « Cette vidéo n'a pas de sous-titres » | Comportement attendu : pas de transcription audio. |
| Un flux intelligent ne matche pas un mot accentué | Les mots-clés sont comparés sans accents ni casse ; en mode regex, écrivez le motif sans accents (`eleve` pour « élève »). |
| « Expression régulière refusée : … » | Message du moteur PostgreSQL : pas de groupes nommés Python `(?P<…>)`, quantificateurs ≤ 255, pas de références arrière. |
| Les résumés automatiques ne se déclenchent plus | Quota du flux intelligent ou budget quotidien atteint (visible dans *Paramètres → Résumés*), ou budget à 0. |
| Article complet vide ou tronqué | Le site est payant ou anti-robot. Essayer « Réessayer via le navigateur » ; sinon passer le flux en `none`. |
| L'interface ne montre pas les nouveaux articles | Bouton ↻ ; si le problème persiste, *Paramètres → Hors-ligne → Resynchroniser tout*. |
| « Cette disposition a été enregistrée par une version plus récente » | Un autre appareil a une version de Fluxus plus récente : mettez celui-ci à jour, le tableau de bord reste consultable en attendant. |
| L'API refuse de démarrer : « FLUXUS_SECRET_KEY doit être… » | Le secret est un exemple ou trop court. Générer une valeur aléatoire de 32 caractères ou plus, ne plus la changer ensuite. |
| Session fermée sur tous les navigateurs après un changement de mot de passe | Comportement voulu : les anciennes sessions sont invalidées, celle qui a fait le changement reste ouverte. |
| Conteneur `api` `unhealthy` alors qu'il répond | Un proxy HTTP injecté par Docker intercepte le test local ; le healthcheck utilise `--noproxy '*'` pour cela. Vérifier `~/.docker/config.json`. |
| Gradle échoue derrière un proxy | `scripts/build-apk.sh` traduit `HTTPS_PROXY` en propriétés Java ; vérifier que la variable est visible dans le conteneur. |

## Licence

À définir.
