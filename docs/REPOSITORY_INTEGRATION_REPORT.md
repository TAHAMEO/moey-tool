# CLIPFORGE AI — Technical Inventory & Repository Integration Report

Inspection date: 2026-11-18. Every repository below was cloned at the listed commit and its
README, LICENSE, dependency manifests and source layout were read. "Test" results refer to
checks actually executed in the build environment; nothing is listed as installed unless it
was installed and imported/executed. The final pass/fail status of each acceptance test is in
[`TEST_RESULTS.md`](TEST_RESULTS.md).

## 1. Build-environment inventory (Phase 1)

| Item | Finding |
|---|---|
| OS | Ubuntu 24.04.5 LTS, x86_64, kernel 6.18 |
| CPU / RAM / GPU | 4 vCPU, 15 GiB RAM, **no GPU** (`nvidia-smi` absent) |
| Disk | ~31 GB free at start |
| Python | 3.11, 3.12, 3.13 present. **Backend uses 3.12** (MediaPipe/AutoClip reframe code and WhisperX all support it; FunClip docs use 3.12) |
| Node.js / npm | v22.23.5 / 10.9.9 |
| Git | 2.43.0 |
| Docker | 29.10.0 + Compose v5.7.1 (daemon started manually). Docker Hub later rate-limited anonymous pulls (HTTP 429), so `mirror.gcr.io` was configured as registry mirror; ghcr.io's blob CDN is blocked |
| FFmpeg | 6.1.1 (Ubuntu build) with `--enable-gpl --enable-libx264 --enable-libass --enable-libfreetype --enable-libfontconfig --enable-libsrt --enable-libflite` |
| PostgreSQL | 16.15 + pgvector 0.6.0 (apt `postgresql-16-pgvector`) |
| Redis | redis-server present (7.x, Ubuntu) |
| Existing project | Empty repository (no commits) on branch `claude/festive-mccarthy-czgitm` — nothing to preserve or overwrite |

### Network policy of the build sandbox (important for test interpretation)

| Reachable | Blocked by the sandbox egress policy |
|---|---|
| PyPI, npm, GitHub **git protocol**, `api.github.com` (own repo only), Docker Hub, ghcr.io, `*.googleapis.com` (YouTube Data/Analytics, OAuth, Gemini), `accounts.google.com`, `api.anthropic.com`, `storage.googleapis.com` (MediaPipe models), `archive.ubuntu.com` | `huggingface.co` (Whisper/WhisperX/pyannote weights), `youtube.com`, `instagram.com`, `graph.facebook.com`, `graph.instagram.com`, `api.openai.com`, `download.pytorch.org`, `modelscope.cn`, `ollama.com`, `alphacephei.com`, GitHub web/archives/releases |

Consequences: real YouTube/Instagram platform calls and OpenAI calls **cannot** be executed from
this sandbox, and Whisper-family weights cannot be downloaded here. No application credentials
(AI keys, Google OAuth client, Meta app) were provided. Those tests are reported as **BLOCKED**,
never as passed. Real local models were obtained only from official publishers' reachable
channels (see §4).

## 2. Integration decisions (catalog items 1–16)

