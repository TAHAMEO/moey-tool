# CLIPFORGE AI

A self-hosted AI video production and publishing platform. You paste a YouTube or Instagram
link into one chat and say what you want; CLIPFORGE checks what access it legitimately has to
that source, finds the strongest moments, edits them into vertical clips with accurate captions,
writes titles and descriptions, and — when you allow it — publishes to your own YouTube and
Instagram accounts, then learns from the results.

```
"Make 3 Shorts under 40 seconds from https://www.youtube.com/watch?v=…"
        │
        ▼
 Brain (LangGraph) ──► Source Access Resolver ──► acquire media / transcript (authorised methods only)
        │                                              │
        │              local pipeline: FFmpeg · Vosk/faster-whisper/WhisperX · PySceneDetect · MediaPipe
        │                                              │
        ├──► highlight scoring (10 criteria) ──► face-aware 9:16 render, captions, loudness, QC
        ├──► titles, descriptions, hashtags, covers (Model Hub: local first, paid only within budget)
        ├──► approval ──► YouTube (resumable upload, caption track) / Instagram Reels (official API)
        └──► analytics (only what platforms report) ──► learning engine (tested strategies, versioned)
```

## What it does

- **One chat, one Brain.** Plain-language requests, status, costs, memory and controls all go
  through Brain Chat. Everything the Brain does is shown as cards you can open, retry or approve.
- **Links in, clips out.** YouTube long-form videos, YouTube livestreams, Instagram Reels and
  Instagram video posts. Uploading files is an optional fallback, not the normal workflow.
- **Honest source access.** Every link is classified into one of 12 access states
  (full media, transcript only, metadata only, live feed, login/permission required, private,
  unavailable, …). CLIPFORGE never bypasses DRM, logins, paywalls or platform protections, and
  never claims to have analysed video it could not access. See [docs/SOURCE_ACCESS.md](docs/SOURCE_ACCESS.md).
- **Local-first media pipeline.** Transcription, scene detection, face tracking, audio events,
  rendering, captions and covers run on your server with open-source tools. No per-minute fees.
- **AI Model Hub.** Connect OpenAI, Google Gemini, Anthropic, any OpenAI-compatible endpoint or a
  local Ollama server. Tasks are routed automatically by capability and cost, with fallback,
  caching and daily/monthly/per-video spending limits.
- **Permanent memory.** Preferences and lessons are stored in PostgreSQL + pgvector (Mem0) and
  survive restarts; you can view, correct, delete and export them.
- **Real publishing.** YouTube Data API (resumable upload, caption track, thumbnail, playlist,
  processing verification, quota tracking) and the Instagram API with Instagram Login (Reels,
  container polling, publishing limits). A video is marked *published* only after the platform
  confirms it; duplicate uploads are blocked.
- **Learning without fake numbers.** Only metrics the platforms return are stored; patterns are
  labelled as correlations until a randomised experiment confirms them; strategies are versioned
  and can be rolled back.
- **Safety controls.** Approval-first publishing, pause, emergency stop, per-integration switches,
  spending limits, roles, audit log, encrypted secrets, SSRF protection, untrusted-content fencing.

## Quick start (Docker)

Requirements: a Linux server or VM with Docker Engine + Compose v2, 4 CPU cores, **16 GB RAM**
(8 GB minimum: the bundled speech model keeps about 5.5 GB in memory in each media worker) and
30 GB free disk plus room for your videos. macOS/Windows with Docker Desktop work for testing.

```bash
git clone <this repository> clipforge && cd clipforge
```

```bash
./scripts/install.sh
```

Open http://localhost:3000, create the owner account, then:

1. **AI Connections** — add at least one AI service (or run the optional local model server, below).
2. **Settings → Accounts** — connect YouTube and/or Instagram (needs the one-time app setup in
   [docs/API_SETUP.md](docs/API_SETUP.md)).
3. **Brain Chat** — paste a link and say what you want.

For a public server with HTTPS: `./scripts/install.sh --domain clips.example.com`
(see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

Optional local language models (no API costs): `docker compose --profile local-ai up -d`,
then add *Ollama (local)* in AI Connections with `http://ollama:11434/v1`.

## Documentation

| Guide | |
|---|---|
| [User guide](docs/USER_GUIDE.md) | Using Brain Chat, My Videos, AI Connections, Learning Center, Settings |
| [Installation](docs/INSTALL.md) | Docker and bare-metal installation, models, upgrades |
| [Deployment](docs/DEPLOYMENT.md) | Cloud servers, HTTPS, backups and restore, monitoring, scaling |
| [API setup](docs/API_SETUP.md) | Google/YouTube, Meta/Instagram and AI provider credentials |
| [Source access](docs/SOURCE_ACCESS.md) | The 12 access states and what CLIPFORGE will and won't do |
| [Security](docs/SECURITY.md) | Threat model and controls |
| [Known limitations](docs/KNOWN_LIMITATIONS.md) | What doesn't work yet, and why |
| [Test results](docs/TEST_RESULTS.md) | Acceptance tests: passed, contract-tested and blocked |
| [Repository integration report](docs/REPOSITORY_INTEGRATION_REPORT.md) | Every evaluated open-source project and the decision taken |
| [Third-party notices](THIRD_PARTY_NOTICES.md), [SBOM](sbom/) | Licences and the software bill of materials |

## Repository layout

```
backend/            Python 3.12 API, Brain, workers, media pipeline (FastAPI, LangGraph, Mem0)
  clipforge/        application package          tests/  pytest suite (unit, contract, end-to-end)
  migrations/       Alembic database migrations
frontend/           Next.js 16 web app (React 19, Tailwind 4)
workers/whisperx/   optional WhisperX engine, run in its own virtual environment
deploy/             Dockerfiles, Caddy, PostgreSQL init
scripts/            install, backup and restore
docs/               guides and reports
```

## Development

```bash
cd backend && uv sync && uv run alembic upgrade head && uv run clipforge api --port 8000
```

```bash
cd backend && uv run clipforge worker
```

```bash
cd frontend && npm ci && npm run dev
```

Tests (PostgreSQL with pgvector and Redis on localhost; see `backend/tests/conftest.py`):

```bash
cd backend && uv run pytest -m "not slow"
```

The slow suite (`-m slow`) runs the real media pipeline and live-stream ingest; it needs the
local speech model (`uv run clipforge download-models --vosk`).

## Status

All features above are implemented and tested. Platform integrations (YouTube, Instagram, cloud
AI providers) are verified against their documented APIs with contract tests; they could not be
exercised against the real services from the build environment because no credentials were
supplied and several hosts were unreachable. [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) lists
exactly what was verified how, and what remains blocked.

No licence has been chosen for CLIPFORGE itself yet; included third-party components keep their
own licences (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
