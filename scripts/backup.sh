#!/bin/sh
# Sauvegarde planifiée de la base (service `backup` de docker-compose.yml, image postgres).
# Une archive `fluxus-AAAAMMJJ-HHMMSS.sql.gz` par intervalle dans /backups, les plus anciennes sont
# supprimées. Connexion par les variables PG* ; restauration : voir docs/INSTALL.md, « Backups ».
set -eu
set -o pipefail

DIR=/backups
KEEP="${BACKUP_KEEP:-7}"
INTERVAL_H="${BACKUP_INTERVAL_HOURS:-24}"
# Marge exigée avant d'écrire : la sauvegarde ne doit pas finir de remplir le disque de la base.
MIN_FREE_KB="${BACKUP_MIN_FREE_MB:-1024}"
MIN_FREE_KB=$((MIN_FREE_KB * 1024))

log() { echo "$(date -u +%FT%TZ) sauvegarde : $*"; }

latest() { ls -1 "$DIR"/fluxus-*.sql.gz 2>/dev/null | sort | tail -n 1; }

due() {
  last="$(latest)"
  [ -z "$last" ] && return 0
  age=$(( $(date +%s) - $(stat -c %Y "$last") ))
  [ "$age" -ge $((INTERVAL_H * 3600)) ]
}

run() {
  free_kb=$(df -Pk "$DIR" | awk 'NR==2 {print $4}')
  if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
    log "ERREUR, espace insuffisant ($((free_kb / 1024)) Mo libres) : sauvegarde non faite"
    return 1
  fi
  name="fluxus-$(date -u +%Y%m%d-%H%M%S).sql.gz"
  tmp="$DIR/.$name.partiel"
  if pg_dump --no-owner | gzip > "$tmp" && gzip -t "$tmp"; then
    chmod 600 "$tmp"
    mv "$tmp" "$DIR/$name"
    log "$name écrite ($(du -h "$DIR/$name" | cut -f1))"
  else
    rm -f "$tmp"
    log "ERREUR, pg_dump a échoué : aucune archive écrite"
    return 1
  fi
  # Rotation : seulement après une sauvegarde réussie, pour ne jamais rester sans archive.
  ls -1 "$DIR"/fluxus-*.sql.gz | sort | head -n -"$KEEP" | while read -r old; do
    rm -f "$old"
    log "$(basename "$old") supprimée (rotation, $KEEP conservées)"
  done
}

mkdir -p "$DIR"
rm -f "$DIR"/.fluxus-*.partiel
if [ "${1:-}" = "now" ]; then
  run
  exit $?
fi
log "toutes les $INTERVAL_H h, $KEEP archives conservées"
while true; do
  if due; then run || true; fi
  sleep 900
done
