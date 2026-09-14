# Fluxus

Lecteur de flux RSS auto-hébergé, façon Inoreader, avec :

- récupération de l'article complet (HTTP + repli navigateur pour les sites JavaScript) ;
- résumé LLM **à la demande** des articles et des vidéos YouTube (via leurs sous-titres) ;
- interface web trois volets, raccourcis clavier, mode sombre, recherche plein texte ;
- application Android (Capacitor) avec lecture **hors-ligne** et synchronisation des états lu / favori ;
- gestion des flux et catégories, import / export OPML.

Mono-utilisateur, quatre conteneurs Docker, aucune dépendance à un service tiers hormis l'API LLM de votre choix.

## Architecture

```
reverse proxy ──> api :8000  (FastAPI + front statique)
                    │
        ┌───────────┼───────────┐
     worker      postgres     browser
 (fetch, extraction,        (Playwright/Chromium,
  résumés, YouTube)          rendu des pages JS)
```

| Dossier     | Contenu                                                        |
|-------------|----------------------------------------------------------------|
| `backend/`  | API FastAPI, worker de jobs (table PostgreSQL), migrations Alembic |
| `web/`      | Front React + TypeScript + Vite, PWA, projet Capacitor Android |
| `browser/`  | Micro-service de rendu Chromium (`POST /render`)               |
| `scripts/`  | Build de l'APK dans un conteneur                               |

## Installation

Prérequis : Docker et Docker Compose.

```bash
git clone <ce dépôt> fluxus && cd fluxus
cp .env.example .env
# Éditer .env : mot de passe Postgres, FLUXUS_SECRET_KEY (32+ caractères aléatoires), ports.
docker compose up -d --build
```

L'interface est servie sur `http://<hôte>:8000` (port `FLUXUS_HTTP_PORT`). Raccordez-la à votre reverse proxy en HTTPS ; l'API ne gère pas TLS elle-même.

À la première ouverture, l'application demande de **créer le mot de passe**. Ensuite :

1. Ajoutez des flux avec le bouton « + » (URL d'un site, d'un flux ou d'une chaîne YouTube) ou importez un OPML depuis *Paramètres → Abonnements*.
2. Renseignez une clé API dans *Paramètres → Résumés* (Anthropic par défaut, modèle `claude-opus-5`, ou toute API compatible OpenAI).
3. Ouvrez un article et cliquez sur ✨ (ou tapez `r`) pour le résumer.

### Variables d'environnement

| Variable                | Rôle                                                        |
|-------------------------|-------------------------------------------------------------|
| `POSTGRES_*`, `DATABASE_URL` | Connexion à PostgreSQL                                 |
| `FLUXUS_SECRET_KEY`     | Signature des JWT et chiffrement des clés LLM stockées      |
| `FLUXUS_HTTP_PORT`      | Port publié pour l'API et le front (défaut 8000)            |
| `FLUXUS_PG_PORT`        | Port hôte de Postgres en développement (défaut 5433)        |
| `FLUXUS_BROWSER_URL`    | URL interne du service de rendu (défaut `http://browser:3000`) |

Les réglages fonctionnels (fournisseur LLM, modèle, langue, clé API) se font dans l'interface et sont stockés en base, clés chiffrées.

## Raccourcis clavier

| Touche | Action                    | Touche | Action                         |
|--------|---------------------------|--------|--------------------------------|
| `j` / `k` | article suivant / précédent | `f` | article complet (récupère ou bascule) |
| `m`    | lu / non lu               | `r`    | résumer                        |
| `s`    | favori                    | `u`    | filtre « non lus »             |
| `v`    | ouvrir l'original         | `/`    | rechercher                     |
| `Échap` | retour à la liste        | `?`    | aide                           |

## Application Android

Le front est empaqueté avec Capacitor. Aucun SDK Android n'est requis sur la machine : le build se fait dans un conteneur.

```bash
scripts/build-apk.sh            # APK de debug -> dist/app-debug.apk
scripts/build-apk.sh release    # APK release non signé
```

Installez l'APK, saisissez l'adresse HTTPS de votre serveur et le mot de passe : l'appli s'appaire avec un **token d'appareil** longue durée (révocable dans *Paramètres → Appareils*). Les articles des 30 derniers jours sont conservés localement avec leur contenu complet ; les actions lu / favori faites hors-ligne sont rejouées au retour du réseau.

Pour signer une release : `apksigner sign --ks ma-cle.jks dist/app-release-unsigned.apk`.

## Développement

```bash
# Base de données seule
docker compose up -d postgres

# Backend (Python 3.12 via uv)
cd backend && uv sync --all-extras
export DATABASE_URL=postgresql+asyncpg://fluxus:<mdp>@localhost:5433/fluxus FLUXUS_SECRET_KEY=dev
uv run alembic upgrade head
uv run uvicorn fluxus.main:app --reload --port 8000
uv run python -m fluxus.worker          # dans un autre terminal

# Front (Node 22, cf. web/.nvmrc)
cd web && npm install && npm run dev    # http://localhost:5173, proxy /api -> :8000
npm run gen:api                         # régénère src/api/schema.d.ts depuis l'OpenAPI
```

Tests :

```bash
cd backend
uv run pytest tests/ --ignore=tests/test_api.py                          # unitaires
DATABASE_URL=postgresql+asyncpg://fluxus:<mdp>@localhost:5433/fluxus_test uv run pytest tests/test_api.py  # intégration
uv run ruff check fluxus tests
cd ../web && npm run typecheck
```

Nouvelle migration après modification des modèles : `uv run alembic revision --autogenerate -m "description"`.

## Sécurité

- Le fetcher refuse les URL pointant vers le réseau local (anti-SSRF).
- Mot de passe hashé avec Argon2, limitation des tentatives de connexion.
- Le HTML des articles est nettoyé côté serveur (nh3) et côté client (DOMPurify) ; seuls les iframes YouTube / Vimeo / Dailymotion sont conservés.
- Les clés LLM sont chiffrées au repos et jamais renvoyées en clair par l'API.

## Licence

À définir.
