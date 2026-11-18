#!/usr/bin/env bash
# Restore CLIPFORGE from a backup folder created by scripts/backup.sh (or copied out of
# /data/backups). REPLACES the current database and memory history.
#
#   ./scripts/restore.sh ./backups/20261118T171041Z-manual
#
# The .env of the original installation must be in place: CLIPFORGE_MASTER_KEY decrypts
# the API keys and OAuth tokens contained in the backup.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:?usage: scripts/restore.sh <backup-folder>}"
[[ -f "$SRC/database.dump" && -f "$SRC/manifest.json" ]] || { echo "Not a CLIPFORGE backup: $SRC" >&2; exit 1; }

read -r -p "This replaces the current CLIPFORGE database with $SRC. Type RESTORE to continue: " answer
[[ "$answer" == "RESTORE" ]] || { echo "Cancelled."; exit 1; }

echo "▸ Stopping the application (database stays up)"
docker compose up -d db redis
docker compose stop proxy web api worker media-worker

echo "▸ Recreating the database from the dump"
docker compose exec -T db sh -c 'dropdb -U clipforge --maintenance-db=postgres --if-exists clipforge && createdb -U clipforge --maintenance-db=postgres clipforge'
docker compose exec -T db pg_restore -U clipforge -d clipforge --no-owner --exit-on-error < "$SRC/database.dump"

if [[ -f "$SRC/mem0-history.db" ]]; then
  echo "▸ Restoring memory history"
  docker compose cp "$SRC/mem0-history.db" worker:/data/mem0/history.db
  # docker cp writes files as root; the application runs as uid 10001.
  docker compose run --rm --no-deps -T --user 0 --entrypoint chown worker 10001:10001 /data/mem0/history.db
fi

echo "▸ Starting CLIPFORGE"
docker compose up -d
echo "Restored from $SRC. Check with: docker compose exec api clipforge doctor"
