#!/usr/bin/env bash
# Construit l'APK Android dans un conteneur (aucun SDK Android requis sur la machine).
#
# Usage :
#   scripts/build-apk.sh            -> APK de debug, installable directement (adb install)
#   scripts/build-apk.sh release    -> APK release, signée si une clé est fournie, sinon non signée
#
# Signature (release) : FLUXUS_KEYSTORE_FILE=/chemin/cle.jks FLUXUS_KEYSTORE_PASSWORD=… FLUXUS_KEY_ALIAS=…
# [FLUXUS_KEY_PASSWORD=…]. Android n'installe une mise à jour que si elle porte la même clé : la
# garder en lieu sûr. La CI utilise la même clé (secrets du dépôt, voir docs/MOBILE.md).
#
# Le build web (web/dist) doit être à jour : `cd web && npm run build && npx cap sync android`.
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-debug}"
IMAGE="fluxus-android-builder"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "→ Construction de l'image de build Android (une seule fois, plusieurs minutes)…"
  docker build -t "$IMAGE" -f scripts/Dockerfile.android scripts
fi

echo "→ Build web + sync Capacitor…"
(cd web && npm run build >/dev/null && npx cap sync android >/dev/null)

echo "→ Gradle ($MODE)…"
TASK="assembleDebug"
[ "$MODE" = "release" ] && TASK="assembleRelease"
SIGNING=()
if [ "$MODE" = "release" ] && [ -n "${FLUXUS_KEYSTORE_FILE:-}" ]; then
  SIGNING=(
    -v "$(realpath "$FLUXUS_KEYSTORE_FILE"):/keys/release.jks:ro"
    -e FLUXUS_KEYSTORE_FILE=/keys/release.jks
    -e FLUXUS_KEYSTORE_PASSWORD -e FLUXUS_KEY_ALIAS -e FLUXUS_KEY_PASSWORD
  )
fi
docker run --rm \
  -v "$PWD/web:/web" \
  -v fluxus-gradle-cache:/root/.gradle \
  "${SIGNING[@]}" \
  -w /web/android \
  "$IMAGE" \
  bash -c '
    # Java ignore HTTP(S)_PROXY : on traduit un éventuel proxy d environnement en propriétés système.
    if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]; then
      P="${HTTPS_PROXY:-$https_proxy}"; P="${P#*://}"; P="${P%/}"
      export JAVA_TOOL_OPTIONS="-Dhttps.proxyHost=${P%:*} -Dhttps.proxyPort=${P##*:} -Dhttp.proxyHost=${P%:*} -Dhttp.proxyPort=${P##*:}"
    fi
    chmod +x gradlew && ./gradlew --no-daemon -q '"$TASK"

# Les fichiers créés par Gradle appartiennent à root : on les rend à l'utilisateur courant.
docker run --rm -v "$PWD/web/android:/p" alpine chown -R "$(id -u):$(id -g)" /p

OUT_DIR="web/android/app/build/outputs/apk/$MODE"
mkdir -p dist
cp "$OUT_DIR"/*.apk dist/
echo "✓ APK disponible dans dist/ :"
ls -la dist/*.apk
