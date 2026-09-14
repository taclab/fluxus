#!/usr/bin/env bash
# Construit l'APK Android dans un conteneur (aucun SDK Android requis sur la machine).
#
# Usage :
#   scripts/build-apk.sh            -> APK de debug, installable directement (adb install)
#   scripts/build-apk.sh release    -> APK release non signé (à signer avec apksigner)
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
docker run --rm \
  -v "$PWD/web/android:/project" \
  -v fluxus-gradle-cache:/root/.gradle \
  -w /project \
  "$IMAGE" \
  bash -c "chmod +x gradlew && ./gradlew --no-daemon -q $TASK"

OUT_DIR="web/android/app/build/outputs/apk/$MODE"
mkdir -p dist
cp "$OUT_DIR"/*.apk dist/
echo "✓ APK disponible dans dist/ :"
ls -la dist/*.apk
