# Journal des changements

Les versions suivent le [versionnage sémantique](https://semver.org/lang/fr/). Le numéro est celui de
`backend/fluxus/__init__.py` (renvoyé par `/api/health`) et de `web/package.json` ; chaque version
porte un tag git `vX.Y.Z`.

## 1.0.0 — 12 novembre 2026

Première version stable : état du projet après les quatre lots de l'audit du 12 novembre 2026
(`docs/AUDIT-2026-11-12.md`).

### Sécurité
- Renderer : Chromium à jour (Playwright 1.65.1), sortie réseau par une passerelle qui valide chaque
  adresse (popups, WebSocket et changement de résolution DNS compris), relance après un crash.
- Connexion : le limiteur de tentatives tient face à des requêtes simultanées.
- Le port de l'API n'est publié que sur `127.0.0.1` par défaut (`FLUXUS_HTTP_BIND`).
- Dépendances avec avis de sécurité mises à jour ; plage 100.64.0.0/10 refusée par la garde SSRF.

### Fiabilité
- Jobs : délai par type, plafond de tentatives à la reprise, résultat d'une exécution dépassée ignoré.
- Synchronisation : plus de fenêtre de perte au pull.
- Un flux avec un `ETag` démesuré ou un caractère NUL n'est plus rejeté en bloc.

### Lecture
- « Tout marquer comme lu » ne marque que les articles connus de l'appareil.
- Un article ancien ne disparaît plus du lecteur pendant sa lecture.
- La connexion aboutit après un changement d'adresse du serveur (application Android).

### Exploitation
- Sauvegarde planifiée : service `backup`, une archive par jour, sept conservées.
- Alerte d'espace disque sur le tableau de bord et dans les paramètres.
- Intégration continue (`.github/workflows/ci.yml`), tests des règles pures du front, lint bloquant,
  version de `uv` figée dans l'image.

### À savoir en mettant à jour
- Sans reverse proxy sur la machine, ajouter `FLUXUS_HTTP_BIND=0.0.0.0` à `.env`.
- « Tout marquer comme lu » ne touche plus les non lus absents du cache de l'appareil.
