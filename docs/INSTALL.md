# Installation & operations

## Install

Requirements: Docker with the Compose plugin, and an HTTPS reverse proxy (Caddy, Traefik, Nginx…).

**From published images** (no sources needed, two files):

```bash
mkdir fluxus && cd fluxus
# Download docker-compose.deploy.yml and .env.example from the release page on GitHub
cp .env.example .env
$EDITOR .env          # Postgres password, FLUXUS_SECRET_KEY, ports
docker compose -f docker-compose.deploy.yml up -d
```

Images (`fluxus`, `fluxus-browser`, `fluxus-backup`) are published on `ghcr.io/taclab` for every
release. `FLUXUS_VERSION` in `.env` pins the deployed version (`1.0.2`, or `1` to follow fixes;
`latest` by default). If the images are private, log Docker in first: `docker login ghcr.io` with a
GitHub token that has `read:packages`.

Update: `docker compose -f docker-compose.deploy.yml pull && docker compose -f docker-compose.deploy.yml up -d`.

**From sources**:

```bash
git clone <this repository> fluxus && cd fluxus
cp .env.example .env
$EDITOR .env
docker compose up -d --build
```

The first build takes a few minutes (Node front end, Python dependencies, Chromium image).

Either way, the app answers on `http://127.0.0.1:8000` (`FLUXUS_HTTP_PORT`), from the host only;
`FLUXUS_HTTP_BIND=0.0.0.0` opens it to the network without a reverse proxy.

Put the port behind your reverse proxy over HTTPS; the API does not handle TLS. Set
`FORWARDED_ALLOW_IPS` to the proxy address so login rate limiting sees real client addresses
(default `*`, acceptable on a private Compose network; a global cap of 20 failures per minute always
applies).

Startup **refuses** a sample `FLUXUS_SECRET_KEY` or one shorter than 32 characters, and Compose
requires `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`.

Caddy example:

```
fluxus.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## First steps

1. Open the interface: it asks you to **create the password** (single account). The home page is
   the dashboard, which fills itself with one widget per folder.
2. Add feeds with **+** at the top of the sidebar: a site URL (feeds are discovered), an RSS/Atom
   feed or a YouTube channel. Or import OPML from *Paramètres → Abonnements*.
3. In *Paramètres → Résumés*, choose the LLM provider and enter the API key.
4. Open an article and click ✨ or press `r`: the summary streams in.

The worker refreshes feeds in the background (adaptive interval, 10 min to 24 h depending on feed
activity). The interface syncs on load, when brought back to the foreground and every 5 minutes;
↻ forces a sync.

## Configuration

### `.env`

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL account created on first start | `fluxus` / `change-me` / `fluxus` |
| `DATABASE_URL` | SQLAlchemy URL used by the API and worker (host `postgres` = Compose service) | |
| `FLUXUS_SECRET_KEY` | Signs JWTs and encrypts stored LLM keys. **At least 32 random characters, never change it afterwards** (stored keys would become unreadable). | |
| `FLUXUS_VERSION` | Image version deployed by `docker-compose.deploy.yml` | `latest` |
| `FLUXUS_HTTP_PORT` | Host port for the API and front end | `8000` |
| `FLUXUS_HTTP_BIND` | Interface for that port; `0.0.0.0` to expose it without a reverse proxy | `127.0.0.1` |
| `FLUXUS_PG_PORT` | Host port for Postgres, useful in development | `5433` |
| `FLUXUS_BROWSER_URL` | Internal URL of the rendering service | `http://browser:3000` |
| `FORWARDED_ALLOW_IPS` | Reverse proxy IP(s) allowed to set `X-Forwarded-For` | `*` |
| `FLUXUS_BACKUP_DIR`, `FLUXUS_BACKUP_KEEP`, `FLUXUS_BACKUP_INTERVAL_HOURS` | Daily backups (see below) | `./backups`, 7, 24 |

Optional `FLUXUS_` variables (see `backend/fluxus/config.py`): `ACCESS_TOKEN_HOURS` (web session
length, 168), `DEFAULT_FETCH_INTERVAL_MIN` (30), `MIN_FETCH_INTERVAL_MIN` (10),
`MAX_FETCH_INTERVAL_MIN` (240), `MAX_ERROR_BACKOFF_MIN` (1440), `WORKER_CONCURRENCY` (6),
`HTTP_TIMEOUT_SECONDS` (20), `LOG_LEVEL`.

### In-app settings

Stored in the database (`settings` table), edited in *Paramètres*:

