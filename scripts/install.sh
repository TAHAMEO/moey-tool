#!/usr/bin/env bash
# CLIPFORGE AI — first-time installation with Docker Compose.
#
#   ./scripts/install.sh                  install and start on http://localhost:3000
#   ./scripts/install.sh --domain clips.example.com
#                                         install with automatic HTTPS for that domain
#   ./scripts/install.sh --skip-speech-model
#                                         don't download the 1.8 GB speech model now
#   ./scripts/install.sh --no-build       use images that are already built or pulled
#   ./scripts/install.sh --build-ca FILE  trust FILE (a TLS-inspecting proxy's CA) while building
#
# Safe to re-run: an existing .env (and its secrets) is never overwritten.
set -euo pipefail

cd "$(dirname "$0")/.."
DOMAIN=""
SPEECH_MODEL=1
BUILD=1
EXTRA_CA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --skip-speech-model) SPEECH_MODEL=0; shift ;;
    --no-build) BUILD=0; shift ;;
    --build-ca) EXTRA_CA="$2"; shift 2 ;;   # CA of a TLS-inspecting proxy, used only while building
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "Docker is required: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (docker compose …)."
docker info >/dev/null 2>&1 || fail "Cannot talk to the Docker daemon (is it running, and may this user use it?)."

random_secret() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

if [[ ! -f .env ]]; then
  say "Creating .env with fresh secrets"
  cp .env.example .env
  chmod 600 .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(random_secret)|" .env
  sed -i "s|^CLIPFORGE_MASTER_KEY=.*|CLIPFORGE_MASTER_KEY=$(random_secret)|" .env
  if [[ -n "$DOMAIN" ]]; then
    sed -i "s|^CLIPFORGE_PUBLIC_BASE_URL=.*|CLIPFORGE_PUBLIC_BASE_URL=https://$DOMAIN|" .env
    sed -i "s|^CLIPFORGE_SITE_ADDRESS=.*|CLIPFORGE_SITE_ADDRESS=$DOMAIN|" .env
    sed -i "s|^CLIPFORGE_HTTP_PORT=.*|CLIPFORGE_HTTP_PORT=80|" .env
    sed -i "s|^CLIPFORGE_HTTPS_PORT=.*|CLIPFORGE_HTTPS_PORT=443|" .env
    sed -i "s|^CLIPFORGE_LIVE_PUBLIC_HOST=.*|CLIPFORGE_LIVE_PUBLIC_HOST=$DOMAIN|" .env
  fi
  echo
  echo "  Your master encryption key is in .env (CLIPFORGE_MASTER_KEY)."
  echo "  Store a copy somewhere safe: stored API keys and tokens can't be recovered without it."
  echo
else
  say "Keeping the existing .env"
fi

if [[ -n "$EXTRA_CA" ]]; then
  [[ -f "$EXTRA_CA" ]] || fail "CA file not found: $EXTRA_CA"
  export CLIPFORGE_BUILD_CA_FILE="$(cd "$(dirname "$EXTRA_CA")" && pwd)/$(basename "$EXTRA_CA")"
fi

if [[ "$BUILD" == 1 ]]; then
  say "Building images (the first build takes several minutes)"
  docker compose build
fi

say "Starting CLIPFORGE"
docker compose up -d
printf '  waiting for the API'
for _ in $(seq 1 90); do
  if docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/health/ready >/dev/null 2>&1; then
    echo " — ready"; break
  fi
  printf '.'; sleep 2
done
docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/health/ready >/dev/null 2>&1 || \
  fail "The API did not become ready. Inspect: docker compose logs migrate api"

say "Downloading the local vision/audio models (about 15 MB, verified)"
docker compose exec -T worker clipforge download-models || true
if [[ "$SPEECH_MODEL" == 1 ]]; then
  say "Downloading the speech recognition model (about 1.8 GB, verified file by file)"
  docker compose exec -T worker clipforge download-models --vosk || \
    echo "  Speech model download failed — retry later with: docker compose exec worker clipforge download-models --vosk"
fi

say "Checking the installation"
docker compose exec -T api clipforge doctor || true

URL=$(grep '^CLIPFORGE_PUBLIC_BASE_URL=' .env | cut -d= -f2-)
echo
say "Done. Open ${URL:-http://localhost:3000} and create the owner account."
echo "  Next steps: connect an AI service (AI Connections), and YouTube / Instagram (Settings → Accounts)."
echo "  Guides: docs/USER_GUIDE.md, docs/API_SETUP.md, docs/DEPLOYMENT.md"
