#!/usr/bin/env bash
# Back up CLIPFORGE (database incl. memory vectors, memory history, settings) and copy the
# backup out of the Docker volume.
#
#   ./scripts/backup.sh                 → ./backups/<timestamp>-manual/
#   ./scripts/backup.sh /mnt/offsite    → /mnt/offsite/<timestamp>-manual/
#
# A daily backup also runs automatically inside the stack (last 14 kept in the volume).
# Rendered media files are not included: back up the clipforge-data volume (or use S3
# storage with versioning) for those. Keep CLIPFORGE_MASTER_KEY safe separately — stored
# API keys and OAuth tokens in the backup can only be decrypted with it.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:-./backups}"
mkdir -p "$DEST"

name=$(docker compose exec -T worker clipforge backup | grep -o '"name": *"[^"]*"' | cut -d'"' -f4)
[[ -n "$name" ]] || { echo "Backup failed" >&2; exit 1; }
docker compose cp "worker:/data/backups/$name" "$DEST/$name"
chmod -R go-rwx "$DEST/$name"
echo "Backup written to $DEST/$name"
ls -la "$DEST/$name"
