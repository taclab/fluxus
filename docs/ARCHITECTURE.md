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
categories 1 ──── n feeds 1 ──── n entries 1 ──── n summaries ──0..1── smart_feeds
                                    │  n                                   │ 1
                                    └──── 0..1 transcripts                 │
                                    └──── n smart_feed_matches n ──────────┘
jobs            settings (clé/valeur)          device_tokens
```

| Table | Rôle | Colonnes notables |
|---|---|---|
| `categories` | dossiers de la barre latérale | `title` unique, `position` · `view_mode` (présentation, héritée par ses flux ; null = défaut) |
| `feeds` | abonnements | `url` unique · `position` (ordre manuel dans son dossier ; sans dossier, sur la même échelle que `categories.position` : la racine mélange dossiers et flux ; un nouveau flux prend `min(position) − 1` de son conteneur) · `etag`, `last_modified` (GET conditionnel) · `next_fetch_at`, `fetch_interval_min` (planification adaptative) · `error_count`, `last_error` · `extraction_mode` ∈ {`none`,`http`,`browser`,`auto`} · `is_youtube` · `disabled` · `fetch_interval_fixed_min` (null = adaptatif) · `view_mode` (null = dossier, puis défaut) · `auto_summary`, `summary_daily_cap` (résumés automatiques de tous les nouveaux articles) |
| `entries` | articles | `guid` unique par flux · `hash` (dédup par URL normalisée ou titre+date) · `summary_html` (contenu du flux, nettoyé) · `content_html`, `content_text`, `extraction_status` ∈ {`none`,`pending`,`done`,`failed`} · `read`, `starred` · `meta` JSONB (`youtube_id`, `cover`, `enclosures`, `extraction_method`) · `updated_at` (pivot de la synchronisation) · `search_vector` |
| `summaries` | résumés produits | `kind` ∈ {`article`,`video`} · `provider`, `model`, `prompt_version` · `text_md` · `input_tokens`, `output_tokens` · `origin` ∈ {`manual`,`auto`} · `smart_feed_id` (SET NULL) |
| `smart_feeds` | recherches enregistrées | `name` unique · `scope_kind` ∈ {`all`,`category`,`feed`}, `scope_id` · `mode` ∈ {`keywords`,`regex`} · `keywords_any/all/none` JSONB · `regex` (≤ 300) · `fields` JSONB (title, excerpt, author, url, content) · `auto_summary`, `summary_daily_cap` · `view_mode` |
| `smart_feed_matches` | correspondances calculées | PK (`smart_feed_id`, `entry_id`), `matched_at` ; exposées comme `smart_feed_ids` sur chaque entrée |
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
         1. safe_get : contrôle anti-SSRF de l'URL puis de CHAQUE redirection (DNS asynchrone),
            délai global 60 s, corps lu en flux et borné à 10 Mo
         2. GET avec If-None-Match / If-Modified-Since
            304 → rien à faire, on rallonge l'intervalle (× 1,5)
            4xx/5xx/réseau → _record_error : error_count++, next_fetch_at = now + intervalle × 2^n (≤ 24 h)
         3. fetcher/parse.parse_feed (feedparser)
            · normalise les URL (https, minuscules, suppression utm_*, fbclid…)
            · nettoie le HTML (extract/clean.sanitize_html)
            · calcule guid, hash, meta (youtube_id, cover, enclosures) ; cover = media:thumbnail,
              media:content image (type absent accepté si l'URL a une extension image), enclosure
              image ou première <img> du contenu
         4. _insert_new_entries : ignore ce qui existe déjà par guid OU hash (aussi au sein du lot) ;
            si le flux est en mode `http` ou `browser`, met en file un `extract_entry` par nouveauté
         5. met à jour titre/site/icône si manquants, etag, last_modified,
            fetch_interval_min (÷2 si ≥5 nouveautés, ×1.5 si aucune, bornes 10 min – 24 h)
         Toute exception inattendue (contrainte SQL…) applique quand même le recul exponentiel,
         dans une transaction séparée : un flux ne peut pas être re-planifié toutes les 30 s.
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
                                         dernière écriture gagne **par champ** : `read` et `starred`
                                         ont chacun leur horloge (read_changed_at, starred_changed_at
                                         = date de l'action, bornée à now) ; un changement n'est
                                         refusé que si ce champ a changé plus récemment ; les refus
                                         reviennent dans `conflicts` avec l'état serveur, le client
                                         réaligne son cache (sauf action locale encore en attente)
GET /api/sync?since=<dernière passe>     page 1 : entries.updated_at > since − 2 s
GET /api/sync?cursor=<opaque>            pages suivantes : (updated_at, id) > curseur
   …jusqu'à has_more = false             première sync (sans since) : published_at > now − N j
                                         (réglage sync_prefetch_days, renvoyé comme retention_days)
                                         OU starred = true (les favoris sont toujours envoyés)
mergeFeeds (page 1) : remplace catégories et flux, supprime les entrées orphelines
mergeEntries : bulkPut ; un état local encore dans l'outbox n'est pas écrasé
sync.since := server_time de la page 1   (seulement si la passe est complète) ; server_time =
                                         min(now, début de la plus ancienne transaction ouverte)
applyRetention : supprime localement les articles lus, non favoris, > 30 j
```

