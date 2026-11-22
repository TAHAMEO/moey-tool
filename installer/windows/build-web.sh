#!/usr/bin/env bash
# Builds CLIPFORGE's web app as a self-contained Node.js server for the Windows app:
#   installer/windows/build-web.sh OUT_DIR
# OUT_DIR then holds server.js (run with `node server.js`). It forwards /api to the local API
# on 127.0.0.1:38100 (fixed when built; see clipforge/desktop.py).
set -euo pipefail
OUT="${1:?output folder}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(cd "$(dirname "$0")" && pwd)/build/web-src"
rm -rf "$WORK" && mkdir -p "$WORK"
( cd "$ROOT/frontend" && git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - ) | tar -xf - -C "$WORK"
cd "$WORK"
npm ci --no-audit --no-fund --loglevel=error
CLIPFORGE_API_URL=http://127.0.0.1:38100 NEXT_TELEMETRY_DISABLED=1 npm run build
rm -rf "$OUT" && mkdir -p "$OUT/.next"
cp -a .next/standalone/. "$OUT/"
cp -a .next/static "$OUT/.next/static"
if [[ -d public ]]; then cp -a public "$OUT/public"; fi
# Linux-only optional image libraries are useless on Windows (images aren't resized server-side).
rm -rf "$OUT/node_modules/@img" "$OUT/node_modules/sharp"
echo "web app built into $OUT ($(du -sh "$OUT" | cut -f1))"
