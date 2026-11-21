# Third-party notices

CLIPFORGE is built on open-source software. This file lists what is included or required, with
licences and the obligations that apply. Machine-readable inventories: [`sbom/`](sbom/) (CycloneDX
1.6: `backend.cdx.json`, `frontend.cdx.json`, `whisperx-worker.cdx.json`). No AGPL code is included
anywhere in CLIPFORGE.

## Summary of obligations

| Component | Licence | What it means here |
|---|---|---|
| Vendored AutoClip modules (`backend/clipforge/vendor/autoclip/`) | MIT | Copyright notice and licence kept in that directory (`LICENSE`); modifications are marked `CLIPFORGE:` |
| FFmpeg (system package in the Docker image) | GPL-2.0-or-later (Ubuntu builds with `--enable-gpl`, libx264) | Run as a separate program, not linked. If you **distribute** the Docker image, you must also offer the corresponding source of the GPL packages it contains — Ubuntu publishes them (`apt-get source ffmpeg x264`) |
| psycopg 3 | LGPL-3.0 | Used unmodified as a separately installed, replaceable library |
| sharp / libvips (Next.js image optimisation) | Apache-2.0 / LGPL-3.0-or-later | Prebuilt, dynamically loaded library, unmodified |
| Fonts Anton and Inter (`backend/clipforge/assets/fonts/`) | SIL Open Font License 1.1 | Licence text in `OFL.txt`; the fonts are not sold on their own |
| caniuse-lite (browser data used by Next.js) | CC-BY-4.0 | Attribution: Alexis Deveria and contributors, caniuse.com |
| Test image `backend/tests/fixtures/astronaut.jpg` | Public domain (NASA) | See `backend/tests/fixtures/NOTICE.md` |

## Vendored source

**AutoClip (local-first)** — <https://github.com/artbyjazi/autoclip>, commit `5d0eac3`.
Copyright (c) 2026 Jad Ghazi. MIT License (full text in `backend/clipforge/vendor/autoclip/LICENSE`).
Used: transcript word model, sentence-boundary refinement, MediaPipe face-landmark reframing and the
libass caption builder.

## Fonts