| Section | Settings |
|---|---|
| Résumés | provider (`anthropic`, or `openai` and compatibles), API key (encrypted), model, reasoning effort, summary language, OpenAI base URL; dedicated model and daily budget for automatic summaries |
| Lecture | font pairing, text size, column width (per device) |
| Apparence | light / dark / auto theme, dashboard reset |
| Abonnements | OPML import / export, refresh all |
| Hors-ligne | retention horizon in days, sync, full resync |
| Appareils | list and revoke mobile app tokens |
| Sécurité | password change (signs out other web sessions; optionally mobile devices) |
| Sauvegarde et migration | full export / import (see the [user guide](USER-GUIDE.md#moving-to-another-instance)) |

### LLM provider

- **Anthropic** (default). The `low` effort is enough for a summary.
- **OpenAI**: the *URL de base* field accepts any compatible API (Mistral, Groq, a remote Ollama…).

Manual summaries only cost when you click. Cumulative token usage is shown in *Paramètres → Résumés*.

## Operations

```bash
docker compose ps                        # state of the four services
docker compose logs -f worker            # refreshes, feed errors
docker compose logs -f api               # HTTP requests
```

Schema migrations run automatically when the `api` container starts (`alembic upgrade head`).

### Backups

The database holds everything (feeds, articles, summaries, settings, password). The `backup`
service writes one `pg_dump` archive per day to `./backups` (`fluxus-YYYYMMDD-HHMMSS.sql.gz`,
readable by root only) and keeps the last seven. By default archives sit on the same disk as the
database: point `FLUXUS_BACKUP_DIR` at another disk to survive its loss. A backup is refused when
less than 1 GB is free.

```bash
docker compose logs backup               # last archive, errors
docker compose run --rm backup now       # immediate backup

# Restore into an empty database, services stopped
docker compose stop api worker backup
docker compose exec -T postgres dropdb -U fluxus fluxus
docker compose exec -T postgres createdb -U fluxus fluxus
gunzip -c backups/fluxus-20261112-030000.sql.gz | docker compose exec -T postgres psql -q -U fluxus -d fluxus
docker compose up -d
```

(With the published images, add `-f docker-compose.deploy.yml` to each command.)

### Disk and resources

Postgres stops when the disk is full. Free server space is shown in *Paramètres*; under 2 GB or 5 %
an alert also appears on the dashboard overview widget.

About 450 MB of RAM at rest for the whole stack (api and worker capped at 768 MB, `browser` at
1 GB). Logs rotate over 3 × 10 MB files per service. The browser runs on a separate Compose
network, without access to Postgres, and refuses to render any private address.

Full reset: `docker compose down -v` (deletes the Postgres volume).

## Troubleshooting

| Symptom | Hint |
|---|---|
| A feed has a red dot | Hover it: HTTP 403/404, invalid XML, domain gone. The worker retries with exponential backoff (up to 24 h). |
| « Aucune clé API configurée » | *Paramètres → Résumés*: enter the key, then save. |
| « Modèle inconnu » | The model does not match the provider (e.g. `claude-…` with OpenAI). |
| « Cette vidéo n'a pas de sous-titres » | Expected: no audio transcription. |
| A smart feed misses an accented word | Keywords are compared without accents or case; in regex mode, write the pattern without accents (`eleve` for « élève »). |
| « Expression régulière refusée : … » | PostgreSQL engine message: no Python named groups `(?P<…>)`, quantifiers ≤ 255, no backreferences. |
| Automatic summaries stopped | Smart feed quota or daily budget reached (see *Paramètres → Résumés*), or budget set to 0. |
| Full article empty or truncated | Paywalled or anti-bot site. Try « Réessayer via le navigateur »; otherwise set the feed to `none`. |
| New articles don't show up | ↻; if it persists, *Paramètres → Hors-ligne → Resynchroniser tout*. |
| « Cette disposition a été enregistrée par une version plus récente » | Another device runs a newer Fluxus: update this one; the dashboard stays readable meanwhile. |
| API refuses to start: « FLUXUS_SECRET_KEY doit être… » | The secret is a sample or too short. Generate 32+ random characters and never change it afterwards. |
| Signed out on every browser after a password change | Intended: old sessions are invalidated, the one that made the change stays open. |
| `api` container `unhealthy` while it answers | A Docker-injected HTTP proxy intercepts the local check; the healthcheck uses `--noproxy '*'` for that. Check `~/.docker/config.json`. |
| Gradle fails behind a proxy | `scripts/build-apk.sh` turns `HTTPS_PROXY` into Java properties; check the variable is visible in the container. |
