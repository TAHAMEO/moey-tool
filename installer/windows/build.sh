#!/usr/bin/env bash
# Builds the Windows installer (ClipForge-Setup-<version>.exe) with NSIS 3 — on Linux:
#   sudo apt-get install -y nsis     then     installer/windows/build.sh [version]
# The payload is exactly the files tracked by git (no local .env, data or build output), plus
# config/oauth-apps.json if you created one to build one-click Connect in (docs/ONE_CLICK_CONNECT.md).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
VERSION="${1:-$(grep -m1 '^version' "$ROOT/backend/pyproject.toml" | cut -d'"' -f2)}"
BUILD="$PWD/build"
rm -rf "$BUILD/payload"
mkdir -p "$BUILD/payload"
# Built installers (dist/) are never packed into a newer one.
( cd "$ROOT" && git ls-files -z --cached --others --exclude-standard | grep -zv '^dist/' | tar --null -T - -cf - ) \
  | tar -xf - -C "$BUILD/payload"
if [[ -f "$ROOT/config/oauth-apps.json" ]]; then
  echo "Including config/oauth-apps.json (one-click Connect for everyone using this build)"
  cp "$ROOT/config/oauth-apps.json" "$BUILD/payload/config/oauth-apps.json"
fi
# Linux scripts must keep LF line endings when they travel through Windows.
find "$BUILD/payload" -name '*.sh' -exec sed -i 's/\r$//' {} +
command -v makensis >/dev/null || { echo "makensis not found: sudo apt-get install -y nsis" >&2; exit 1; }
makensis -V3 -INPUTCHARSET UTF8 -DVERSION="$VERSION" -DPAYLOAD="$BUILD/payload" -DOUTFILE="$BUILD/ClipForge-Setup-$VERSION.exe" clipforge.nsi
ls -la "$BUILD/ClipForge-Setup-$VERSION.exe"
sha256sum "$BUILD/ClipForge-Setup-$VERSION.exe" | tee "$BUILD/ClipForge-Setup-$VERSION.exe.sha256"