Le curseur composite `(updated_at, id)` est indispensable : un import OPML crée des centaines
d'entrées dans la même seconde, et une pagination par simple timestamp avec marge tournait en rond.

**Visibilité des commits.** Une transaction longue (extraction, évaluation de flux intelligent) fixe
`updated_at` à l'horloge Python puis valide plus tard ; un client qui aurait pris `since = now` entre
les deux ne la verrait jamais. Le serveur renvoie donc comme `server_time` le début de la plus
ancienne transaction encore ouverte sur la base (`pg_stat_activity.xact_start`), s'il est antérieur
à `now`. Le client relit au pire quelques minutes de changements déjà vus (les écritures sont
idempotentes), jamais moins. La marge de 2 s absorbe l'écart entre l'horloge Python et `xact_start`.

**Horloges par champ.** `updated_at` reste le pivot de réplication (contenu, résumés,
correspondances), mais la résolution des conflits d'états utilisateur ne s'y compare plus : toute
écriture serveur de `read` ou `starred` (PATCH, mark-read, push) pose `read_changed_at` /
`starred_changed_at`. Un favori posé hors ligne avant une extraction n'est donc plus refusé, et les
actions successives d'un même lot s'appliquent dans l'ordre de leurs dates.

`syncNow()` appelé pendant une passe en cours ne se contente pas de l'attendre : il **met une seconde
passe en file** (une seule, quel que soit le nombre d'appels). La passe en cours a pu lire le serveur
avant le changement qui motive l'appel (création d'un flux intelligent, réorganisation), et la
première synchronisation d'un appareil dure plusieurs secondes.

### 5.5 Flux intelligents

Une seule évaluation, en SQL (`smartfeeds/rules.py` → `build_condition`) : portée (`feed_id`,
sous-requête catégorie), mots-clés `unaccent(coalesce(col,'')) ILIKE '%mot%'` avec échappement,
« tous » en AND, « aucun » en NOT OR ; regex `unaccent(concat_ws(champs)) ~* pattern`. Toutes les
colonnes passent par `coalesce` (un auteur NULL ne doit pas exclure une entrée de « aucun »). La
syntaxe regex est validée **par PostgreSQL** (`begin_nested` + sqlstate 2201B) ; références arrière
interdites, longueur ≤ 300.

```
fetcher._insert_new_entries ─► evaluate(feed, new_ids)      savepoint + SET LOCAL statement_timeout 5 s,
extract.run_extraction ──────► evaluate(content_only=True)  remis à DEFAULT avant RELEASE ; une erreur
POST/PUT /smart-feeds ───────► apply(sf)                    n'annule jamais le lot d'articles
feeds.update_feed (catégorie) ► reevaluate_feed(feed)
        └─► nouvelles paires ─► schedule_auto_summaries ─► jobs summarize_entry (étalés de 20 s)
```

`apply` recalcule sur la fenêtre `published_at > now − sync_prefetch_days OR starred`, par
`DELETE … RETURNING` puis `INSERT … RETURNING`, et bump `updated_at` (horloge Python, dernière
instruction) de la différence symétrique : les clients reçoivent les entrées qui entrent **et** qui
sortent, `bulkPut` remplaçant l'objet entier. `remove` collecte les ids avant la cascade.