| # | Repository | Version / commit inspected | License | Decision | Installation | Integration | Test (this environment) |
|---|---|---|---|---|---|---|---|
| 1 | [LangGraph](https://github.com/langchain-ai/langgraph) | PyPI `langgraph==1.2.22`, `langgraph-checkpoint-postgres==3.2.1` (repo `84ced60`) | MIT | **Install & integrate (core)** | `uv` (pinned, `uv.lock`) | Brain chat graph + durable video/publish workflow graphs with `PostgresSaver` checkpoints, interrupts for child jobs and publish approvals | See TEST_RESULTS |
| 2 | [Mem0](https://github.com/mem0ai/mem0) | PyPI `mem0ai==2.2.1` (repo `fff8a64`) | Apache-2.0 | **Install & integrate (core)** | `uv` | Memory engine with **pgvector** store in the CLIPFORGE Postgres and a LangChain-adapter embedder backed by the local MediaPipe sentence encoder (no paid API). Telemetry disabled (`MEM0_TELEMETRY=False`) | See TEST_RESULTS |
| 3 | [FunClip](https://github.com/modelscope/FunClip) | repo `cdcb53f` (v2.2.x) | MIT | **Evaluated — not installed in core** | — | Paraformer ASR is Chinese-centric and downloads from ModelScope; pins `numpy==1.26.4` and `moviepy==1.0.3`, which conflict with WhisperX (`numpy>=2.1`). Its English path is Whisper, already covered by WhisperX/faster-whisper. Documented as an optional isolated zh-ASR worker. ModelScope is unreachable here | Blocked by dependencies (shared env) + model host unreachable |
| 4 | [WhisperX](https://github.com/m-bain/whisperX) | PyPI `whisperx==3.8.6` (repo `b904f02`, 3.8.7rc1) | BSD-2-Clause | **Install in isolated venv (optional high-accuracy engine)** | Separate venv (`torch~=2.8`, `pyannote-audio>=4`) — never in the API env | Transcription engine #1 when installed (word alignment, optional diarization). CPU fallback supported; GPU optional | **Verified:** the hashed lock (`workers/whisperx/requirements.lock`, 117 packages, torch 2.8.0) installs into an isolated Python 3.12 venv, `import whisperx` works, `transcribe.py --check` correctly reports the weights unavailable offline (Hugging Face blocked) — transcription itself **BLOCKED**. Audit: 14 known advisories in torch 2.8.0 / transformers 4.57.6, which WhisperX 3.8.6 pins (see SECURITY/TEST_RESULTS) |
| 5 | [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | `scenedetect==0.7.3` (repo `8a0b5ce`) | BSD-3-Clause | **Install & integrate (core)** | `uv` | Scene boundaries feed clip boundaries, crop-path shot splitting and thumbnail selection | See TEST_RESULTS |
| 6 | [FFmpeg](https://github.com/FFmpeg/FFmpeg) | System binary 6.1.1 (repo HEAD `3a78197` inspected) | LGPL-2.1+; **this build is GPL** (`--enable-gpl`, libx264) | **Use system binary (core renderer)** | apt / Docker base image | Invoked as a separate process (no linking). All untrusted inputs run with `-protocol_whitelist` and `-format_whitelist` | See TEST_RESULTS |
| 7 | [AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | repo `1142a74` | MIT | **Evaluated — not installed** | — | Default mode requires the paid MuAPI service; local mode = yt-dlp + faster-whisper + OpenAI/Gemini + OpenCV crop, all covered by better components below. Its yt-dlp YouTube download path is not an authorized access method for arbitrary videos. Idea reused: cache transcripts to avoid re-transcription | Not needed (redundant) |
| 8 | [AutoClip (local-first)](https://github.com/artbyjazi/autoclip) | repo `5d0eac3` | MIT | **Adapt selected modules (vendored with notice)** | Code vendored into `backend/clipforge/vendor/autoclip/` with its MIT license | Reused: transcript word model, sentence-boundary refinement, MediaPipe face-landmark reframing (tracks, speaker assignment, One-Euro smoothing, crop paths) and libass caption builder. Requirements honoured: Python 3.12, FFmpeg with libass+libx264. Its yt-dlp ingest is **not** used | See TEST_RESULTS |
| 9 | [AutoClip (desktop)](https://github.com/zhouxiaoka/autoclip) | repo `cd8e6be` (v1.5.8) | MIT | **Evaluated — not installed** | — | Desktop/Electron product that overlaps #8 and bundles yt-dlp; covers, post copy and per-platform presets are implemented natively instead of installing a second clipping system | Not needed (redundant) |
| 10 | [ViralMint](https://github.com/openclaw-easy/ViralMint) | repo `20f2bd7` | **AGPL-3.0** | **Architecture reference only** | — | No code copied into the proprietary core | Blocked by licensing for direct reuse |
| 11 | [google-api-python-client](https://github.com/googleapis/google-api-python-client) | repo `354d888` | Apache-2.0 | **Evaluated — not installed** | — | README states the library is "complete and in maintenance mode". No Cloud Client Library exists for the YouTube Data API, so CLIPFORGE calls the official REST endpoints directly with `httpx` (resumable upload protocol, captions, analytics). Samples consulted | Not needed |
| 12 | [YouTube Data API v3 / Analytics API v2](https://developers.google.com/youtube/v3) | REST | Google API Terms | **Integrated (REST)** | — | OAuth web flow (PKCE), resumable uploads with backoff, idempotency, captions insert/verify, thumbnails, playlists, analytics | Requires user credentials + reachable network → BLOCKED here; contract-tested |
| 13 | [Inoue-AI Instagram SDK](https://github.com/Inoue-AI/Inoue-AI-Instagram-SDK) | **git commit `5fcda41`** (package `instagram-python-sdk 0.1.0`) | MIT | **Install pinned git commit & integrate** | `uv` git dependency | Used for media listing (own-content access resolution), container polling and Reels insights. OAuth (not implemented by the SDK) and resumable `rupload` upload (not supported by the SDK) are implemented natively. ⚠️ **Supply-chain note:** the PyPI name `instagram-python-sdk` belongs to a *different* project (rohitkhatri) — never install it by name | Contract-tested; real API BLOCKED |
| 14 | [instagram-publish](https://github.com/remc0r/instagram-publish) | repo `e0dd56f` / PyPI 1.0.0 | MIT | **Evaluated — not installed** | — | Synchronous `requests` client covering the same URL-based publishing subset as #13; maintaining two publishing implementations has no technical justification | Not needed (redundant) |
| 15 | [Meta Instagram Platform](https://developers.facebook.com/docs/instagram-platform/) | Graph API (Instagram Login, `graph.instagram.com`) | Meta Platform Terms | **Integrated** | — | Instagram Login OAuth, long-lived token refresh, Reels containers, status polling, publish, verification, insights | Requires Meta app approval + professional account → BLOCKED here; contract-tested |
| 16 | [Postiz](https://github.com/gitroomhq/postiz-app) | repo `a638091` | **AGPL-3.0** | **Architecture reference only** | — | NestJS/Prisma/Temporal stack; a native scheduler (Postgres jobs + `run_after`) meets the requirements without the AGPL code | Blocked by licensing for direct reuse |

## 3. Integration groups (Part 5)

* **Core:** LangGraph, Mem0, FFmpeg, WhisperX (isolated), PySceneDetect, PostgreSQL + pgvector.
* **Video pipeline:** AutoClip-local modules (vendored) + PySceneDetect + transcription engines
  (WhisperX → faster-whisper → Vosk; all local — no cloud speech recognition). FunClip, AI-Youtube-Shorts-Generator
  and AutoClip-desktop were compared and rejected as redundant or conflicting.
* **Publishing:** official YouTube REST APIs, official Instagram Graph API, Inoue-AI SDK.
* **Architecture references only:** ViralMint, Postiz (AGPL).

## 4. Additional components selected (not in the catalog)

| Component | Version | License | Why |
|---|---|---|---|
| faster-whisper | 1.2.1 | MIT | CTranslate2 Whisper without PyTorch — light CPU engine when WhisperX is not installed |
| Vosk | 0.3.45 | Apache-2.0 | Small CPU-only engine. In this sandbox the model `vosk-model-en-us-0.22` was taken from the publisher's official image `alphacep/kaldi-en` (layer sha256 `be8c940e…` verified against the registry manifest) because alphacephei.com is blocked |
| MediaPipe (Tasks) | 1.2.3 | Apache-2.0 | Face landmarks for reframing, Universal Sentence Encoder for local embeddings, YAMNet for laughter/applause/music events. Official models from `storage.googleapis.com/mediapipe-models` |
| OpenCV (contrib, headless) | 5.1.0.95 | Apache-2.0 | Frame analysis; uv overrides replace GUI builds pulled by MediaPipe/PySceneDetect |
| pysubs2 | 1.9.1 | MIT | ASS/SRT/VTT writing |
| psycopg 3 / psycopg-pool | 3.3.9 / 3.3.3 | LGPL-3.0 (dynamically imported, unmodified) | PostgreSQL driver used by LangGraph's checkpointer |
| FastAPI / SQLAlchemy / Alembic / Redis client | see `backend/uv.lock` | MIT/BSD | API, ORM, migrations, wake-ups and caching |
| Next.js / React | see `frontend/package-lock.json` | MIT | Web UI |
| Caddy | 2 (official image) | Apache-2.0 | Front door with automatic HTTPS; routes `/api` straight to the API and sets a trustworthy client address |
| Valkey | 8 (official image) | BSD-3-Clause | Redis-protocol server for wake-ups/throttling in the Docker deployment. Chosen over Redis because Redis 7.4+ (what `redis:7-alpine` resolves to) is under the source-available RSALv2/SSPLv1 licences |
| Ollama | 0.40.16 (official image, optional `local-ai` profile) | MIT | Local language models. Verified with Llama 3.2 1B (Q4_0) and Qwen 2.5 3B (Q4_K_M) GGUF files from Docker Hub's `ai/` namespace (OCI manifests and layer digests verified). Building llama.cpp from GitHub sources was not permitted in this environment, so Ollama was used instead |
| yt-dlp | 2026.10.26 (PyPI, **without** the `[default]` extra) | Unlicense | Public video downloads, added at the owner's request (Settings → Sources). Runs in the media worker; only the YouTube and Instagram extractors are loaded; no cookies, logins, geo-bypass, impersonation or remote components. The `[default]` extra is not installed: it brings GPL-licensed `mutagen` (not needed) and impersonation helpers (not wanted) |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC | YouTube's player challenge solver scripts, used locally so yt-dlp never fetches code from the internet (`remote_components` stays empty) |
| Deno | 2.9.8 (PyPI `deno` wheel) | MIT | Sandboxed JavaScript runtime yt-dlp uses to run those scripts (no file, network or environment permissions) |
| tzdata | 2026.6 | Apache-2.0 | Time-zone database for schedules on systems without one (Docker images, Windows) |
| NSIS | 3.09 (build tool only) | zlib/libpng | Builds the Windows `setup.exe`; its runtime stub is under the zlib licence |
| Ubuntu 24.04 WSL image, Docker Engine | 20240423 image (SHA-256 pinned); Engine from Docker's apt repository | various / Apache-2.0 | Downloaded by the Windows installer on the user's PC into CLIPFORGE's own WSL 2 environment (not redistributed) |

**Supply-chain checks:** `pip-audit` (PyPI advisory database) found no known vulnerabilities in the
123 PyPI runtime packages of `backend/uv.lock`; `npm audit` found none in the frontend. The
git-pinned Instagram SDK is not covered by any advisory database (its source was inspected at the pinned commit in Phase 1; it is used only for media listing, container polling and insights).
`httpx2`/`httpcore2` in the lock are Pydantic's maintained HTTPX successor (project metadata points to
`github.com/pydantic/httpx2`), required by `openai`, `huggingface-hub` and `langsmith` — checked
because the name resembles `httpx` and the release was two days old.

**`yt-dlp`: previously excluded, now integrated at the owner's explicit request.** The first
version deliberately left out third-party downloaders. The owner then asked for YouTube and
Instagram links to download, so public downloads were added as an owner-controlled method (on by
default, *Settings → Sources*), with hard limits that keep the original rules: official and
owner methods are tried first; nothing behind a login, membership, age gate, paywall or DRM is
downloaded; no cookies or credentials are ever given to yt-dlp; no location faking or
impersonation; a sign-in wall is reported as `LOGIN_REQUIRED`; and clips from another creator's
video are never published automatically. YouTube's Terms of Service restrict downloading without
permission and Instagram's terms restrict automated collection, so every project made this way
reminds the owner to use only videos they own or may reuse. Details:
[`SOURCE_ACCESS.md`](SOURCE_ACCESS.md#public-downloads-yt-dlp).

## 5. Installation matrix

The machine-readable version is [`installation-matrix.json`](installation-matrix.json); exact
transitive versions are pinned in `backend/uv.lock`, `workers/whisperx/requirements.lock` and
`frontend/package-lock.json`. Third-party notices: [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
SBOM: [`../sbom/`](../sbom/).
