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
- Toute modification d'une entrée visible par un client met `entry.updated_at = now` (pivot de la sync). Toute écriture de `read`/`starred` pose aussi `read_changed_at`/`starred_changed_at` (horloges par champ, seule base de la résolution des conflits de sync ; jamais `updated_at`).
- Renderer (`browser/server.py`) : jamais `route.continue_()` ; les redirections sont suivies à la main dans `route_handler` avec revalidation de chaque saut (`scripts/check-browser-redirect.py` doit passer). `safe_get` : le corps est déjà décodé, les en-têtes de transport sont réécrits.
- Consommation LLM : toute génération journalise une ligne `LlmUsage` (réservée avant l'appel, soldée après) ; le budget des résumés automatiques se calcule sur ce journal, pas sur `summaries`.
- Ordre pour un changement d'API : Python → `npm run gen:api` → front. Ne jamais redéclarer les types d'API côté front.
- Aucun résumé LLM n'est déclenché sans action de l'utilisateur, à l'exception des résumés automatiques des flux intelligents (opt-in, quota par flux et budget quotidien).
- Nouveau réglage : `settings_store.DEFAULTS` (+ `SECRET_KEYS` si secret) puis `SettingsPage`.
- Nouveau job : handler `@job_handler`, importé dans `handlers.py`, idempotent.
- Nouveau widget de tableau de bord : `features/dashboard/layout.ts` (type, normalisation, titre) → `queries.ts` → `widgets/` → `DashboardGrid.WidgetSwitch` + `WidgetConfigDialog`.
- Toute requête Dexie d'un widget doit passer par un index de `db.ts` ; pas de scan de table dans un widget.
- Tout accès HTTP sortant passe par `fetcher/http.safe_get` (anti-SSRF après redirection, taille et délai bornés) ; jamais `client.get` directement.
- Tests E2E : lancer l'image `fluxus-browser` avec `-u 0` (elle tourne en `pwuser`) pour pouvoir écrire les captures.
- Flux intelligents : toute condition passe par `smartfeeds/rules.build_condition` (SQL, `coalesce` systématique) ; jamais de filtrage en JavaScript. Toute écriture sur `smart_feed_matches` bump `entries.updated_at` (horloge Python).
- Tests E2E Playwright : `bypass_csp=True` dans le contexte (la CSP interdit `eval`, utilisé par `wait_for_function`).
- Ordre de la barre latérale : `position` est la clé de tri partout (barre, `GET /feeds`, OPML) ; la racine mélange dossiers et flux sans dossier sur la même échelle ; un nouveau flux prend `min(position) − 1` de son conteneur (`feeds/router._first_position`), un nouveau dossier va en fin de racine, les flux d'un dossier supprimé héritent de sa position. Les règles de déplacement vivent dans `features/feeds/sidebarOrder.ts`, sans React ; pas de capteur clavier dnd-kit dans la barre (Entrée doit ouvrir la vue).
- Points d'étape et dette connue : `docs/AUDIT-2026-09-14.md`, `docs/AUDIT-2026-09-15.md` (17 constats, tous corrigés ; tableau « Suivi des correctifs »).
- Migration : `uv run alembic revision --autogenerate`, relire le fichier (triggers et index partiels non détectés).

## Pièges connus sur la machine de développement

- Docker injecte un proxy d'entreprise dans les conteneurs : trafic interne en `NO_PROXY`, healthcheck avec `--noproxy '*'`, httpx `trust_env=False` vers `browser`, Gradle via `JAVA_TOOL_OPTIONS`.
- Port 5432 déjà pris : Postgres de dev sur 5433.
- Pas de navigateur pilotable dans les sessions : tests d'interface via `scripts/e2e-ui.py` dans l'image `fluxus-browser` (`docker run --network host`).