Résumés automatiques (`summarize/auto.py` : budget, réservation, mise en file, partagés) : quota du
jour = résumés `origin='auto'` du flux intelligent + jobs `summarize_entry` en attente ou en cours ;
budget global = tokens `origin='auto'` du jour (UTC) d'après `llm_usage` ; vérifiés à la
planification et au démarrage du job. Un **flux** peut aussi résumer tous ses nouveaux articles
(`feeds.auto_summary`, opt-in) : `schedule_feed_auto_summaries` est appelé à l'insertion, après les
flux intelligents ; son quota compte les résumés `auto` du jour des articles du flux + les jobs dont
`payload.feed_id` est ce flux ; le job vérifie que l'option est toujours active. La clé de
dédoublonnage `summarize:{entry_id}` est commune : un article couvert par les deux n'est résumé
qu'une fois. « Résumer les non résumés » existe pour un flux (`POST /api/feeds/{id}/summarize-all`)
comme pour un flux intelligent : lot manuel borné par le budget restant. Le job classe les erreurs : permanentes
(contenu absent, clé absente…) → terminé sans relance ; fournisseur → relance ×2. Modèle dédié
`llm_auto_model` via `build_summarizer({**settings, "llm_model": …})`.

### 5.6 Authentification

```
GET  /api/auth/status  → setup_required        POST /api/auth/setup (une seule fois)
POST /api/auth/login   → JWT HS256, 7 jours, claim `pv` = version du mot de passe
POST /api/auth/refresh (sessions web seulement)   POST /api/auth/password → invalide les autres JWT
POST /api/auth/devices {name, password} → token opaque "flx_…" (mobile), stocké haché
Authorization: Bearer <jwt | flx_…>   (ou ?token= pour les cas sans en-tête)
```

`auth/deps.require_auth` accepte les deux formes et rejette un JWT dont `pv` ne correspond plus à
`settings._password_version`. Cinq échecs par minute et par IP, et vingt toutes IP confondues,
bloquent le login (`LoginRateLimiter`, en mémoire, par processus) : le plafond global rend inutile
la falsification de `X-Forwarded-For`.

## 6. API HTTP

Documentation vivante sur `/api/docs`. Résumé des routes (toutes sous `/api`, authentifiées sauf
mention) :

| Domaine | Routes |
|---|---|
| auth | `GET status`*, `POST setup`*, `POST login`*, `POST refresh` (JWT seulement), `POST password` (renvoie un nouveau JWT), `GET devices`, `POST devices`*, `DELETE devices/{id}` |
| catégories | `GET/POST categories`, `PUT/DELETE categories/{id}` |
| ordre | `POST subscriptions/reorder` : positions des dossiers, des flux (avec `category_id`) et des flux intelligents en une transaction ; un flux qui change de dossier est ré-évalué par les flux intelligents à portée catégorie |
| flux | `GET/POST feeds` (triés par dossier puis `position`), `POST feeds/discover`, `GET/PUT/DELETE feeds/{id}`, `POST feeds/{id}/refresh`, `POST feeds/refresh-all`, `POST opml/import`, `GET opml/export` |
| articles | `GET entries` (filtres `feed`, `category`, `status`, `starred`, `q`, `cursor`, `limit`, `order`), `GET/PATCH entries/{id}`, `POST entries/mark-read` (par ids, flux, catégorie, flux intelligent, favoris ou tout, borne `before`), `POST entries/{id}/fetch-content`, `GET entries/{id}/summaries` |
| résumés | `POST entries/{id}/summarize` (SSE), `GET/DELETE entries/{id}/summary` |
| sync | `GET sync` (renvoie aussi `smart_feeds` ; `server_time` = horizon de visibilité des commits), `POST sync` (par champ, renvoie `conflicts`) |
| flux intelligents | `GET/POST smart-feeds`, `GET/PUT/DELETE smart-feeds/{id}` (PUT ré-applique seulement si la définition change), `POST smart-feeds/preview`, `POST smart-feeds/{id}/summarize-all` |
| réglages | `GET/PATCH settings` (disposition du tableau de bord versionnée : `dashboard_layout_version`, `PATCH` exige `dashboard_layout_base_version`, 409 avec la disposition courante si périmée), `GET settings/stats` |
| système | `GET health`* |

