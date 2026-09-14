# Architecture de Fluxus

Ce document explique comment le projet est construit : les composants, le modèle de données, les
principaux flux de traitement, la carte du code et les conventions. Il complète le
[README](../README.md), qui couvre l'installation et l'usage.

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Choix structurants](#2-choix-structurants)
3. [Composants](#3-composants)
4. [Modèle de données](#4-modèle-de-données)
5. [Flux de traitement](#5-flux-de-traitement)
6. [API HTTP](#6-api-http)
7. [Front : offline-first](#7-front--offline-first)
8. [Sécurité](#8-sécurité)
9. [Carte du code](#9-carte-du-code)
10. [Conventions et recettes](#10-conventions-et-recettes)
11. [Limites connues et pistes](#11-limites-connues-et-pistes)

---

## 1. Vue d'ensemble

```
                         ┌────────────────────────────────────────────┐
   navigateur / APK ───► │  api  (FastAPI, uvicorn)            :8000  │
   (reverse proxy HTTPS) │  · routes /api/*                           │
                         │  · sert le build du front (SPA)            │
                         └──────┬───────────────────────────┬─────────┘
                                │ SQLAlchemy async          │ HTTP interne (repli extraction)
                                ▼                           ▼
                    ┌───────────────────┐        ┌────────────────────────┐
                    │ postgres (16)     │        │ browser (Playwright)   │
                    │ · données         │        │ · POST /render → HTML  │
                    │ · table jobs      │        │   après exécution JS   │
                    └─────────▲─────────┘        └────────────▲───────────┘
                              │ SELECT … FOR UPDATE SKIP LOCKED│
                    ┌─────────┴──────────────────────────────┴───────────┐
                    │ worker (même image que api)                        │
                    │ · planifie et exécute les jobs fetch_feed,         │
                    │   extract_entry                                    │
                    └────────────────────────────────────────────────────┘
```

Deux processus Python partagent le même code et la même image : **api** répond aux requêtes,
**worker** exécute le travail de fond. Ils ne communiquent que par la base. **browser** est un
micro-service isolé qui rend une page dans Chromium. Il n'y a ni Redis, ni file externe, ni cron.

Le front est une application React compilée en fichiers statiques, servie par l'API. La même
application, empaquetée par Capacitor, constitue l'APK Android.

## 2. Choix structurants

| Choix | Pourquoi |
|---|---|
| From scratch plutôt que sur Miniflux / FreshRSS | Une seule interface, un seul modèle de données et une seule authentification pour le web, le mobile et les résumés. Le fetcher est la partie la mieux couverte par les bibliothèques (`feedparser`, `trafilatura`). |
| File de jobs dans PostgreSQL | Un seul service à exploiter et sauvegarder. `FOR UPDATE SKIP LOCKED` permet plusieurs workers sans coordination. Le volume (quelques centaines de jobs par heure) est très en dessous des limites de cette approche. |
| Front offline-first (Dexie / IndexedDB) | Un seul chemin de code pour le web et le mobile : l'interface lit toujours la base locale ; le réseau ne sert qu'à synchroniser. La lecture hors-ligne n'est pas un mode spécial mais le fonctionnement normal. |
| Résumés à la demande, en streaming | Coût maîtrisé (rien n'est résumé sans clic) et retour immédiat à l'écran. Le résumé est persisté, un second clic ne coûte rien. |
| YouTube par sous-titres uniquement | Gratuit et instantané. Pas de transcription audio : sans sous-titres, échec explicite. |
| Extraction HTTP puis repli navigateur | trafilatura sur le HTML brut couvre la majorité des sites ; Chromium n'est lancé que si le résultat est pauvre ou si le domaine est connu pour dépendre du JavaScript. |
| Mono-utilisateur | Pas de `user_id` dans le schéma. Le mot de passe est dans `settings`, les sessions sont des JWT courts (web) ou des tokens d'appareil opaques (mobile). |
| Un port HTTP, TLS délégué | L'utilisateur a déjà un reverse proxy. |

## 3. Composants

### api (`backend/fluxus/main.py`)

FastAPI avec un router par domaine (`auth`, `feeds`, `entries`, `summarize`, `sync`, `settings`).
Toutes les routes métier dépendent de `Auth` (`auth/deps.py`). Le build du front est monté à la
racine avec un repli SPA : tout chemin qui n'est ni `/api/*` ni un fichier existant renvoie
`index.html`. La documentation OpenAPI est sur `/api/docs` et `/api/openapi.json` ; ce dernier
sert à générer les types TypeScript du front.

### worker (`backend/fluxus/worker.py`)

Boucle asynchrone unique :

1. toutes les 30 s, met en file un `fetch_feed` pour chaque flux dont `next_fetch_at` est dépassé
   (avec `dedupe_key` pour ne jamais empiler deux fois le même flux) ;
2. réclame jusqu'à `WORKER_CONCURRENCY` jobs, les exécute en tâches concurrentes, chacune dans sa
   propre session ;
3. toutes les 6 h, purge les jobs terminés depuis plus de 3 jours.

Un job échoué est reprogrammé avec un délai `attempts²` minutes jusqu'à `max_attempts` (3), puis
passe en `failed`. Un job `running` depuis plus de 15 min est considéré perdu et repris. Les
handlers sont enregistrés par décorateur (`jobs/registry.py`) ; `handlers.py` importe les modules
qui en définissent.

### browser (`browser/server.py`)

FastAPI + Playwright dans l'image officielle Microsoft. Un Chromium unique, un contexte par
requête, deux rendus simultanés maximum. Images, polices, médias et domaines publicitaires connus
sont bloqués pour aller vite. Renvoie le HTML après `domcontentloaded` puis une attente courte de
`networkidle`. Non exposé hors du réseau Compose.

### postgres

PostgreSQL 16. Schéma géré par Alembic (`backend/alembic/`), appliqué au démarrage de `api`.
Recherche plein texte native : colonne `entries.search_vector` (tsvector) alimentée par un trigger
et indexée GIN.

### Front (`web/`)

React 19 + TypeScript + Vite 8 + Tailwind 4. État local dans IndexedDB via Dexie ; l'interface se
met à jour par `useLiveQuery`. Le service worker (vite-plugin-pwa) ne met en cache que la coquille
et les images : les appels `/api/*` ne sont jamais cachés, l'offline est assuré par Dexie.

## 4. Modèle de données

Défini dans `backend/fluxus/models.py`.

```
categories 1 ──── n feeds 1 ──── n entries 1 ──── n summaries
                                    │
                                    └──── 0..1 transcripts
jobs            settings (clé/valeur)          device_tokens
```

| Table | Rôle | Colonnes notables |
|---|---|---|
| `categories` | dossiers de la barre latérale | `title` unique, `position` |
| `feeds` | abonnements | `url` unique · `etag`, `last_modified` (GET conditionnel) · `next_fetch_at`, `fetch_interval_min` (planification adaptative) · `error_count`, `last_error` · `extraction_mode` ∈ {`none`,`http`,`browser`,`auto`} · `is_youtube` · `disabled` |
| `entries` | articles | `guid` unique par flux · `hash` (dédup par URL normalisée ou titre+date) · `summary_html` (contenu du flux, nettoyé) · `content_html`, `content_text`, `extraction_status` ∈ {`none`,`pending`,`done`,`failed`} · `read`, `starred` · `meta` JSONB (`youtube_id`, `cover`, `enclosures`, `extraction_method`) · `updated_at` (pivot de la synchronisation) · `search_vector` |
| `summaries` | résumés produits | `kind` ∈ {`article`,`video`} · `provider`, `model`, `prompt_version` · `text_md` · `input_tokens`, `output_tokens` |
| `transcripts` | sous-titres YouTube | `language`, `text`, `segments` JSONB (`start`, `duration`, `text`) |
| `jobs` | file de travail | `type`, `payload` JSONB · `status` ∈ {`queued`,`running`,`done`,`failed`} · `run_at`, `attempts`, `max_attempts`, `locked_at` · `dedupe_key` (index unique partiel) |
| `settings` | réglages et secrets | `key`, `value` ; les clés listées dans `SECRET_KEYS` sont chiffrées (Fernet dérivé de `FLUXUS_SECRET_KEY`) ; `_password_hash` est le mot de passe Argon2 ; `dashboard_layout` est le JSON de la disposition du tableau de bord (validé : JSON, ≤ 64 Ko, tableau `widgets`) |
| `device_tokens` | tokens de l'appli mobile | `token_hash` (SHA-256 du token `flx_…`), `last_seen_at` |

Règles importantes :

- **`entries.updated_at` est la seule horloge de synchronisation.** Toute modification visible par
  un client (état lu/favori, contenu extrait, nouveau résumé) doit la faire avancer. Les routes le
  font explicitement quand `onupdate` ne suffit pas.
- La suppression d'un flux supprime ses entrées (cascade) ; la suppression d'une catégorie met
  ses flux à `category_id = NULL`.
- Les timestamps sont tous `timezone=True`, en UTC.

## 5. Flux de traitement

### 5.1 Rafraîchissement d'un flux

```
worker.schedule_due_feeds ─► jobs(fetch_feed, dedupe "fetch_feed:<id>")
   └► fetcher/jobs.fetch_feed_job ─► fetcher/service.refresh_feed
         1. assert_public_url (anti-SSRF)
         2. GET avec If-None-Match / If-Modified-Since
            304 → rien à faire, on rallonge l'intervalle
            4xx/5xx/réseau → _record_error : error_count++, next_fetch_at = now + intervalle × 2^n (≤ 24 h)
         3. fetcher/parse.parse_feed (feedparser)
            · normalise les URL (https, minuscules, suppression utm_*, fbclid…)
            · nettoie le HTML (extract/clean.sanitize_html)
            · calcule guid, hash, meta (youtube_id, cover, enclosures) ; cover = media:thumbnail,
              media:content image (type absent accepté si l'URL a une extension image), enclosure
              image ou première <img> du contenu
         4. _insert_new_entries : ignore ce qui existe déjà par guid OU hash
         5. met à jour titre/site/icône si manquants, etag, last_modified,
            fetch_interval_min (÷2 si ≥5 nouveautés, ×1.5 si aucune, bornes 10 min – 24 h)
```

L'ajout d'un flux par l'API (`POST /api/feeds`) télécharge une première fois le flux de manière
synchrone pour valider l'URL et obtenir le titre, puis met en file un `fetch_feed` immédiat. Si
l'URL est un site et non un flux, `fetcher/discover.py` cherche les balises `<link rel="alternate">`,
les chemins usuels (`/feed`, `/rss.xml`…) ou l'identifiant de chaîne YouTube ; s'il y a plusieurs
candidats, l'API renvoie `300` avec la liste et l'interface propose le choix.

### 5.2 Extraction de l'article complet

```
POST /api/entries/{id}/fetch-content   (synchrone : l'utilisateur attend)
  ou summarize/service._prepare_source  (avant un résumé si pas déjà extrait)
  ou jobs(extract_entry)                (handler disponible, non planifié automatiquement)
   └► extract/jobs.run_extraction ─► extract/service.extract_article(url, mode, reference_text_len)
         mode http    : GET + trafilatura.extract(output=html, favor_recall)
         mode browser : POST browser:3000/render puis trafilatura
         mode auto    : http d'abord ; navigateur si le texte fait < 400 caractères
                        ou < 1,2 × le contenu du flux, ou si le domaine est dans JS_HEAVY_DOMAINS ;
                        on garde le meilleur des deux
         résultat     : sanitize_html (nh3, URL absolues, iframes vidéo seulement) + html_to_text
   └► entry.content_html / content_text / extraction_status / meta.extraction_method
      + meta.cover = première image de l'article si le flux n'en fournissait pas
      L'échec est stocké dans extraction_error, jamais levé vers l'utilisateur comme 500.
```

### 5.3 Résumé (SSE)

```
POST /api/entries/{id}/summarize[?force=true]   → text/event-stream
   summarize/service.summarize_entry (générateur d'événements)
     1. résumé existant et !force → event done {summary, cached: true}   (aucun appel LLM)
     2. build_summarizer(settings)  → AnthropicSummarizer | OpenAISummarizer
        (clé absente → event error)
     3. _prepare_source
        · entrée YouTube : transcript en base sinon youtube/transcript.fetch_transcript
          → texte horodaté par tranches de 30 s  ("[12:30] …")
        · article : content_text, sinon run_extraction, sinon texte du flux ;
          < 200 caractères → error
     4. texte > 250 000 caractères → découpage en blocs de 120 000, résumé partiel de chaque
        bloc (events status), puis fusion (prompts.merge_prompt)
     5. streaming : chaque morceau → event delta {text}
     6. persistance Summary (tokens inclus), entry.updated_at = now → event done {summary}
```

Le front lit le flux avec `fetch` + `ReadableStream` (`web/src/api/sse.ts`) car `EventSource`
ne sait pas faire de POST ; les fins de ligne `\r\n` de sse-starlette sont normalisées.
Événements : `status`, `delta`, `done`, `error`. Les prompts (`summarize/prompts.py`) portent un
`PROMPT_VERSION` stocké avec chaque résumé.

Côté Anthropic : `client.messages.stream` avec `thinking={"type": "adaptive"}` et
`output_config={"effort": …}` (sauf Haiku) ; `stop_reason == "refusal"` devient une erreur lisible.
Côté OpenAI : `chat.completions.create(stream=True, stream_options.include_usage)`.

### 5.4 Synchronisation hors-ligne

```
Client                                   Serveur
──────                                   ───────
pushOutbox : POST /api/sync {changes:[{entry_id, read?, starred?, at}]}
                                         dernière écriture gagne : un changement plus ancien que
                                         entries.updated_at et contradictoire est ignoré
GET /api/sync?since=<dernière passe>     page 1 : entries.updated_at > since − 2 s
GET /api/sync?cursor=<opaque>            pages suivantes : (updated_at, id) > curseur
   …jusqu'à has_more = false             première sync (sans since) : published_at > now − 30 j
mergeFeeds (page 1) : remplace catégories et flux, supprime les entrées orphelines
mergeEntries : bulkPut ; un état local encore dans l'outbox n'est pas écrasé
sync.since := server_time de la page 1   (seulement si la passe est complète)
applyRetention : supprime localement les articles lus, non favoris, > 30 j
```

Le curseur composite `(updated_at, id)` est indispensable : un import OPML crée des centaines
d'entrées dans la même seconde, et une pagination par simple timestamp avec marge tournait en rond.

### 5.5 Authentification

```
GET  /api/auth/status  → setup_required        POST /api/auth/setup (une seule fois)
POST /api/auth/login   → JWT HS256, 7 jours    POST /api/auth/refresh
POST /api/auth/devices {name, password} → token opaque "flx_…" (mobile), stocké haché
Authorization: Bearer <jwt | flx_…>   (ou ?token= pour les cas sans en-tête)
```

`auth/deps.require_auth` accepte les deux formes. Cinq échecs par minute et par IP bloquent le
login (`LoginRateLimiter`, en mémoire, par processus).

## 6. API HTTP

Documentation vivante sur `/api/docs`. Résumé des routes (toutes sous `/api`, authentifiées sauf
mention) :

| Domaine | Routes |
|---|---|
| auth | `GET status`*, `POST setup`*, `POST login`*, `POST refresh`, `POST password`, `GET/POST devices`*, `DELETE devices/{id}` |
| catégories | `GET/POST categories`, `PUT/DELETE categories/{id}` |
| flux | `GET/POST feeds`, `POST feeds/discover`, `GET/PUT/DELETE feeds/{id}`, `POST feeds/{id}/refresh`, `POST feeds/refresh-all`, `POST opml/import`, `GET opml/export` |
| articles | `GET entries` (filtres `feed`, `category`, `status`, `starred`, `q`, `cursor`, `limit`, `order`), `GET/PATCH entries/{id}`, `POST entries/mark-read`, `POST entries/{id}/fetch-content`, `GET entries/{id}/summaries` |
| résumés | `POST entries/{id}/summarize` (SSE), `GET/DELETE entries/{id}/summary` |
| sync | `GET sync`, `POST sync` |
| réglages | `GET/PATCH settings`, `GET settings/stats` |
| système | `GET health`* |

\* sans authentification (`POST devices` exige le mot de passe dans le corps).

Les listes d'articles sont paginées par curseur `(published_at, id)` encodé en base64. Les erreurs
suivent le format FastAPI `{"detail": …}` ; `300 Multiple Choices` sur `POST feeds` transporte
`{"message", "choices": [...]}`.

## 7. Front : offline-first

### Principe

```
                 ┌──────────────┐  useLiveQuery  ┌──────────────┐
  composants ◄───┤ Dexie (IDB)  │◄───────────────┤  sync/engine │◄──► /api/sync
                 │ entries      │                │  pull / push │
  actions ───────► feeds, cats  │──── outbox ────►              │
  (lu, favori)   │ summaries    │                └──────────────┘
                 └──────────────┘
```

- **Lecture** : `EntryList`, `Sidebar`, `Reader` interrogent Dexie. Les index composites
  (`[feed_id+readFlag+publishedTs]`, `[readFlag+publishedTs]`, `[starredFlag+publishedTs]`) évitent
  de trier en mémoire ; les booléens sont stockés en `0/1` car IndexedDB ne les indexe pas.
- **Écriture d'état** : `recordChange` / `recordBulkRead` modifient Dexie et ajoutent une ligne à
  `outbox`, puis tentent un push immédiat. L'interface réagit avant tout aller-retour réseau.
- **Actions réseau** (ajouter un flux, extraire, résumer, réglages) : appel direct de l'API via
  le client typé `openapi-fetch`, puis mise à jour de Dexie avec la réponse.
- **Déclencheurs de sync** : démarrage, `online`, retour au premier plan, toutes les 5 min,
  bouton ↻, et après un ajout de flux ou un import OPML (avec délai pour laisser le worker travailler).

### Navigation et état d'interface

La vue courante vit dans l'URL (`app/view.ts`) : `/` (tableau de bord), `/all`, `/unread`,
`/starred`, `/feed/:id`, `/category/:id`, `/search?q=`, plus `?entry=<id>` et `?unread=1`. Le
layout (`app/Layout.tsx`) affiche le tableau de bord pleine largeur, ou un, deux ou trois volets
selon la largeur (`md` 768 px, `lg` 1024 px), et gère les raccourcis clavier globaux
(`app/useKeyboard.ts`, désactivés dans les champs et les dialogues ; réduits sur le tableau de bord
pour laisser les flèches à dnd-kit). `openEntryIn(view, id)` ouvre un article dans le contexte
d'une vue (`/category/3?entry=42`) : c'est ce que font les widgets.

### Tableau de bord (`features/dashboard/`)

```
Dashboard ─ DashboardDataProvider (feeds, categories, unreadByFeed : 3 liveQueries partagées)
   ├─ useDashboardLayout ── meta['dashboard.layout'] (cache) ⇄ settings.dashboard_layout (serveur)
   ├─ DashboardGrid ── DndContext + SortableContext (stratégie neutre) ── SortableWidget × n
   │      └─ WidgetFrame (chrome commun, menu Monter/Descendre/Largeur/Configurer/Retirer)
   │            └─ NewsWidget | YoutubeWidget | StarredWidget | SummariesWidget | StatsWidget
   └─ WidgetConfigDialog
```

- **Modèle** (`layout.ts`) : `{ version, updatedAt, widgets[] }`, widgets typés (`news`, `youtube`,
  `starred`, `summaries`, `stats`), largeur `2 | 3 | 6` colonnes sur une grille de 6. Un widget
  `news` a un `display` ∈ {`carousel`, `magazine`, `cards`, `list`} rendu par `widgets/NewsWidget.tsx`
  (composant `Cover` : image `meta.cover`, miniature YouTube, sinon substitut teinté par flux). `parseLayout`
  valide défensivement et renvoie `ok | empty | newer` ; un widget de type inconnu est conservé et
  rendu « non pris en charge » ; une `version` supérieure passe le dashboard en lecture seule.
  `buildDefaultLayout` dérive une disposition des catégories quand rien n'est stocké ; elle n'est
  persistée qu'à la première modification.
- **Requêtes** (`queries.ts`) : `latestForFeeds` fait une requête indexée par flux
  (`[feed_id+readFlag+publishedTs]`, `.reverse().limit(n)`) et fusionne : le tri mémoire est borné à
  `flux × n`, contrairement à la liste principale qui charge toute une catégorie. « Lus aujourd'hui »
  utilise l'index `[readFlag+updatedTs]` ajouté en **Dexie v2** (`updatedTs` dérivé de `updated_at`,
  rempli par `upgrade()`). Les tokens viennent de `/api/settings/stats`, mis en cache 10 min dans
  `meta`.
- **Sauvegarde** (`useDashboardLayout.ts`) : écriture locale immédiate + drapeau `dirty`, PATCH après
  1 s de silence, envoi `keepalive` sur `pagehide`, rejeu à `online` et dans `syncNow()` via
  `flushDashboardLayout()`. `reconcile(local, dirty, server)` (pure) : le `updatedAt` le plus récent
  gagne ; un serveur vide efface le cache local sauf si une modification locale attend ; `newer` →
  lecture seule.
- **Glisser-déposer** (`DashboardGrid.tsx`) : `@dnd-kit` avec une `SortingStrategy` neutre et un
  réordonnancement réel du tableau dans `onDragOver` (garde `lastOverId`, `closestCorners`), pour
  que la grille CSS reflue exactement malgré les largeurs mixtes. `DragOverlay` compact sans
  animation de dépôt. Capteurs souris (distance 6 px), tactile (appui 250 ms, `touch-none` sur la
  poignée seulement) et clavier (`sortableKeyboardCoordinates`). Le drag n'est actif qu'en mode
  « Personnaliser » ; le menu Monter/Descendre couvre l'accessibilité et le mobile.

### Thème

Classe `dark` sur `<html>`, appliquée avant le premier rendu par un script inline dans
`index.html` (pas de flash). Préférence dans `localStorage` (`fluxus.theme`) : `light`, `dark`,
`system`. Variables CSS (`--bg`, `--fg`…) exposées comme utilitaires Tailwind (`bg-app`,
`text-muted`, `border-app`).

### Typographie de lecture

Préférences par appareil (`app/reading.ts`, `localStorage['fluxus.reading']`) appliquées comme
attributs `data-reading-font|size|width` sur `<html>` par le script inline d'`index.html` puis par
`applyReadingPrefs`. `index.css` traduit chaque attribut en variables `--reading-body`,
`--reading-heading`, `--reading-size`, `--reading-width` consommées par `.prose-reader`,
`.reader-heading` et `.reader-column`. Les polices viennent de paquets fontsource importés dans
`index.css` (variables : Inter, Source Serif 4, Literata, Lora, Montserrat, JetBrains Mono ;
statique : Atkinson Hyperlegible). Chaque fichier woff2 est découpé par alphabet avec
`unicode-range` ; le service worker ne pré-cache que le latin et met les autres en cache à l'usage.
`components/TypographyPicker.tsx` est partagé entre la page Paramètres et le popover Aa du lecteur.

### Mobile (Capacitor)

`web/capacitor.config.ts` embarque le build (`webDir: dist`). L'adresse du serveur est saisie à la
connexion (`api/auth.ts` la mémorise, `reconfigureClient` recrée le client HTTP). Détection de la
plateforme par `window.Capacitor.isNativePlatform()`. Le bouton retour matériel remonte l'historique
(`App.tsx`). Le projet Android (`web/android/`) est généré par `cap add android` et versionné ;
`cap sync android` y recopie le build.

## 8. Sécurité

| Risque | Mesure | Où |
|---|---|---|
| SSRF via une URL de flux ou d'article | Résolution DNS puis refus des IP privées, loopback, link-local ; seuls http/https | `fetcher/http.assert_public_url`, appelé par le fetcher, la découverte et l'extraction |
| XSS par contenu de flux ou d'article | Nettoyage serveur (nh3, liste blanche de balises/attributs, iframes limités à YouTube/Vimeo/Dailymotion) et client (DOMPurify, liens `noopener`, images `no-referrer`) | `extract/clean.py`, `web/src/lib/markdown.ts` |
| Force brute sur le mot de passe | Argon2, 5 tentatives / min / IP, exigence de 8 caractères | `auth/security.py`, `auth/router.py` |
| Fuite des clés LLM | Chiffrées au repos (Fernet), jamais renvoyées en clair (masquées `sk-a…1234`), non journalisées | `settings_store.py`, `settings_router.py` |
| Tokens mobiles | Opaques, 256 bits, stockés hachés, révocables | `auth/security.py`, `auth/deps.py` |
| Rendu de pages arbitraires | Chromium isolé dans son conteneur, mémoire plafonnée, pas d'exposition réseau, ressources lourdes bloquées | `browser/`, `docker-compose.yml` |

Ce qui n'est **pas** couvert : TLS (délégué au reverse proxy), protection CSRF (l'API n'utilise pas
de cookies, seulement des en-têtes `Authorization`), multi-utilisateurs.

## 9. Carte du code

```
backend/
  alembic/                  migrations (env.py async, versions/)
  fluxus/
    main.py                 application FastAPI, montage des routers et du front
    worker.py               boucle du worker
    handlers.py             importe les modules qui enregistrent des handlers de jobs
    config.py               Settings pydantic (préfixe FLUXUS_, DATABASE_URL)
    db.py                   engine/session async, Base déclarative, session_scope()
    models.py               tables SQLAlchemy + SQL du trigger de recherche
    schemas.py              schémas Pydantic partagés (FeedOut, EntryOut…)
    settings_store.py       lecture/écriture de la table settings, chiffrement
    settings_router.py      GET/PATCH /settings, /settings/stats
    auth/                   security (argon2, JWT, tokens), deps (require_auth), router
    feeds/                  router (catégories, flux, OPML), opml.py (parse/build)
    entries/                router (liste paginée, états, extraction synchrone)
    fetcher/                http (client + anti-SSRF), parse (feedparser → entrées),
                            service (refresh_feed), discover (découverte), jobs (handler)
    extract/                clean (nh3, texte), service (trafilatura + navigateur), jobs
    summarize/              providers (Anthropic/OpenAI), prompts, service (orchestration), router (SSE)
    youtube/                transcript (youtube-transcript-api, texte horodaté)
    sync/                   router (pull par curseur, push last-write-wins)
  tests/                    pytest : unitaires (parse, clean, guard, opml, summarize)
                            et intégration test_api.py (nécessite DATABASE_URL)
browser/
  server.py                 FastAPI + Playwright : POST /render
scripts/
  build-apk.sh              build de l'APK dans un conteneur (Dockerfile.android)
  e2e-ui.py                 parcours Playwright de l'interface (liste, lecteur, mobile, hors-ligne)
  e2e-dashboard.py          parcours Playwright du tableau de bord (défaut, édition, drag, persistance, conflit)
web/
  src/main.tsx              point d'entrée
  src/app/                  App (routes, auth, thème, bouton retour), Layout (volets, raccourcis),
                            view (état dans l'URL), useKeyboard, theme
  src/api/                  client openapi-fetch + ApiError, auth (token, URL serveur),
                            sse (lecture d'un flux SSE en POST), schema.d.ts (généré)
  src/db/db.ts              schéma Dexie, LocalEntry, meta
  src/sync/                 engine (pull/push/rétention), useSync
  src/features/
    dashboard/              Dashboard (page), DashboardGrid (dnd-kit), WidgetFrame, WidgetConfigDialog,
                            DashboardData (contexte), layout (modèle), queries (Dexie), useDashboardLayout,
                            widgets/{News,Youtube,Starred,Summaries,Stats}Widget
    feeds/useUnreadCounts   non lus par flux, partagé Sidebar / dashboard
    auth/LoginPage          création du mot de passe, connexion, adresse serveur
    feeds/Sidebar           navigation, compteurs, badge de sync, FeedIcon
    feeds/AddFeedDialog     ajout avec découverte et choix multiple
    feeds/FeedDialog        édition/suppression d'un flux, CategoryDialog
    entries/EntryList       liste, filtres, recherche, densité, tout marquer lu
    reader/Reader           article, vidéo YouTube, barre d'actions, résumé
    summary/SummaryPanel    consommation du SSE, rendu Markdown, horodatages
    settings/SettingsPage   tous les réglages
  src/components/           ui (Button, IconButton, Dialog, Toast…), ThemeToggle
  src/lib/                  format (dates, hôtes), markdown (marked + DOMPurify)
  src/index.css             Tailwind, variables de thème, typographie de lecture
  capacitor.config.ts       configuration Android
  android/                  projet natif généré (versionné)
docker-compose.yml          postgres, api, worker, browser
backend/Dockerfile          multi-étapes : build du front (Node 22) puis image Python (uv)
```

## 10. Conventions et recettes

### Conventions

- Backend : Python 3.12, tout en async, une session SQLAlchemy par requête ou par job, `ruff`
  (`E,F,I,B,UP`, ligne 120). Messages d'erreur destinés à l'utilisateur en français, dans `detail`.
- Front : TypeScript strict (`noUnusedLocals`), composants fonctionnels, pas de bibliothèque d'état
  globale : Dexie + URL suffisent. Types d'API importés depuis `schema.d.ts`, jamais redéclarés.
- Un changement d'API se fait dans cet ordre : route/schéma Python → `npm run gen:api` →
  code front. Le typage échoue si le front n'est pas à jour.
- Tout ce qui change une entrée visible par un client met `entry.updated_at` à `now`.

### Ajouter un type de job

1. Écrire `async def handler(session, job)` décoré `@job_handler("mon_job")` dans le module
   concerné ; importer ce module dans `handlers.py`.
2. Mettre en file avec `enqueue(session, "mon_job", payload, dedupe_key=…)`.
3. Le handler doit être idempotent : il peut être rejoué après un crash.

### Ajouter un fournisseur LLM

Sous-classer `Summarizer` dans `summarize/providers.py`, implémenter `stream()` qui renvoie un
`StreamResult` (générateur de texte + `Usage` rempli à la fin), l'ajouter dans `build_summarizer`,
puis exposer ses réglages dans `settings_store.DEFAULTS` et `SettingsPage`.

### Ajouter un type de widget

1. Étendre l'union `Widget` et `normalizeWidget` / `blankWidget` / `widgetTitle` dans `layout.ts`,
   ajouter le libellé dans `TYPE_LABEL`.
2. Écrire la requête dans `queries.ts` en partant des index existants (`db.ts`).
3. Créer `widgets/MonWidget.tsx` autour de `WidgetFrame`, le brancher dans `WidgetSwitch`
   (`DashboardGrid.tsx`) et ses champs dans `WidgetConfigDialog.tsx`.
4. Si le nouveau type change la forme du JSON existant, incrémenter `LAYOUT_VERSION` et écrire la
   migration dans `migrate()`.

### Ajouter un réglage

Ajouter la clé dans `settings_store.DEFAULTS` (et `SECRET_KEYS` si c'est un secret), puis un champ
dans `SettingsPage`. `PATCH /settings` refuse les clés inconnues.

### Modifier le schéma

Modifier `models.py`, puis `uv run alembic revision --autogenerate -m "…"`, relire la migration
(les triggers et index partiels ne sont pas toujours détectés), `alembic upgrade head`. Si une
colonne d'`entries` doit être synchronisée, l'ajouter à `EntryOut` (`schemas.py`) et à
`LocalEntry` (`db.ts`), puis incrémenter la version Dexie si un index change.

### Tester

- Unitaires : `uv run pytest --ignore=tests/test_api.py`.
- Intégration : `test_api.py` recrée le schéma dans la base pointée par `DATABASE_URL` (utiliser
  une base dédiée `fluxus_test`). Il couvre l'auth, les tokens d'appareil, l'anti-SSRF, la liste et
  la pagination, la recherche, la sync (pull, curseur à timestamps identiques, push last-write-wins),
  le masquage des secrets et la chaîne SSE de résumé avec un fournisseur simulé.
- Interface : `scripts/e2e-ui.py` (Playwright) joue le parcours complet, y compris mobile et
  hors-ligne ; `scripts/e2e-dashboard.py` couvre le tableau de bord (disposition par défaut,
  navigation vers le lecteur, tout marquer lu, menu, largeur, ajout, retrait, drag souris et
  clavier, persistance serveur, conflit, lecture seule, mobile hors-ligne). Les deux produisent des
  captures.

## 11. Limites connues et pistes

- **Pas de sync planifiée quand l'appli Android est fermée** : la synchronisation se fait à
  l'ouverture. Piste : Capacitor Background Runner.
- **Le limiteur de connexion est en mémoire** : par processus, remis à zéro au redémarrage.
- **Pas de suppression propagée d'articles individuels** : la sync ne supprime localement que les
  entrées dont le flux a disparu (`deleted_entry_ids` est réservé mais toujours vide).
- **Recherche hors-ligne limitée** aux titres et extraits ; le plein texte est côté serveur.
- **Un seul utilisateur** par instance, par conception.
- Pistes de phase 5 : règles de filtrage par flux (mots-clés), statistiques de lecture, vue
  magazine, notifications, sauvegarde automatisée de la base.
