# Development

See [ARCHITECTURE.md](ARCHITECTURE.md) for the code map and data flows, and
[CLAUDE.md](../CLAUDE.md) for the project rules.

## Local setup

```bash
docker compose up -d postgres                       # database only, published on :5433

# Backend — Python 3.12 managed by uv
cd backend && uv sync --all-extras
export DATABASE_URL=postgresql+asyncpg://fluxus:<password>@localhost:5433/fluxus
export FLUXUS_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
uv run alembic upgrade head
uv run uvicorn fluxus.main:app --reload --port 8000
uv run python -m fluxus.worker                      # second terminal

# Front end — Node 22 (web/.nvmrc)
cd web && npm install
npm run dev                                          # http://localhost:5173, /api proxied to :8000
npm run gen:api                                      # regenerate src/api/schema.d.ts from OpenAPI
```

Interactive API docs are served on `/api/docs`.

New migration after changing `backend/fluxus/models.py`:
`uv run alembic revision --autogenerate -m "description"`, then review the generated file
(triggers and partial indexes are not detected).

## Quality

```bash
cd backend
uv run ruff check fluxus tests
DATABASE_URL=postgresql+asyncpg://fluxus:<password>@localhost:5433/fluxus_test uv run pytest   # full suite
uv run pytest                                                      # without a database, over half is skipped
cd ../web && npm run typecheck && npm run lint && npm test && npm run build
cd ../cloudflare/email-worker && node --test worker.test.mjs     # newsletter Email Worker
```

The Email Worker and the server sign incoming newsletters the same way: both test suites check the
same signature vector, change them together.

Lint is blocking (no warning tolerated). `npm test` (Vitest) covers the pure front-end rules:
sidebar order, layouts, dashboard layout, sync merge and retention.

## End-to-end tests

Playwright scripts run in the `fluxus-browser` image against a running stack:

```bash
# -u 0: the image runs unprivileged, screenshots must be writable in /out
docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out \
  fluxus-browser python /scripts/e2e-ui.py
```

One script per area: `scripts/e2e-*.py` (ui, dashboard, smartfeeds, layout, sidebar, subscriptions, reading, pwa, backup, i18n, newsletters).
`e2e-newsletters.py` generates a new Worker secret: run it on a disposable instance only.
Set `E2E_BASE` to target another instance (for example a disposable copy on :8001).

The README screenshots come from `scripts/readme-screenshots.py` (English, dark theme, 2880×1800).
Run it against a disposable demo instance with English-language feeds, never on personal data:
an empty database, folders Tech, Science, Design and Videos, a few feeds in each, and the worker
stopped once they are fetched so the content stays still. Write to a temporary directory
(`-v /tmp/shots:/out`), then copy `dashboard.jpg` and `reader.jpg` into `docs/screenshots/`.

## CI and releases

- `.github/workflows/ci.yml` runs on every push: ruff, backend tests with PostgreSQL, typecheck,
  lint, tests and build of the front end, image builds and the renderer isolation check
  (`scripts/check-browser-redirect.py`).
- `.github/workflows/release.yml` runs on a `vX.Y.Z` tag: it publishes the three images on
  ghcr.io, builds the signed APK and creates the GitHub release (see [MOBILE.md](MOBILE.md)).

To release: bump the version in `backend/fluxus/__init__.py`, `backend/pyproject.toml` (+ `uv lock`)
and `web/package.json` (+ `package-lock.json`), add a `CHANGELOG.md` entry, commit, then tag
`vX.Y.Z` and push the tag. The workflow refuses a tag that differs from `__version__`.