\* sans authentification (`POST devices` exige le mot de passe dans le corps).

Les listes d'articles sont paginées par curseur `(published_at, id)` encodé en base64. Les erreurs
suivent le format FastAPI `{"detail": …}` ; `300 Multiple Choices` sur `POST feeds` transporte
`{"message", "choices": [...]}`.

## 7. Front : offline-first

### Principe

La base Dexie est nommée d'après le serveur (`db.databaseName()` : `fluxus` pour la même origine
web, `fluxus@<hôte>` sinon) : cache, outbox et curseur de sync d'une instance ne sont jamais rejoués
sur une autre. Changer d'adresse sur l'écran de connexion recharge l'application pour ouvrir la base
correspondante ; la déconnexion explicite efface la base courante.

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

### Présentation par vue (`features/entries/viewMode.ts`, `useViewMode.ts`, `features/reader/ReaderModal.tsx`)

Six présentations dans une table unique `VIEW_MODE_SPECS` (libellé, icône, disposition, classes du
`<ul>`) : `compact` et `cards` gardent l'article **à côté** de la liste, `grid`, `magazine`, `list` et
`titles` donnent la **pleine largeur**. La disposition se déduit de la présentation (`layoutOf`) : il
n'y a plus de réglage de disposition séparé.

La présentation est un **attribut serveur**, synchronisé : colonne `view_mode` (nullable) de `feeds`,
`categories` et `smart_feeds`, et réglage `default_view_mode` (envoyé aussi dans `GET /api/sync`).
Résolution (`resolveViewMode`, pur) : flux → son dossier → défaut ; dossier ou flux intelligent → le
sien → défaut ; Tous, Non lus, Favoris et Recherche → défaut, donc le menu de ces vues modifie le
défaut. `useViewMode(view)` lit par clé dans Dexie (`feeds.get`, `categories.get`, `meta`), sans
scan. `Layout` résout une fois et passe le résultat à `EntryList` ; rien n'est monté tant que la
première lecture n'est pas revenue, pour éviter une disposition qui basculerait aussitôt.

Écriture (`setViewMode(target, mode | 'inherit')`) : mise à jour Dexie immédiate, puis
`PUT /api/view-mode` ; en cas d'échec réseau le choix reste dans `meta.viewMode.pending`, rejoué par
`syncNow` avant la passe de lecture, et réappliqué par `mergeFeeds` (dans la même transaction) tant
qu'il n'est pas parti. Les écritures par un autre chemin (fenêtre du flux, action groupée) retirent la
clé de la file (`forgetPendingViewModes`). Les anciennes préférences d'appareil
(`fluxus.listLayout`, `fluxus.density`, `fluxus.fullDensity`) sont reprises une fois comme défaut si
le serveur a encore le défaut d'origine, puis effacées.

En pleine largeur, `Layout` monte `EntryList` en `flex-1` et, si un article est ouvert, la fenêtre
`ReaderModal` (portail, plein écran sous `md`) qui contient le `Reader` habituel avec `onBack` =
fermer ; `← →` sont déclarées **dans la fenêtre** (`useKeyboard` monté avec elle) et appellent le
même `move()` que `j/k` ; la position « n / total » vient de l'ordre fourni par `onVisibleEntries`.
La fenêtre **ne porte pas `data-dialog`** : les raccourcis globaux doivent y rester actifs. Dans les
rendus pleine largeur (`FullRow`), le titre est le seul bouton d'ouverture, étiré sur la ligne par un
pseudo-élément ; les actions rapides favori et lu sont des frères placés au-dessus. L'élément
d'ouverture de chaque ligne porte `data-entry-focus`, contrat que la fenêtre de lecture cible pour
rendre le focus à la fermeture.

### Écran Abonnements (`features/feeds/SubscriptionsPage.tsx`, `feedSettings.ts`)

