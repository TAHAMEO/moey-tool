# Deployment and operations

## Architecture

```
            Internet
               │  80/443 (HTTPS via Let's Encrypt)        UDP 9000-9003 (optional: SRT live ingest)
          ┌────▼────┐                                              │
          │  Caddy  │── /api/* ──► api (FastAPI)                  │
          └────┬────┘                │                             │
               └── everything else ─► web (Next.js)               │
                                     │                             │
   PostgreSQL 16 + pgvector ◄────────┼──── worker (default queue) ◄┘  orchestration, publishing,
   (data, memory vectors,            │      holds the master key        analytics, backups, live ingest
    job queue, checkpoints)          │
   Valkey (wake-ups, throttling) ◄───┴──── media-worker (media queue)   decoding, transcription,
                                           NO secrets                   analysis, rendering
   volume clipforge-data (/data): media, models, backups, memory history
```

Only Caddy (and the SRT ports, if used) are published; the API, database and Valkey (Redis-compatible) are internal.

## Choosing a server

| Workload | Suggested size |
|---|---|
| A few videos a week, cloud AI models | 4 vCPU, 16 GB RAM, 80 GB SSD |
| Daily long videos or livestreams | 8 vCPU, 32 GB RAM, 200 GB SSD, 2 media workers |
| Local language models / WhisperX | add an NVIDIA GPU (8 GB+ VRAM) |

Rendering is CPU-bound (x264): in our tests a 30-second vertical clip with face tracking took about
43 s on 4 cores, and transcribing a 76-second source about 58 s. The media worker keeps the speech
model in memory (~5.5 GB).

## Public installation with HTTPS

1. Point a DNS record (e.g. `clips.example.com`) at the server.
2. Open TCP 80 and 443 (and UDP 9000-9003 only if you will use live ingest).
3. `./scripts/install.sh --domain clips.example.com`

Caddy obtains and renews the certificate automatically. Then complete the Google and Meta app
setup with the HTTPS redirect URIs ([API_SETUP.md](API_SETUP.md)).

Behind your own load balancer or proxy instead of the bundled Caddy: route `/api/*` to the API
and the rest to the web app, make sure the proxy *replaces* `X-Forwarded-For` with the client
address, and set `CLIPFORGE_TRUSTED_PROXY_HOPS` to the number of proxies in front of the API.

## Updates

```bash
./scripts/backup.sh && git pull && docker compose build && docker compose up -d
```

The `migrate` service applies database migrations before the API and workers start.

## Backups and restore

- **Automatic:** the worker takes a backup every 24 hours (PostgreSQL dump incl. memory vectors,
  memory export and history) into the data volume and keeps the newest 14.
- **Off the server:** `./scripts/backup.sh /path/to/offsite` runs a backup and copies it out of the
  volume. Schedule it, e.g. with cron:
  ```
  17 3 * * * cd /opt/clipforge && ./scripts/backup.sh /mnt/backups >> /var/log/clipforge-backup.log 2>&1
  ```
- **Restore:** `./scripts/restore.sh /path/to/backup-folder` (asks for confirmation, replaces the
  database and memory history, restarts). Verified: a restored instance matches the backup row
  for row, and settings changed after the backup are rolled back.
- **Not included:** rendered media and uploaded files. Snapshot the `clipforge_clipforge-data`
  volume with your provider's disk snapshots, or use S3 storage (below) with versioning.
- **The master key is not in any backup.** Keep `.env` (or at least `CLIPFORGE_MASTER_KEY`) in a
  password manager; a restore needs it to decrypt stored credentials.

## Monitoring

| Check | Use |
|---|---|
| `GET /api/health` | liveness (process up) |
| `GET /api/health/ready` | readiness: database and FFmpeg; also reports whether workers are alive. Point an uptime monitor at `https://<domain>/api/health/ready` |
| `GET /api/diagnostics` (admin) | workers' heartbeats and queues, Redis/Valkey, disk, models, transcription engines, permanent memory, platform app configuration |
| Dashboard → System health | the same, for admins in the app |
| `docker compose exec api clipforge doctor` | full installation check; exit code 1 when something essential is missing |
| `docker compose logs -f worker media-worker` | job progress and errors |