- Anton — Copyright 2020 The Anton Project Authors (https://github.com/googlefonts/AntonFont), OFL-1.1
- Inter — Copyright 2016-2023 The Inter Project Authors (https://github.com/rsms/inter), OFL-1.1
  (also used in the web app via `@fontsource-variable/inter`)

## Services and programs used at runtime

| Software | Licence | Used as |
|---|---|---|
| PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`) | PostgreSQL License | Database, vector search |
| Valkey 8 (`valkey/valkey:8-alpine`) | BSD-3-Clause | Wake-ups and throttling |
| Caddy 2 (`caddy:2-alpine`) | Apache-2.0 | HTTPS front door |
| Ubuntu 24.04 base, Python 3.12 | various (Debian/Ubuntu package licences), PSF-2.0 | Backend image |
| FFmpeg 6.1, x264, libass | GPL-2.0-or-later / LGPL | Media processing (separate process) |
| Node.js 22 (`node:22-bookworm-slim`) | MIT | Web app image |
| Ollama (optional, `local-ai` profile) | MIT | Local language-model server |
| Deno 2.9 (PyPI `deno` wheel, inside the backend image) | MIT | Sandboxed JavaScript runtime for yt-dlp's YouTube challenge scripts |
| Windows installer stub (NSIS 3.09) | zlib/libpng | `ClipForge-Setup.exe` |
| Ubuntu 24.04 WSL image, Docker Engine (downloaded by the Windows installer on the user's PC) | various (Ubuntu package licences) / Apache-2.0 | CLIPFORGE's WSL 2 environment; not redistributed with CLIPFORGE |
| WhisperX 3.8.6 + PyTorch (optional, separate environment) | BSD-2-Clause, BSD-3-Clause | High-accuracy transcription; see `sbom/whisperx-worker.cdx.json` |

## Models

| Model | Licence | Notes |
|---|---|---|
| Vosk `vosk-model-en-us-0.22` | Apache-2.0 | Downloaded at install time from alphacephei.com, verified file by file |
| MediaPipe face landmarker, BlazeFace, Universal Sentence Encoder, YAMNet | Apache-2.0 | Downloaded at install time from Google, SHA-256 pinned |
| faster-whisper / WhisperX models (optional) | MIT (Whisper), model-specific for alignment/diarisation models | Downloaded by you from Hugging Face |
| Local language models you add (optional) | each model's own licence | e.g. Llama 3.2 Community License. Check the upstream model card before commercial use — Qwen2.5-3B's card lists the Qwen Research License even though some repackaged copies ship an Apache-2.0 text |

## Python packages (backend runtime, from `backend/uv.lock`)

| Package | Version | Licence |
|---|---|---|
| absl-py | 2.5.1 | Apache-2.0 |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.7 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| argon2-cffi-bindings | 26.2.0 | MIT |
| attrs | 26.1.0 | MIT |
| av | 19.0.2 | BSD-3-Clause |
| boto3 | 1.44.5 | Apache-2.0 |
| botocore | 1.44.5 | Apache-2.0 |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| contourpy | 1.4.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| ctranslate2 | 4.9.0 | MIT |
| cycler | 0.12.1 | BSD License |
| deno | 2.9.8 | MIT |
| distro | 1.9.0 | Apache Software License |
| dnspython | 2.9.2 | ISC |
| email-validator | 2.4.0 | The Unlicense (Unlicense) |
| fastapi | 0.163.2 | MIT |
| faster-whisper | 1.2.1 | MIT License |
| filelock | 4.2.12 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fonttools | 4.68.0 | MIT |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| grpcio | 1.85.3 | Apache-2.0 |
| h11 | 0.16.0 | MIT License |
| h2 | 4.4.1 | MIT |
| hf-xet | 1.8.0 | Apache-2.0 |
| hpack | 4.2.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.2 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.2 | BSD-3-Clause |
| huggingface_hub | 2.4.0 | Apache Software License |
| hyperframe | 6.1.0 | MIT License |
| idna | 3.20 | BSD-3-Clause |
| instagram-python-sdk | 0.1.0 | MIT License |
| jiter | 0.19.0 | MIT |
| jmespath | 1.1.0 | MIT License |
| jsonpatch | 1.47 | BSD-3-Clause |
| jsonpointer | 3.12 | BSD-3-Clause |
| kiwisolver | 1.5.1 | BSD License |
| langchain | 1.5.0 | MIT License |
| langchain-core | 1.8.2 | MIT License |
| langchain-protocol | 0.0.21 | MIT License |
| langgraph | 1.2.22 | MIT |
| langgraph-checkpoint | 4.3.2 | MIT |
| langgraph-checkpoint-postgres | 3.2.1 | MIT |
| langgraph-prebuilt | 1.1.2 | MIT |
| langgraph-sdk | 0.4.14 | MIT |
| langsmith | 0.15.0 | MIT |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mediapipe | 1.2.3 | Apache Software License |
| mem0ai | 2.2.1 | Apache-2.0 |
| multidict | 7.2.1 | Apache-2.0 |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.32.0 | MIT License |
| openai | 3.55.0 | Apache-2.0 |
| opencv-contrib-python-headless | 5.1.0.95 | Apache Software License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.13.1 | Apache-2.0 OR MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pgvector | 0.5.1 | MIT |
| pillow | 13.0.0 | MIT-CMU |
| platformdirs | 4.14.7 | MIT |
| portalocker | 4.4.2 | BSD-3-Clause |
| posthog | 8.0.1 | MIT |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 6.33.6 | 3-Clause BSD License |
| psycopg | 3.3.9 | LGPL-3.0-only |
| psycopg-binary | 3.3.9 | LGPL-3.0-only |
| psycopg-pool | 3.3.3 | LGPL-3.0-only |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| pyparsing | 3.3.3 | MIT |
| pyspellchecker | 0.9.1 | MIT |
| pysubs2 | 1.9.1 | MIT |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.34 | Apache-2.0 |
| pytz | 2026.6 | MIT License |
| PyYAML | 6.0.3 | MIT License |
| qdrant-client | 1.20.0 | Apache Software License |
| redis | 8.1.0 | MIT |
| requests | 2.34.2 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| s3transfer | 0.19.3 | Apache Software License |
| scenedetect | 0.7.3 | BSD-3-Clause |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License; Apache Software License |
| sounddevice | 0.5.6 | MIT |
| SQLAlchemy | 2.1.9 | MIT |
| srt | 3.5.3 | MIT License |
| starlette | 1.10.0 | BSD-3-Clause |
| tenacity | 9.2.1 | Apache-2.0 |
| tokenizers | 0.23.3 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uuid_utils | 0.19.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | Apache Software License; MIT License |
| vosk | 0.3.45 | Apache Software License |
| watchfiles | 1.3.0 | MIT License |
| websockets | 17.4 | BSD-3-Clause |
| xxhash | 4.1.0 | BSD-2-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| yt-dlp | 2026.10.26 | Unlicense |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC |
| zstandard | 0.25.0 | BSD-3-Clause |

## JavaScript packages (web app production dependencies, from `frontend/package-lock.json`)

| Package | Version | Licence |
|---|---|---|
| baseline-browser-mapping | 2.11.38 | Apache-2.0 |
| caniuse-lite | 1.0.30001826 | CC-BY-4.0 |
| client-only | 0.0.1 | MIT |
| clsx | 2.1.1 | MIT |
| @img/colour | 1.1.0 | MIT |
| csstype | 3.2.4 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| @next/env | 16.4.6 | MIT |
| @swc/helpers | 0.5.23 | Apache-2.0 |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 |
| lucide-react | 1.75.0 | ISC |
| nanoid | 3.3.20 | MIT |
| next | 16.4.6 | MIT |
| picocolors | 1.1.1 | ISC |
| react | 19.3.1 | MIT |
| @types/react | 19.3.0 | MIT |
| react-dom | 19.3.1 | MIT |
| @emnapi/runtime | 1.11.3 | MIT |
| scheduler | 0.28.0 | MIT |
| semver | 7.8.6 | ISC |
| sharp | 0.35.5 | Apache-2.0 |
| @img/sharp-libvips-linux-x64 | 1.3.4 | LGPL-3.0-or-later |
| @img/sharp-linux-x64 | 0.35.5 | Apache-2.0 |
| @img/sharp-wasm32 | 0.35.5 | Apache-2.0 AND LGPL-3.0-or-later AND MIT |
| source-map-js | 1.2.2 | BSD-3-Clause |
| styled-jsx | 5.1.6 | MIT |
| @next/swc-linux-x64-gnu | 16.4.6 | MIT |
| tslib | 2.9.1 | 0BSD |

## Referenced, not included

ViralMint and Postiz (AGPL-3.0) were studied as architecture references only; no code from them is
included. See [`docs/REPOSITORY_INTEGRATION_REPORT.md`](docs/REPOSITORY_INTEGRATION_REPORT.md) for
every evaluated project and the decision taken.
