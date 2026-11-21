# Installation

Two supported ways: **Docker Compose** (recommended, one command) or a **bare-metal** install on
Ubuntu 24.04 with systemd. Both run the same code.

## Requirements

| | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores (x86-64) | 8 cores; an NVIDIA GPU only if you add WhisperX or local LLMs |
| Memory | 8 GB | 16 GB (each media worker keeps ~5.5 GB once the speech model is loaded) |
| Disk | 30 GB + your media | SSD; long sources need ~2× their size while processing |
| OS | Linux with Docker Engine 24+ and Compose v2 | Ubuntu 24.04 LTS |

Outbound access needed: your AI providers, `*.googleapis.com` / `oauth2.googleapis.com`,
`graph.instagram.com` / `rupload.facebook.com` (publishing), `storage.googleapis.com` (vision/audio
models, first run), `alphacephei.com` (speech model, first run), and — for public video
downloads — `www.youtube.com`, `*.googlevideo.com`, `www.instagram.com` and `*.cdninstagram.com`.

## Docker Compose

```bash
./scripts/install.sh
```

What it does:

1. creates `.env` from `.env.example` with a random database password and master key
   (an existing `.env` is never overwritten);
2. builds the images and starts the stack: PostgreSQL + pgvector, Valkey (Redis-compatible), a one-shot migration,
   the API, the main worker, the media worker, the web app and Caddy;
3. waits until the API reports ready;
4. downloads and verifies the models: four MediaPipe models (~15 MB, SHA-256 pinned) and the
   English speech model (~1.8 GB download, every file checked against a pinned manifest);
5. runs `clipforge doctor` and prints the address.

Options:

| Option | |
|---|---|
| `--domain clips.example.com` | public install with automatic HTTPS (Let's Encrypt). Point the domain's DNS at the server and open ports 80 and 443 first |
| `--skip-speech-model` | install the speech model later (`docker compose exec worker clipforge download-models --vosk`) |
| `--no-build` | use images you built or pulled already |
| `--build-ca FILE` | trust a TLS-inspecting proxy's CA while building (never copied into the images) |

Then open the address, create the owner account and follow the setup checklist on the dashboard.

### Models without internet access on the server

Download `vosk-model-en-us-0.22.zip` from alphacephei.com on another machine, copy it to the
server and install it — it is still verified file by file:

```bash
docker compose cp vosk-model-en-us-0.22.zip worker:/tmp/model.zip
```

```bash
docker compose exec worker clipforge download-models --vosk-from /tmp/model.zip
```

### Optional engines

- **faster-whisper** (multilingual, punctuated; CPU or GPU):
  `docker compose exec worker clipforge download-models --faster-whisper` downloads the model set in
  `CLIPFORGE_FASTER_WHISPER_MODEL` (default `small`) from Hugging Face.
- **WhisperX** (most accurate, word-aligned; GPU recommended) runs in its own virtual environment
  with PyTorch, outside the Docker images — see [workers/whisperx/requirements.in](../workers/whisperx/requirements.in).
  Set `CLIPFORGE_WHISPERX_PYTHON` to that environment's interpreter.
- **Local language models**: `docker compose --profile local-ai up -d` starts Ollama; see
  [API_SETUP.md](API_SETUP.md#3-ai-providers).

CLIPFORGE picks the best available engine automatically: WhisperX → faster-whisper → Vosk.

## Windows

Use the installer: **ClipForge-Setup-&lt;version&gt;.exe** turns on WSL 2 if needed, creates
CLIPFORGE's own Linux environment with the free Docker Engine (no Docker Desktop), builds
CLIPFORGE, downloads the speech model and adds Start menu shortcuts — with a progress window, and
continuing by itself if Windows has to restart. Everything is in **[WINDOWS.md](WINDOWS.md)**.

Prefer Docker Desktop? It works too: with its WSL 2 engine and the Ubuntu integration switched
on, clone this repository inside Ubuntu (`~/clipforge`, not a `C:\` folder) and run
`bash scripts/install.sh`. Give WSL enough memory (`%USERPROFILE%\.wslconfig`: `[wsl2]`,
`memory=12GB`). Docker Desktop is free for personal use and small businesses only.

## Bare metal (Ubuntu 24.04, systemd)

```bash
sudo apt-get install -y python3.12-venv ffmpeg postgresql-16 postgresql-16-pgvector redis-server libegl1 libgles2 libgl1 nodejs npm caddy
```

1. Accounts and directories — the media worker runs as its own user without access to secrets:

   ```bash
   sudo groupadd --system clipforge && sudo useradd --system -g clipforge clipforge && sudo useradd --system -g clipforge clipforge-media
   ```

   ```bash
   sudo install -d -o clipforge -g clipforge -m 2770 /var/lib/clipforge && sudo install -d -m 750 /etc/clipforge
   ```

2. Database:

   ```bash
   sudo -u postgres psql -c "CREATE ROLE clipforge LOGIN PASSWORD 'choose-a-password'" -c "CREATE DATABASE clipforge OWNER clipforge"
   ```

   ```bash
   sudo -u postgres psql -d clipforge -c "CREATE EXTENSION IF NOT EXISTS vector"
   ```

3. Code (into `/opt/clipforge`) and dependencies with [uv](https://docs.astral.sh/uv/):

   ```bash
   cd /opt/clipforge/backend && uv sync --frozen --no-dev
   ```

   ```bash
   cd /opt/clipforge/frontend && npm ci && npm run build && cp -r .next/static .next/standalone/.next/static
   ```

4. Configuration — `/etc/clipforge/clipforge.env` (mode 600) for the API and main worker:

   ```
   CLIPFORGE_ENV=production
   CLIPFORGE_DATABASE_URL=postgresql+psycopg://clipforge:choose-a-password@127.0.0.1:5432/clipforge
   CLIPFORGE_REDIS_URL=redis://127.0.0.1:6379/0
   CLIPFORGE_DATA_DIR=/var/lib/clipforge
   CLIPFORGE_PUBLIC_BASE_URL=https://clips.example.com
   CLIPFORGE_MASTER_KEY=<output of: /opt/clipforge/backend/.venv/bin/clipforge gen-key>
   CLIPFORGE_TRUSTED_PROXY_HOPS=1
   ```

   and `/etc/clipforge/media.env` (group-readable) with the same values **except** the master key
   and any platform secrets.

5. Services and front door: copy `deploy/systemd/*.service` to `/etc/systemd/system/`, put your
   domain into `deploy/systemd/Caddyfile` and install it as `/etc/caddy/Caddyfile`, then

   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now clipforge-api clipforge-worker clipforge-media-worker clipforge-web && sudo systemctl reload caddy
   ```

6. Models and check:

   ```bash
   sudo -u clipforge env $(sudo cat /etc/clipforge/clipforge.env | xargs) /opt/clipforge/backend/.venv/bin/clipforge download-models --vosk
   ```

   ```bash
   sudo -u clipforge env $(sudo cat /etc/clipforge/clipforge.env | xargs) /opt/clipforge/backend/.venv/bin/clipforge doctor
   ```

## Upgrading

```bash
git pull && docker compose build && docker compose up -d
```

Migrations run automatically (the `migrate` service; `ExecStartPre` on bare metal). Take a backup
first: `./scripts/backup.sh`.

## Uninstalling

`docker compose down` stops everything and keeps your data. `docker compose down -v` **deletes**
the database, media and models volumes.