Every job keeps an event timeline (project page → *Advanced diagnostics*, with *Settings → Advanced →
Show technical details* on); failed jobs always show the error and can be retried from their last
checkpoint.

## Scaling

- More parallel processing on one server: `CLIPFORGE_MEDIA_WORKERS=2` in `.env`, then
  `docker compose up -d`.
- Several servers: run additional `media-worker` containers elsewhere pointing at the same
  PostgreSQL and Valkey, and switch storage to S3 (local volumes can't be shared between hosts):
  ```
  CLIPFORGE_STORAGE_BACKEND=s3
  CLIPFORGE_S3_BUCKET=…  CLIPFORGE_S3_REGION=…  CLIPFORGE_S3_ENDPOINT_URL=…   (any S3-compatible service)
  CLIPFORGE_S3_ACCESS_KEY_ID=…  CLIPFORGE_S3_SECRET_ACCESS_KEY=…
  ```
- Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so any number of workers can share a
  queue; a crashed worker's job is re-queued when its lease expires and resumes from its checkpoint.

## Watched folders and built-in sign-in apps

- **Inbox:** `CLIPFORGE_INBOX_HOST` (default `./inbox` next to `docker-compose.yml`) is mounted at
  `/inbox` in the API and both workers. Point it at a share your team copies videos into (an SMB/NFS
  mount works — folders are polled every minute, so no file-system events are needed). For the
  *move to Processed* option, the container user (uid 10001) needs write access to it.
- **One-click Connect for every user:** put `config/oauth-apps.json` on the server (see
  [ONE_CLICK_CONNECT.md](ONE_CLICK_CONNECT.md)) and use a *Web application* Google client with
  `https://<your domain>/api/connections/google/callback` registered; on HTTPS, Instagram needs no
  relay page.
- **Public downloads** need outbound access to `www.youtube.com`, `*.googlevideo.com`,
  `www.instagram.com` and `*.cdninstagram.com`. Some cloud providers' IP ranges are asked by YouTube
  to sign in ("confirm you're not a bot"); CLIPFORGE reports that rather than working around it.

## Live ingest

The main worker listens for SRT on UDP 9000-9003 (four concurrent sessions). Set
`CLIPFORGE_LIVE_PUBLIC_HOST` to the hostname your encoder uses. Each session has its own
encryption passphrase; RTMP is deliberately not offered because FFmpeg's RTMP listener does not
authenticate stream keys.

## Local AI models

`docker compose --profile local-ai up -d` adds Ollama. For an NVIDIA GPU install the NVIDIA
Container Toolkit and add to `docker-compose.override.yml`:

```yaml
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: all, capabilities: [gpu] }]
```

## Servers behind a TLS-inspecting proxy

Corporate networks that re-sign HTTPS need their CA trusted in two places:

- building images: `./scripts/install.sh --build-ca /path/to/proxy-ca.pem` (used only by the
  build stages, never copied into the images);
- at runtime: mount a CA bundle that contains the proxy CA *and* the public roots, and point
  CLIPFORGE at it, e.g. in `docker-compose.override.yml`:
  ```yaml
  services:
    api:          { volumes: ["/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/corp.pem:ro"] }
    worker:       { volumes: ["/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/corp.pem:ro"] }
    media-worker: { volumes: ["/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/corp.pem:ro"] }
    migrate:      { volumes: ["/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/corp.pem:ro"] }
  ```
  with `CLIPFORGE_EXTRA_CA_FILE=/etc/ssl/certs/corp.pem` in `.env`.

## Resource clean-up

Settings → Automation & safety → *Storage clean-up* sets how long downloaded sources, work files,
finished clips and the live-stream buffer are kept; a maintenance job enforces it every 6 hours.
