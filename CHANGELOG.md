# Journal des changements

Les versions suivent le [versionnage sémantique](https://semver.org/lang/fr/). Le numéro est celui de
`backend/fluxus/__init__.py` (renvoyé par `/api/health`) et de `web/package.json` ; chaque version
porte un tag git `vX.Y.Z`.

## 1.0.4 — 19 novembre 2026

### Application Android
- L'application ne passe plus sous la barre d'état ni sous la barre de navigation (affichage bord à
  bord d'Android 15+, signalé sur Galaxy S25) : le contenu, les fenêtres, le tiroir de la barre
  latérale et les messages respectent les zones sûres, aussi dans la PWA.
- Les icônes de la barre d'état suivent le thème clair ou sombre.

## 1.0.3 — 19 novembre 2026

### Corrections
- Images des articles et icônes des flux qui disparaissaient dans le navigateur après la première
  visite : le service worker recevait la CSP de la page (`connect-src 'self'`) et ne pouvait pas
  télécharger les images d'autres sites qu'il met en cache. `sw.js` a désormais sa propre CSP.
- Chaque version remplace le service worker installé (numéro de version dans le bundle, affiché dans
  *Paramètres → Session*), même quand seul le serveur change.

### Projet
- README en anglais avec captures d'écran, documentation détaillée dans `docs/`, licence MIT.

## 1.0.2 — 12 novembre 2026

### Application Android
- APK signée construite par la CI et jointe à la release GitHub de chaque version.
- La version de l'application suit celle du projet. Passage d'une APK de debug à cette APK signée :
  désinstaller l'ancienne une fois (la clé de signature change).

### Corrections
- Écritures refusées après un import de sauvegarde tant que l'appareil ne s'est pas resynchronisé.
- Résumés : consommation journalisée avant l'appel, une seule génération à la fois par article,
  estimation du budget plus juste (textes très longs, écritures non latines).
- Horodatages `[mm:ss]` des résumés de vidéos (le lien pointait vers une position invalide).
- Échap ne ferme que la fenêtre du dessus ; flèches dans la fenêtre de lecture ; aide des raccourcis
  dans une fenêtre ; confirmation avant une déconnexion qui perdrait des changements.
- Thème, typographie et dossiers repliés synchronisés entre onglets.
- Import OPML : nouveau dossier en fin de liste ; adresse d'un flux modifié vérifiée ; changement de
  mot de passe limité en tentatives.

## 1.0.1 — 12 novembre 2026

### Exploitation
- Images publiées sur `ghcr.io` à chaque version (`fluxus`, `fluxus-browser`, `fluxus-backup`).
- `docker-compose.deploy.yml` : déploiement sans les sources, avec ce fichier et un `.env`.
- Le service `backup` a sa propre image ; le script n'est plus monté depuis le dépôt.

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
