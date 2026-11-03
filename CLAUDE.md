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
- Confirmation : `useConfirm()` (`components/ui.tsx`), jamais `window.confirm` (le navigateur peut le bloquer en silence et renvoyer faux).
- Toute modification d'une entrée visible par un client met `entry.updated_at = now` (pivot de la sync). Toute écriture de `read`/`starred` pose aussi `read_changed_at`/`starred_changed_at` (horloges par champ, seule base de la résolution des conflits de sync ; jamais `updated_at`).
- Renderer (`browser/server.py`) : jamais `route.continue_()` ; les redirections sont suivies à la main dans `route_handler` avec revalidation de chaque saut (`scripts/check-browser-redirect.py` doit passer). `safe_get` : le corps est déjà décodé, les en-têtes de transport sont réécrits.
- Consommation LLM : toute génération journalise une ligne `LlmUsage` (réservée avant l'appel, soldée après) ; le budget des résumés automatiques se calcule sur ce journal, pas sur `summaries`.
- Ordre pour un changement d'API : Python → `npm run gen:api` → front. Ne jamais redéclarer les types d'API côté front.
- Aucun résumé LLM n'est déclenché sans action de l'utilisateur, à l'exception des résumés automatiques des flux intelligents et des flux « tout résumer » (opt-in, quota par flux et budget quotidien). Budget, réservation et mise en file : `summarize/auto.py`, rien de recopié.
- Nouveau réglage : `settings_store.DEFAULTS` (+ `SECRET_KEYS` si secret) puis `SettingsPage`.
- Nouveau job : handler `@job_handler`, importé dans `handlers.py`, idempotent.
- Nouveau widget de tableau de bord : `features/dashboard/layout.ts` (type, normalisation, titre) → `queries.ts` → `widgets/` → `DashboardGrid.WidgetSwitch` + `WidgetConfigDialog`.
- Toute requête Dexie d'un widget doit passer par un index de `db.ts` ; pas de scan de table dans un widget. Pour lister ou compter au-delà d'une page, clés d'abord, articles ensuite (`db/entryQueries.ts`) : un article local porte tout son HTML. Compteurs de non lus : `useUnreadCountsFull` / `useUnreadCount`, jamais une nouvelle requête.
- Tout accès HTTP sortant passe par `fetcher/http.safe_get` (anti-SSRF après redirection, taille et délai bornés) ; jamais `client.get` directement.
- Tests E2E : `docker run --rm --network host -u 0 -v $PWD/scripts:/scripts:ro -v /tmp/shots:/out fluxus-browser python /scripts/e2e-<nom>.py` ; tout script passe par `scripts/e2e_common.py` (connexion, `api()`, contexte avec `bypass_csp`, collecte des erreurs, `main(run, results, …)`), jamais de harnais recopié.
- Flux intelligents : toute condition passe par `smartfeeds/rules.build_condition` (SQL, `coalesce` systématique) ; jamais de filtrage en JavaScript. Toute écriture sur `smart_feed_matches` bump `entries.updated_at` (horloge Python).
- Présentation de liste : attribut serveur synchronisé (`view_mode` de `feeds`, `categories`, `smart_feeds` + réglage `default_view_mode`), résolu par `features/entries/viewMode.ts` (flux → dossier → défaut ; la disposition se déduit de la présentation) et écrit par `setViewMode` (`useViewMode.ts` : Dexie d'abord, file hors ligne). Les autres préférences d'affichage restent par appareil, jamais côté serveur : elles passent par `createDevicePref` (`app/devicePref.ts` : validation, hook, sync entre onglets). La fenêtre de lecture (`ReaderModal`) ne doit pas porter `data-dialog` (les raccourcis y restent actifs) ; les touches propres à un composant se déclarent dans ce composant via `useKeyboard`, pas dans la carte globale de `Layout`.
- Ordre de la barre latérale : `position` est la clé de tri partout (barre, `GET /feeds`, OPML) ; la racine mélange dossiers et flux sans dossier sur la même échelle ; un nouveau flux prend `min(position) − 1` de son conteneur (`feeds/router._first_position`), un nouveau dossier va en fin de racine, les flux d'un dossier supprimé héritent de sa position. Les règles de déplacement vivent dans `features/feeds/sidebarOrder.ts`, sans React ; pas de capteur clavier dnd-kit dans la barre (Entrée doit ouvrir la vue).
- Points d'étape et dette connue : `docs/AUDIT-2026-09-14.md`, `docs/AUDIT-2026-09-15.md` (17 constats, tous corrigés ; tableau « Suivi des correctifs »).
- Sauvegarde complète (`backup/service.py`) : toute nouvelle table de données utilisateur s'ajoute à `EXPORT_TABLES` (ordre des clés étrangères) et, si elle doit être vidée à l'import, à `CLEARED` ; une nouvelle colonne est exportée d'office. Jamais de secret, de mot de passe ni de réglage `_…` dans le fichier. Tout nouveau chemin d'écriture client rejouable (file hors ligne) envoie l'époque de sync (`sync.epoch`) et le serveur refuse en 409 une époque périmée.
- Migration : `uv run alembic revision --autogenerate`, relire le fichier (triggers et index partiels non détectés).

## Pièges connus sur la machine de développement

- Docker injecte un proxy d'entreprise dans les conteneurs : trafic interne en `NO_PROXY`, healthcheck avec `--noproxy '*'`, httpx `trust_env=False` vers `browser`, Gradle via `JAVA_TOOL_OPTIONS`.
- Port 5432 déjà pris : Postgres de dev sur 5433.
- Pas de navigateur pilotable dans les sessions : tests d'interface via `scripts/e2e-ui.py` dans l'image `fluxus-browser` (`docker run --network host`).
- L'instance de dev (:8000) n'a plus le mot de passe E2E : lancer les E2E sur une copie jetable (`pg_dump fluxus | psql fluxus_e2e`, suppression du réglage `_password_hash`, conteneur `fluxus-app` sur :8001 dans le réseau `fluxus_internal`, sans worker, `/api/auth/setup`, puis `-e E2E_BASE=http://localhost:8001`) et la supprimer ensuite.
