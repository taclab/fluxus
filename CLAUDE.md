# Fluxus — repères pour travailler sur ce dépôt

Lecteur RSS auto-hébergé mono-utilisateur : FastAPI + PostgreSQL + worker de jobs (backend/),
front React offline-first (web/), micro-service Playwright (browser/), APK Capacitor.
Lire `docs/ARCHITECTURE.md` avant toute modification structurelle ; `README.md` pour l'usage.

## Commandes

```bash
docker compose up -d --build                 # pile complète sur :8000
cd backend && uv run ruff check fluxus tests && uv run pytest --ignore=tests/test_api.py
DATABASE_URL=postgresql+asyncpg://fluxus:<mdp>@localhost:5433/fluxus_test uv run pytest tests/test_api.py
cd web && npm run typecheck && npm run build   # Node 22 (nvm use 22), .npmrc gère les peer deps
npm run gen:api                                # après tout changement de route ou de schéma Python
```

## Règles du projet

- Langue de l'interface, des messages d'erreur `detail` et des commentaires : français.
- Toute modification d'une entrée visible par un client met `entry.updated_at = now` (pivot de la sync).
- Ordre pour un changement d'API : Python → `npm run gen:api` → front. Ne jamais redéclarer les types d'API côté front.
- Les résumés LLM ne sont jamais déclenchés automatiquement.
- Nouveau réglage : `settings_store.DEFAULTS` (+ `SECRET_KEYS` si secret) puis `SettingsPage`.
- Nouveau job : handler `@job_handler`, importé dans `handlers.py`, idempotent.
- Migration : `uv run alembic revision --autogenerate`, relire le fichier (triggers et index partiels non détectés).

## Pièges connus sur la machine de développement

- Docker injecte un proxy d'entreprise dans les conteneurs : trafic interne en `NO_PROXY`, healthcheck avec `--noproxy '*'`, httpx `trust_env=False` vers `browser`, Gradle via `JAVA_TOOL_OPTIONS`.
- Port 5432 déjà pris : Postgres de dev sur 5433.
- Pas de navigateur pilotable dans les sessions : tests d'interface via `scripts/e2e-ui.py` dans l'image `fluxus-browser` (`docker run --network host`).
