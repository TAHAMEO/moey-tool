#!/usr/bin/env bash
# Builds the Windows installer (ClipForge-Setup-<version>.exe) with NSIS 3 — on Linux:
#   sudo apt-get install -y nsis nodejs npm     then     installer/windows/build.sh [version]
# The payload is CLIPFORGE's backend, the Windows scripts and docs (files tracked by git: no local
# .env, data or build output), the web app built as a standalone Node.js server, plus
# config/oauth-apps.json if you created one to build one-click Connect in (docs/ONE_CLICK_CONNECT.md).
# Python, PostgreSQL, FFmpeg and Node.js are not packed in: setup downloads them, pinned by checksum.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
VERSION="${1:-0.3.0}"
BUILD="$PWD/build"
PAYLOAD="$BUILD/payload"
rm -rf "$PAYLOAD"
mkdir -p "$PAYLOAD"
( cd "$ROOT" && git ls-files -z --cached --others --exclude-standard -- backend installer/windows/scripts docs config \
    README.md LICENSE* THIRD_PARTY_NOTICES* | grep -zv '^backend/tests/' | tar --null -T - -cf - ) | tar -xf - -C "$PAYLOAD"
if [[ -f "$ROOT/config/oauth-apps.json" ]]; then
  echo "Including config/oauth-apps.json (one-click Connect for everyone using this build)"
  cp "$ROOT/config/oauth-apps.json" "$PAYLOAD/config/oauth-apps.json"
fi
"$PWD/build-web.sh" "$PAYLOAD/web"
command -v makensis >/dev/null || { echo "makensis not found: sudo apt-get install -y nsis" >&2; exit 1; }
makensis -V3 -INPUTCHARSET UTF8 -DVERSION="$VERSION" -DPAYLOAD="$PAYLOAD" -DOUTFILE="$BUILD/ClipForge-Setup-$VERSION.exe" clipforge.nsi
ls -la "$BUILD/ClipForge-Setup-$VERSION.exe"
sha256sum "$BUILD/ClipForge-Setup-$VERSION.exe" | tee "$BUILD/ClipForge-Setup-$VERSION.exe.sha256"