Route `/abonnements`. Flux et dossiers lus dans Dexie (consultables hors ligne), activité
(`GET /api/feeds/stats` : articles sur 7 et 30 jours, dernier article, résumés automatiques du jour,
une requête agrégée) et budget en ligne. Tableau groupé par dossier dans l'ordre `position` (ou liste
triée), édition sur place optimiste (`updateFeed` : patch Dexie, `PUT /feeds/{id}`, réponse serveur
ou retour arrière), actions groupées par `POST /api/feeds/bulk` (tout ou rien, même logique que la
modification unitaire : `feeds/router._apply_feed_settings`). Sur téléphone, une carte par flux ouvre
`FeedDialog`, qui partage libellés et écritures via `feedSettings.ts`.

Réglages par flux côté serveur : `disabled` (plus aucune récupération), `extraction_mode` (« toujours »
= extraction de chaque nouvel article en arrière-plan), `fetch_interval_fixed_min` (null = intervalle
adaptatif ; sinon `_next_interval` le renvoie tel quel, le recul sur erreur ne descend jamais sous lui,
et un changement avance `next_fetch_at` sans jamais le reculer), `auto_summary` + `summary_daily_cap`
(voir ci-dessous) et `view_mode`.

### Sauvegarde complète (`backend/fluxus/backup/`, `features/settings/BackupSection.tsx`)

`GET /api/backup/export` produit en flux un JSON Lines gzip.

- **En-tête** : `format`, `version` (1), révision Alembic, date, comptes par table.
- **Lignes** : une par enregistrement (`{"t": table, "r": colonnes}`), dans l'ordre des clés étrangères de `EXPORT_TABLES`. Les identifiants sont conservés et `search_vector` est omis.
- **Lecture** : instantané `REPEATABLE READ`, par lots de 500.
- **Exclusions** : les réglages exportés se limitent aux clés de `DEFAULTS`, hors `SECRET_KEYS` et hors clés `_…`. Mot de passe, jetons d'appareils, jobs et `llm_usage` ne sont jamais exportés.

