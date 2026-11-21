#!/usr/bin/env bash
# Runs inside CLIPFORGE's own WSL 2 Linux environment ("ClipForge"), as root, called by install.ps1.
#
#   provision.sh system                      Docker Engine + WSL settings (first install and updates)
#   provision.sh app SOURCE INBOX DISPLAY    copy CLIPFORGE from SOURCE (a /mnt/c/... folder) to /opt/clipforge,
#                                            point the watched-folder inbox at INBOX, then build and start it
#   provision.sh models                      download the speech and vision models (verified)
#   provision.sh start | stop | status       used by the Start menu shortcuts
#
# Every step is safe to run again: an update re-runs the same steps and keeps .env and all data.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/clipforge
say() { printf '>> %s\n' "$*"; }

wait_for_docker() {
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && return 0; sleep 1; done
  echo "Docker Engine did not start" >&2; return 1
}

case "${1:-}" in
  system)
    say "Configuring the Linux environment"
    cat > /etc/wsl.conf <<'CONF'
# Written by the CLIPFORGE installer.
[boot]
systemd = true

[interop]
appendWindowsPath = false

[user]
default = root
CONF
    say "Updating Ubuntu packages"
    apt-get update -q
    apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
    apt-get install -y -q --no-install-recommends ca-certificates curl gnupg rsync
    if ! command -v docker >/dev/null 2>&1; then
      say "Installing Docker Engine (free, open source)"
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      . /etc/os-release
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -q
      apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable docker >/dev/null 2>&1 || true
    say "Docker $(docker --version | awk '{print $3}' | tr -d ,) ready"
    ;;

  app)
    SOURCE="${2:?source folder}"; INBOX="${3:?inbox folder}"; DISPLAY_PATH="${4:-}"
    systemctl start docker 2>/dev/null || (command -v dockerd >/dev/null && (dockerd >/var/log/dockerd.log 2>&1 &))
    wait_for_docker
    say "Copying CLIPFORGE into the Linux environment"
    mkdir -p "$APP"
    # .env (your secrets) and runtime folders are never overwritten by an update.
    rsync -a --delete --exclude '.env' --exclude 'inbox/' --exclude 'backups/' --exclude 'config/oauth-apps.json' \
      "$SOURCE"/ "$APP"/
    if [[ -f "$SOURCE/config/oauth-apps.json" ]]; then
      install -m 0640 "$SOURCE/config/oauth-apps.json" "$APP/config/oauth-apps.json"
    fi
    cd "$APP"
    if [[ ! -f .env ]]; then
      cp .env.example .env
      chmod 600 .env
      secret() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }
      sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" .env
      sed -i "s|^CLIPFORGE_MASTER_KEY=.*|CLIPFORGE_MASTER_KEY=$(secret)|" .env
    fi
    # Your Windows folder Videos\ClipForge is the inbox for watched folders.
    set_env() {  # set_env KEY VALUE: folder names may contain \ & | ' and spaces; all are kept as they are
      local escaped
      escaped="$(printf '%s' "$2" | sed -e 's/[\\&|]/\\&/g')"
      if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=${escaped}|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
    }
    set_env CLIPFORGE_INBOX_HOST "$INBOX"
    set_env CLIPFORGE_INBOX_DISPLAY "$DISPLAY_PATH"
    mkdir -p "$INBOX" 2>/dev/null || true
    say "Building CLIPFORGE (the first time downloads about 3 GB and takes 10-30 minutes)"
    ./scripts/install.sh --skip-speech-model
    ;;

  models)
    cd "$APP"
    wait_for_docker
    say "Downloading the vision models (about 15 MB, verified)"
    docker compose exec -T worker clipforge download-models
    if [[ "${2:-}" == "vision-only" ]]; then say "Speech recognition model skipped (download it later from Settings)"; exit 0; fi
    say "Downloading the speech recognition model (about 1.8 GB, every file verified; progress below)"
    docker compose exec -T worker clipforge download-models --vosk
    ;;

  start)
    cd "$APP"
    systemctl start docker 2>/dev/null || true
    wait_for_docker
    docker compose up -d
    ;;

  stop)
    cd "$APP"
    docker compose stop
    ;;

  status)
    cd "$APP"
    docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
    ;;

  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
