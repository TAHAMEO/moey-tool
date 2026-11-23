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
- **An assistant that edits your clips.** "Make clip 2 punchier", "cut the first 3 seconds",
  "karaoke captions on all clips", "why is clip 1 weak?" — with a free **local AI** model on your
  own PC (Ollama; set up in one card with download progress) the assistant reads each clip's scores
  and quality checks, decides the change and re-renders. Specific edits work without any model.
- **Links in, clips out.** YouTube long-form videos, YouTube livestreams, Instagram Reels and
  Instagram video posts. Public videos are downloaded with yt-dlp when your own copy isn't
  available (owner switch; never behind a login, paywall or DRM). Uploading files is an optional
  fallback.
- **Whole folders.** Pick a folder in the browser, or let CLIPFORGE watch one (on Windows
  `Videos\ClipForge`): every video in it is turned into clips with one recipe and, if you want,
  posted on a timetable such as one clip a day at 18:00.
- **Exact posting times.** Schedule any post for a date and time. Public YouTube videos are
  handed to YouTube's own scheduler (they go out even if your PC is off); Instagram Reels are
  prepared early and published at the minute.
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
  confirms it; duplicate uploads are blocked. Accounts connect with one click (sign-in pop-up);
  see [ONE_CLICK_CONNECT.md](docs/ONE_CLICK_CONNECT.md).
- **Learning without fake numbers.** Only metrics the platforms return are stored; patterns are
  labelled as correlations until a randomised experiment confirms them; strategies are versioned
  and can be rolled back.
- **Safety controls.** Approval-first publishing, pause, emergency stop, per-integration switches,
  spending limits, roles, audit log, encrypted secrets, SSRF protection, untrusted-content fencing.

## Windows 10 and 11

Run **ClipForge-Setup-&lt;version&gt;.exe** (a ready-built one is in [dist/windows](dist/windows/)).
CLIPFORGE installs as an ordinary Windows app — no Docker, no Linux, no administrator rights, no
sign-in — shows its progress, adds Start menu shortcuts and opens in your browser. See
[docs/WINDOWS.md](docs/WINDOWS.md) (including *Local AI*). The installer is built with
`installer/windows/build.sh`.

## Quick start (Docker)

Requirements: a Linux server or VM with Docker Engine + Compose v2, 4 CPU cores, **16 GB RAM**
(8 GB minimum: the bundled speech model uses about 5.5 GB in a media worker while it transcribes) and
30 GB free disk plus room for your videos. macOS/Windows with Docker Desktop work for testing.

```bash
git clone <this repository> clipforge && cd clipforge
```

```bash
./scripts/install.sh
```

Open http://localhost:3000, create the owner account, then:

1. **AI Connections** — add at least one AI service (or run the optional local model server, below).
2. **Settings → Accounts** — connect YouTube and/or Instagram with one click (the first time,
   a guided set-up registers your Google/Instagram app unless your copy has it built in —
   [docs/ONE_CLICK_CONNECT.md](docs/ONE_CLICK_CONNECT.md)).
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
| [Windows](docs/WINDOWS.md) | The Windows 10/11 installer: what it does, everyday use, troubleshooting |
| [One-click connect](docs/ONE_CLICK_CONNECT.md) | Registering the Google/Instagram apps once, or building them into your copy |
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
installer/windows/  Windows setup.exe (NSIS) and its PowerShell / WSL scripts
config/             optional built-in Google/Instagram apps (oauth-apps.json)
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

Implemented and tested end to end with real software: the chat Brain, permanent memory, the
Model Hub with real local models (two connections, routing, failover), link and upload
ingestion, transcription, scene and face analysis, clip selection, rendering with captions,
covers, live-stream ingest, the job system, the web app, the Docker installation, and backup and
restore. YouTube, Instagram and cloud AI providers are implemented against their official APIs
and verified with contract tests only: no credentials were supplied and those services were
unreachable from the build environment. [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) states,
for each of the 38 acceptance tests and 20 link-ingestion tests, what passed for real, what was
contract-tested and what remains blocked — and what is needed to unblock it.

No licence has been chosen for CLIPFORGE itself yet; included third-party components keep their
own licences (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