`POST /api/backup/import` (multipart `file`, `password`, `confirm=REMPLACER`, limiteur de la
connexion ; formulaire lu après l'authentification et le contrôle de `Content-Length`, 1 Go au plus) :

1. **Validation complète d'abord**, hors de la boucle asyncio (`asyncio.to_thread`) : format, version, tables et colonnes connues, ordre, comptes, lignes de 64 Mo au plus.
2. **Rétrocompatibilité** : une sauvegarde plus ancienne (colonnes absentes) prend les défauts ; une plus récente est refusée. Si la base rejette l'import, le message cite les deux révisions de schéma quand elles diffèrent.
3. **Verrou de maintenance** : `pg_try_advisory_xact_lock(727003)`, jusqu'à 60 s, sinon 503 sans effet. Chaque job du worker tient ce verrou en partagé, sur une connexion dédiée, pendant toute son exécution. L'import attend donc la fin des jobs en cours ; un job qui attendait l'import constate ensuite que sa ligne a disparu et s'arrête.
4. **Remplacement, en une transaction** :
   - `DELETE` dans l'ordre des dépendances ;
   - insertion par lots, lecture du fichier par tranches dans un thread ;
   - `setval` des séquences ;
   - `_dashboard_layout_version` + 1 ;
   - nouveau `_sync_epoch`.

   Le journal `llm_usage` est conservé, pour le budget du jour ; ses liens passent à NULL. Une violation de contrainte provoque un rollback complet et une erreur 400.

**Époque de synchronisation** : `GET /api/sync/epoch`, aussi dans `SyncPull.epoch`. Elle vaut `"0"` tant qu'aucun import n'a eu lieu (lecture seule, sans écriture concurrente).

- `syncNow` lit l'époque **avant** d'envoyer l'outbox et les choix en attente (`adoptEpoch`).
- Si le cache a été construit pour une autre époque, ou n'en connaît aucune alors que l'instance a déjà été restaurée, la base Dexie est vidée (`clearDatabase`) puis tout est rechargé sans `since`.
- `POST /api/sync` et `PUT /api/view-mode` reçoivent l'époque du client et répondent 409 si elle est périmée. Un changement fait avant un import n'est jamais appliqué au contenu qui porte désormais le même identifiant.

### Ordre de la barre latérale (`features/feeds/SubscriptionTree.tsx`, `sidebarOrder.ts`)

Un seul `DndContext` couvre les flux intelligents et les abonnements. Les capteurs souris et
tactile exigent un **clic long** (300 ms, tolérance 6 px) : un clic court reste un clic, un mouvement
tactile avant le délai reste un défilement (dnd-kit annule la prise et laisse passer le geste). Pas de
capteur clavier : Entrée et Espace doivent continuer à ouvrir la vue.

Le motif est celui du tableau de bord : stratégie de tri neutre et **brouillon** (`Draft` : liste
racine mêlant dossiers et flux sans dossier, flux par dossier, flux intelligents) réordonné réellement
dans `onDragOver`/`onDragMove` par `moveInDraft`, le DOM reflue donc pendant le geste. Les règles sont
pures et sans React (`sidebarOrder.ts`) : un dossier ne survole que des éléments de la racine, un flux
survole des flux (de dossier ou racine) ou des en-têtes de dossier (`canDropOn`, appliqué comme filtre
de détection de collision, `pointerWithin` puis `closestCenter`). Sur un en-tête, la **zone** dépend du
pointeur : tiers supérieur → le flux se pose à la racine juste avant le dossier ; sinon → en tête du
dossier (un dossier replié attend 600 ms avant de s'ouvrir ; le dépôt s'applique même avant).

Au dépôt, `diffDraft` renumérote `0..n-1` les seules listes modifiées (racine : dossiers et flux
sans dossier ensemble, flux intelligents, dossiers source et destination des flux) et produit à la fois le corps de
`POST /api/subscriptions/reorder` et les lignes Dexie : écriture locale optimiste, requête, puis
`syncNow()` (le serveur fait foi ; en cas d'échec ou hors-ligne, un toast et la sync restaurent
l'ordre serveur). Le brouillon reste affiché jusqu'à ce que Dexie reflète le dépôt, pour éviter un
retour furtif à l'ancien ordre ; un filet de 3 s le relâche sinon, jamais pendant un autre glisser.
Les clics droit et double-clic (édition) sont ignorés pendant une prise.

### Thème

Classe `dark` sur `<html>`, appliquée avant le premier rendu par un script inline dans
`index.html` (pas de flash). Préférence dans `localStorage` (`fluxus.theme`) : `light`, `dark`,
`system`. Variables CSS (`--bg`, `--fg`…) exposées comme utilitaires Tailwind (`bg-app`,
`text-muted`, `border-app`).

### Flux intelligents côté client

Dexie v3 : index multiEntry `*smart_feed_ids` sur le champ serveur, table `smartFeeds`. La vue
`/smart/:id` et le widget lisent `where('smart_feed_ids').equals(id)` puis filtrent et trient en
mémoire (pas d'index composé possible avec multiEntry ; borné par les correspondances).
`useUnreadCountsFull` compte les non-lus par flux et par flux intelligent en une passe.
`features/smartfeeds/SmartFeedDialog.tsx` : éditeur avec aperçu serveur (debounce 500 ms) ; à la
sauvegarde, `syncNow()` rapatrie les `smart_feed_ids` mis à jour.

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
| SSRF via une URL de flux ou d'article | Résolution DNS puis refus des IP privées, loopback, link-local ; seuls http/https ; **revalidé à chaque redirection** ; le service navigateur intercepte chaque requête de la page (`page.route`), **suit lui-même les redirections** avec `route.fetch(max_redirects=0)` en revalidant chaque destination (Chromium suivrait sinon un 302 sans repasser par le filtre), refuse chaque sous-ressource vers un hôte privé ou interne, et vit sur un réseau Compose sans Postgres ; vérifié par `scripts/check-browser-redirect.py` | `fetcher/http.safe_get`, `browser/server.py`, `docker-compose.yml` |
| Traversée de chemin sur le repli SPA | Chemin résolu et vérifié sous le dossier statique | `main.py` |
| Serveur au goutte-à-goutte, corps géant | Délai global 60 s, lecture en flux bornée (10 Mo / 5 Mo), upload OPML ≤ 5 Mo | `fetcher/http.safe_get`, `feeds/router.py` |
| Secret d'exemple en production | Refus de démarrer (`validate_runtime_config`) | `config.py`, `main.py`, `worker.py` |
| Clickjacking, sniffing, scripts injectés | CSP `script-src 'self'` (script de thème externe), `X-Frame-Options`, `nosniff`, `Referrer-Policy` | `main.py` (`SecurityHeadersMiddleware`) |
| XSS par contenu de flux ou d'article | Nettoyage serveur (nh3, liste blanche de balises/attributs, iframes limités à YouTube/Vimeo/Dailymotion) et client (DOMPurify, liens `noopener`, images `no-referrer`) | `extract/clean.py`, `web/src/lib/markdown.ts` |
| Force brute sur le mot de passe | Argon2, 5 tentatives / min / IP + 20 / min global, exigence de 8 caractères | `auth/security.py`, `auth/router.py` |
| Session volée | JWT 7 j invalidés au changement de mot de passe (claim `pv`), `/refresh` interdit aux tokens d'appareil, révocation optionnelle des appareils | `auth/` |
| Fuite des clés LLM | Chiffrées au repos (Fernet), jamais renvoyées en clair (masquées `sk-a…1234`), non journalisées | `settings_store.py`, `settings_router.py` |
| Tokens mobiles | Opaques, 256 bits, stockés hachés, révocables | `auth/security.py`, `auth/deps.py` |
| Rendu de pages arbitraires | Chromium en utilisateur non privilégié, mémoire et processus plafonnés, réseau `render` sans Postgres, hôtes privés refusés, ressources lourdes bloquées | `browser/`, `docker-compose.yml` |

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
    smartfeeds/             rules (définition → SQL), service (evaluate/apply/preview/quotas), jobs (summarize_entry), router
  tests/                    pytest : unitaires (parse, clean, guard, opml, summarize)
                            et intégration test_api.py (nécessite DATABASE_URL)
browser/
  server.py                 FastAPI + Playwright : POST /render
scripts/
  build-apk.sh              build de l'APK dans un conteneur (Dockerfile.android)
  e2e-ui.py                 parcours Playwright de l'interface (liste, lecteur, mobile, hors-ligne)
  e2e-dashboard.py          parcours Playwright du tableau de bord (défaut, édition, drag, persistance, conflit)
  e2e-smartfeeds.py         parcours Playwright des flux intelligents (aperçu, vue, édition rétroactive, regex, widget, orphelin, hors-ligne)
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
    feeds/useUnreadCounts   non lus par flux et par flux intelligent, une passe, partagé Sidebar / dashboard
    smartfeeds/SmartFeedDialog  éditeur de flux intelligent avec aperçu
    auth/LoginPage          création du mot de passe, connexion, adresse serveur
    reader/ReaderModal      fenêtre de lecture centrée (disposition pleine largeur), précédent/suivant
    feeds/Sidebar           navigation, compteurs, badge de sync
    feeds/SubscriptionTree  sections flux intelligents + abonnements, glisser-déposer au clic long
    feeds/sidebarOrder      règles pures du réordonnancement (brouillon, déplacement, diff → reorder)
    feeds/FeedIcon          icône de flux et compteur, partagés
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

### Tester (harnais E2E)

Les parcours `scripts/e2e-*.py` partagent `scripts/e2e_common.py` : `api()`, `login()`,
`new_context()` (viewport, locale, `bypass_csp`), `watch_errors()` et `main(run, results, …)` qui gère
la capture d'échec, le code de sortie et la remise en état (`cleanup`). Lancement :
`docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-<nom>.py`.

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
- **Pas de rétention côté serveur ni de suppression propagée** : la table `entries` croît ; la sync
  ne supprime localement que les entrées dont le flux a disparu (`deleted_entry_ids` réservé, vide).
- **DNS rebinding** : le contrôle anti-SSRF et la connexion httpx sont deux résolutions distinctes.
- **Recherche hors-ligne limitée** aux titres et extraits ; le plein texte est côté serveur.
- **Un seul utilisateur** par instance, par conception.
- Pistes : digest e-mail des flux intelligents (les correspondances et `summaries.origin/smart_feed_id`
  sont prêts), historique de lecture, notifications, sauvegarde automatisée de la base, réglage de
  l'intervalle par flux dans l'UI.
- Bilan détaillé et correctifs du 14/09/2026 : [AUDIT-2026-09-14.md](AUDIT-2026-09-14.md).
