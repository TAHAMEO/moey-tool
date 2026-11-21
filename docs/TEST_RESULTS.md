# Test results and acceptance report

Date: 2026-11-18. Environment: Ubuntu 24.04, 4 vCPU, 15 GiB RAM, no GPU (details in the
[integration report](REPOSITORY_INTEGRATION_REPORT.md#1-build-environment-inventory-phase-1)).

## How to read this report

| Status | Meaning |
|---|---|
| **PASS** | Exercised with real software end to end — no mocks or fakes — in the automated suite or in a recorded run described below |
| **CONTRACT** | CLIPFORGE's side is implemented against the official API documentation and verified against a local fake of that API. The real service could not be used here. **This is not a pass of the real integration.** |
| **BLOCKED** | Could not be verified: credentials were not supplied, the host is unreachable from the build environment, or a download was not possible. Requirements to unblock are listed at the end |

Nothing in this report was produced with mocked success or invented metrics. Fakes are used only
where marked CONTRACT, and they reproduce documented request/response shapes and error codes.

## Automated test suite

**198 tests, all passing.** Full run on 2026-11-21 (`pytest -m "slow or not slow"`): 197 passed, 0 failed
in 12 min 59 s on 4 vCPU; the chat folder test added afterwards passed with its file and the Brain
Chat and scheduling suites (65 passed). 6 tests are marked `slow` (real media, real live stream,
full speech-model install); the rest run in about 4 minutes.

| File | Tests | What it covers |
|---|---|---|
| `test_analytics_learning.py` | 6 | analytics collection (real metrics only), comparison statistics, learning loop with experiments and versioned strategies |
| `test_auth_api.py` | 8 | first-run setup, sessions, CSRF, lockout, roles, cookie flags, security headers, audit log, forged forwarded headers |
| `test_batches.py` | 11 | folder batches: recipe, path safety (incl. symlinks), watched folders (stable files, duplicates, moving to Processed, changed files), browser uploads, pause/resume/cancel, posting slots, auto-publish vs draft-only, chat command |
| `test_brain_chat.py` | 19 | Brain Chat intents and actions, memory survives restart, knowledge reuse/consensus/disputes, controls, uploads |
| `test_connect.py` | 7 | one-click connect (CONTRACT): app sources and precedence, encrypted storage, credential check, Desktop-app loopback sign-in, Instagram relay, strict loopback origins, refresh with the issuing app |
| `test_jobs_runtime.py` | 7 | durable jobs: LangGraph checkpoints, child jobs, approvals, retries, stale leases, dedupe, pause, cancel |
| `test_library.py` | 2 | media library rights gate, type checks, master files |
| `test_live.py` | 2 | **real** SRT live stream: authentication, disconnect/reconnect, live transcription, replay, retention |
| `test_memory_env.py` | 1 | permanent memory without a writable home directory (containers) |
| `test_memory_refine.py` | 4 | instant remember with background LLM refinement |
| `test_metadata.py` | 2 | title/description sanitising |
| `test_model_hub.py` | 13 | providers, discovery, routing, fallback, budgets, caching, structured output, adapters (CONTRACT for cloud APIs) |
| `test_models.py` | 3 | verified speech-model installation, tampered archive and zip-slip refusal |
| `test_pipeline_e2e.py` | 3 | **real** media pipeline: link → download → transcription → analysis → selection → face-tracked render → captions → packages; reuse of expensive work; resumable transcription |
| `test_public_download.py` | 19 | public downloads with yt-dlp against a local server: download, size limit, extractor allow-list, no sign-in/geo-bypass options, honest error states, media-worker job, sign-in wall → `LOGIN_REQUIRED` project |
| `test_publishing.py` | 7 | YouTube/Instagram publishing (CONTRACT): resumable upload, interruption, duplicates, quota, captions, policies |
| `test_render_still.py` | 3 | caption-free covers, cover text fitting, brand caption colour |
| `test_resolver.py` | 16 | source access resolver: all access states, ownership, live states, CDN guard, expired links, public downloads and their limits (CONTRACT for platforms) |
| `test_scheduling.py` | 35 | scheduled publishing: time phrases, YouTube's scheduler hand-off and confirmation, moving/editing/holding/cancelling waiting videos, publishing at the exact minute, missed posts, daily limits, emergency stop, quota reset, approvals, chat (CONTRACT for platform calls) |
| `test_retention.py` | 2 | storage clean-up of old clips without touching uploads in progress; live buffer default |
| `test_security.py` | 23 | encryption, key rotation, media worker without key, passwords, SSRF, untrusted-text fencing |
| `test_settings_api.py` | 5 | settings validation, controls, integration switches, worker diagnostics |

The slowest tests (2026-11-21 run):

- 168 s — `test_live_stream_end_to_end`
- 143 s — `test_duplicate_link_reuses_expensive_work`
- 113 s — `test_link_to_rendered_clips`
- 31 s — `test_transcription_resumes_failed_chunk`
- 19 s — `test_interrupted_upload_resumes_without_duplicate`
- 12 s — `test_workflow_children_approval_and_completion`
- 12 s — `test_resumes_do_not_consume_retry_budget`
- 9 s — `test_render_writes_caption_free_still`

Run it yourself (PostgreSQL with pgvector and Redis/Valkey on localhost):

```bash
cd backend && uv run pytest -m "not slow"
```

```bash
cd backend && uv run pytest -m slow
```

The slow suite uses real media: a synthetic 76-second talk (text-to-speech with four visual
scenes and a real face image), real FFmpeg, real Vosk speech recognition, real MediaPipe face
tracking, and a real SRT live stream pushed by FFmpeg.

## Recorded end-to-end runs

These were run against the running application and are reproducible with the commands given.

1. **Browser, development stack** — owner setup, link → clips through Brain Chat
   (`Make 2 vertical shorts from http://…/talk.mp4`), pipeline completed (prepare → transcribe →
   analyse → render), project page with players, scores, QC, caption review, packages; every page
   on desktop and on an iPhone 13 viewport with no page errors, no HTTP 5xx and no sideways
   scrolling. The rendered clip loads in the browser player: 1080×1920, 29.1 s. Re-rendering a clip
   from *Adjust* worked. (`frontend/e2e/smoke.mjs`)
2. **Docker installation** — `./scripts/install.sh` on a clean Docker host: secrets generated,
   seven services started, migrations applied, API ready, MediaPipe models downloaded and verified
   *inside* the containers, `clipforge doctor` all essential checks OK. A video uploaded in the
   browser was processed by the media-worker container (prepare 28 s, Vosk transcription 58 s,
   analysis 4 s, render 43 s) and played through Caddy (1080×1920, 29.1 s).
3. **Real local AI models** (Ollama 0.40.16 with Llama 3.2 1B Q4_0 and Qwen 2.5 3B Q4_K_M,
   CPU only) added through the API as two connections — *Ollama (native)* and
   *OpenAI-compatible* — both passed the connection test (model discovery + live reply).
   Observed in the AI usage ledger:
   - routing by quality tier: knowledge answers and clip re-ranking (balanced tier) → Qwen 3B;
     memory extraction (fast tier) → Llama 1B; the 4k-context endpoint's failure on the long
     extraction prompt fell over to the native Ollama connection, which sizes the context;
   - failover: with the first connection pointed at a dead port, the question was answered by the
     other connection (6.3 s) and the broken one was marked *degraded* with a cooldown;
   - memory: "Remember that…" answered in 1.9 s; the background refinement replaced the verbatim
     note with the extracted fact ("User stated their brand colors are teal and warm orange");
   - knowledge: an important question got a second opinion from the *other model*, was marked
     *disputed* (similarity 0.57) with the reason shown;
   - video: clip re-ranking by Qwen 3B (`local_heuristic+llm:qwen2.5:3b`) and LLM-written titles;
     cached transcription/analysis reused for the repeated upload (130 s total).
4. **Backup and restore** — `scripts/backup.sh` (database, memory export and history), a setting
   changed afterwards, `scripts/restore.sh`: the change was rolled back, sign-in worked, the
   project was intact; restored row counts matched the source for every table checked (users,
   projects, clips, jobs, secrets, LangGraph checkpoints, audit log). File ownership after restore
   verified.
5. **Security checks on the containers** — a forged `X-Forwarded-For: 6.6.6.6` sent through
   Caddy was ignored (the real address was recorded); the media-worker container has no master
   key or platform secrets in its environment, the main worker has the key; containers run as
   uid 10001.
6. **Dependency audits** — `pip-audit` on the 123 PyPI runtime packages of `backend/uv.lock`: no
   known vulnerabilities. `npm audit` (production and dev): none. The optional WhisperX
   environment: 14 advisories in torch 2.8.0 / transformers 4.57.6, which WhisperX 3.8.6 pins (no
   fixed WhisperX release yet; it runs as a separate optional process, see SECURITY.md).

## Improvements of 2026-11-21

The five requested improvements, and how each was verified (same status meanings as above):

| Request | What was built | Status | Evidence |
|---|---|---|---|
| 1. Exact posting date and time | Schedule on any package, while approving (one per hour/day/week), from chat ("tomorrow at 6pm", in the browser's time zone) and per batch (daily time slots). Public YouTube videos use YouTube's own scheduler (`publishAt`), so they go out with the PC off; Instagram and unlisted/private YouTube posts are prepared 10 minutes early and published at the minute. Move, cancel, publish now, edit while waiting, hold, emergency stop, late-post limit, daily limits for the chosen day, Upcoming posts on the Dashboard | **PASS** (scheduling logic, queue, UI, chat) / **CONTRACT** (YouTube and Instagram calls) | `test_scheduling.py` (35 tests: time phrases, YouTube scheduler hand-off and confirmation, moving/editing/holding/cancelling a waiting video, unlisted video switched at the minute, Instagram published at the exact second, missed posts, holds, daily limits, emergency stop, quota reset, approvals, chat); browser walk-through below |
| 2. Download from YouTube and Instagram | yt-dlp downloads of videos anyone can watch, in the media worker, with no cookies/logins/geo-bypass/impersonation; private, members-only, age-restricted, paid, DRM and sign-in-walled videos get the honest access state; owner switch in Settings → Sources; clips of other creators' videos never auto-publish | **PASS** (download pipeline against a local server, honest states, routing) / **BLOCKED** (real YouTube and Instagram: unreachable from this environment) | `test_public_download.py` (19 tests incl. the media-worker job and a sign-in wall turning the project into `LOGIN_REQUIRED`), `test_resolver.py` (4 new); in the running stack `/api/diagnostics` reports yt-dlp 2026.10.26 with Deno and the challenge solver ready |
| 3. One-click account connection | Popup sign-in that closes itself; the Google/Instagram app found automatically (saved in-app encrypted, built in via `config/oauth-apps.json`, or `.env`); guided one-time set-up with credential check; Google Desktop-app loopback sign-in (no redirect registration); Instagram HTTPS relay page that only forwards to this computer | **CONTRACT** (OAuth exchanges) / **PASS** (relay page in Chromium; credential check against the **real** Google token endpoint) / **BLOCKED** (a real sign-in: needs a registered app and a person at the consent screen) | `test_connect.py` (7), `frontend/e2e/relay.mjs` (11 checks in Chromium, including refusal of foreign destinations); `google.check_app` with a made-up client against `oauth2.googleapis.com` returned "doesn't recognise this client" |
| 4. Windows 10/11 installer | `ClipForge-Setup-<version>.exe` (NSIS): checks the PC, turns on WSL 2 (restart and automatic continuation through a sign-in task), checks that Windows can run WSL 2's virtual machine, downloads Ubuntu 24.04 for WSL (SHA-256 pinned), installs Docker Engine in CLIPFORGE's own WSL environment, builds CLIPFORGE, downloads models with progress, Start-menu/desktop shortcuts, start-at-sign-in, launcher with a progress window, uninstaller that can keep data | **PASS** (full silent install on real Windows Server 2022 and 2025) / **PARTIAL** (Windows 11 desktop: in progress on the owner's PC) | GitHub Actions run [44955908487](https://github.com/TAHAMEO/moey-tool/actions/runs/44955908487): on both Windows machines the real `setup.exe /S` ran the whole installation in about 7 minutes (WSL 2, Ubuntu, Docker Engine, the CLIPFORGE build, vision models; the 1.8 GB speech model skipped), exited 0, `http://localhost:3000/api/health/ready` and `/login` answered 200 from Windows, Start menu shortcuts present, no continuation task left. Same run: started the way setup.exe starts it (32-bit PowerShell, input that never ends) the script switches to 64-bit, finds `C:\Windows\System32\wsl.exe`, and no program can wait for input (checked with findstr) or run past its time limit. Linux: `installer/windows/tests/run-tests.ps1` (12 checks) and `provision.sh` in the real Ubuntu WSL image. Owner's Windows 11 PC (build 26300): 0.2.0 failed ("'wsl.exe' is not recognized", fixed in 0.2.1); 0.2.1 reached "Creating the ClipForge Linux environment" and failed with HCS_E_SERVICE_NOT_AVAILABLE (virtual machines not yet available; 0.2.2 detects this and asks for the restart or explains the BIOS setting) |
| 5. Folder batches | Pick a folder in the browser (each video uploaded and started as it arrives), watch an inbox folder (on Windows `Videos\ClipForge`), or say it in Brain Chat; one recipe per batch; files taken once they stop changing; duplicates skipped; owner files untouched unless moved to `Processed`; posting slots shared across the batch; pause/resume/cancel | **PASS** | `test_batches.py` (11 tests: path safety incl. symlinks, stability, duplicates, moving, browser uploads, posting slots, auto-publish vs draft-only, the chat command "process all videos in the Podcast folder…"); browser walk-through: a real talk video copied into the watched `Podcast` folder was picked up and went through the full real pipeline to *Waiting for your approval* |

**Browser walk-through** (`frontend/e2e/features.mjs`, Docker stack, real pipeline): the downloader is
ready in the container; Settings shows the public-downloads switch; Connect opens the guided
Google set-up; a watched folder took in a real video and produced clips; a clip was scheduled for
tomorrow 18:00 and appeared under *Upcoming posts*; chat answered "publish clip 1 tomorrow at
6pm" with *Scheduled 2 post(s): tomorrow (Sun 22 Nov) at 18:00 UTC*. It found three interface
bugs that were then fixed: dialogs could render behind page content (they now render at the top
level of the page), *Clips per video* defaulted to 5 and overrode "make 2 shorts" in the
instruction, and routine background jobs were listed under *Working on now*.

## Acceptance tests (Part 26)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Brain Chat works | **PASS** | Recorded runs 1–3; `test_brain_chat.py` (link → project, intents, memory, knowledge, controls, per-turn cards) |
| 2 | At least two AI providers connect where credentials are available | **PASS** (local) / **BLOCKED** (cloud) | Two real connections with real local models (run 3). OpenAI, Gemini, Anthropic: no API keys supplied (OpenAI's host is also unreachable) — adapters CONTRACT-tested in `test_model_hub.py` |
| 3 | Automatic model routing works | **PASS** | Run 3 (tier-based routing visible in the usage ledger); `test_local_first_routing_and_quality_escalation`, `test_budget_blocks_paid_models` |
| 4 | Provider fallback works | **PASS** | Run 3 (dead connection → other connection, cooldown recorded); `test_fallback_on_rate_limit_sets_cooldown`, `test_invalid_credentials_marked` |
| 5 | Permanent memory survives restart | **PASS** | `test_memory_survives_process_restart` (new process), container restarts, backup/restore (run 4), `test_memory_works_without_a_writable_home` |
| 6 | Cross-model knowledge can be saved | **PASS** | Run 3 (two different local models, dispute recorded); `test_knowledge_saved_then_reused`, `test_second_opinion_needs_a_different_model`, `test_dispute_is_always_explained` |
| 7 | A real video can be uploaded (optional workflow) | **PASS** | Runs 1–2; `test_upload_validation_and_dedupe` |
| 8 | Transcription works | **PASS** | Vosk on real audio: 9.2 % word error rate over the whole test talk (development measurement); every clip's captions asserted below 25 % WER in `test_link_to_rendered_clips`; chunked and resumable (`test_transcription_resumes_failed_chunk`). WhisperX/faster-whisper: BLOCKED (weights) |
| 9 | Scene detection works | **PASS** | PySceneDetect on the four-scene test video (`test_pipeline_e2e.py`) |
| 10 | Clip selection works | **PASS** | `test_link_to_rendered_clips` (top clip contains the planted strongest moment, durations within limits, 10 scores and reasons) |
| 11 | FFmpeg renders a real MP4 | **PASS** | H.264/AAC 1080×1920 verified with ffprobe, QC passed, plays in the browser (runs 1–2) |
| 12 | Captions are accurate | **PASS** | Per-clip caption WER < 25 % against the known script in the e2e test; uncertain/sensitive words flagged for review (run 1 flagged one misrecognised word) |
| 13 | YouTube OAuth works when credentials are available | **BLOCKED** | No Google OAuth client supplied. CONTRACT: PKCE start/finish, token refresh |
| 14 | YouTube video upload works when authorised | **BLOCKED** | CONTRACT: resumable upload incl. interruption and resume without duplicates (`test_interrupted_upload_resumes_without_duplicate`) |
| 15 | YouTube subtitle upload is verified where authorised | **BLOCKED** | CONTRACT: caption insert followed by list verification (`test_youtube_publish_captions_thumbnail_and_analytics_schedule`) |
| 16 | Instagram authorisation works when eligible | **BLOCKED** | No Meta app supplied; `graph.instagram.com` unreachable. CONTRACT: OAuth code exchange, long-lived token |
| 17 | Instagram Reel publishing works when authorised | **BLOCKED** | CONTRACT: resumable upload, container polling, publish, permalink verification (`test_instagram_reel_resumable_publish`) |
| 18 | Captions and hashtags are included | **PASS** (generation) / **BLOCKED** (delivery) | Packages carry YouTube title/description/tags with #shorts, caption track, Instagram caption + hashtags; burned-in captions enforced for Reels (`test_instagram_requires_burned_in_captions`). Delivery depends on 14/17 |
| 19 | Publishing records are saved | **CONTRACT** | Publication rows with status, external id, URL, attempts and errors are written and verified in `test_publishing.py`; no real publication could be made |
| 20 | Analytics can be collected | **BLOCKED** | CONTRACT: real-metrics-only collection with unavailable metrics recorded (`test_youtube_metrics_collected_without_inventing`, `test_instagram_insights_marks_unsupported_metrics`) |
| 21 | Videos can be compared | **CONTRACT** | Median-normalised comparison and permutation tests verified on fixture data (`test_permutation_test_sanity`, `test_learning_loop_…`); no real analytics existed |
| 22 | Learning records can be created | **CONTRACT** | `test_learning_loop_correlation_experiment_and_promotion` (correlation → experiment → promotion, versioned); `test_no_data_means_no_findings` |
| 23 | Later videos can use saved strategies | **PASS** | Every clip records the strategy in force (`test_link_to_rendered_clips`); after an experiment promotes a strategy, new projects are assigned it (`test_learning_loop_correlation_experiment_and_promotion`) — promotion itself driven by fixture analytics |
| 24 | Failed tasks can be inspected and retried | **PASS** | Job events and timeline in the UI; `test_resume_from_checkpoint_after_failure`, `test_stale_lease_is_recovered`, `test_jobs_api_cancel_and_inspect`, `test_resumes_do_not_consume_retry_budget` |
| 25 | Duplicate publishing is prevented | **PASS** (CLIPFORGE side) | Idempotency key per platform/account/file and duplicate refusal happen before any platform call (`test_duplicate_publication_is_prevented`); resumed uploads don't duplicate (contract) |
| 26 | API secrets remain protected | **PASS** | AES-256-GCM bound to rows, rotation, keys never returned, media worker can't decrypt (`test_security.py`, `test_api_key_is_never_returned`); verified in containers (run 5) |
| 27 | The application is easy to use | **PASS** (evidence; final judgement is the owner's) | Chat-first, six pages, setup checklist, plain-language errors with alternatives, desktop and phone layouts (run 1) |
| 28 | A YouTube long-form link can be submitted | **PASS** (submission) / **CONTRACT** (access) | Link accepted, normalised, de-duplicated and resolved (`test_link_creates_project_and_job`, `test_same_source_in_any_url_form_is_deduplicated`); YouTube itself unreachable here |
| 29 | A YouTube livestream link can be submitted | **PASS** / **CONTRACT** | `test_livestream_states`, `test_live_without_feed_waits_for_live_source` |
| 30 | An Instagram Reel link can be submitted | **PASS** / **CONTRACT** | `test_instagram_states_and_cdn_guard` |
| 31 | An Instagram video post link can be submitted | **PASS** / **CONTRACT** | `/p/` and `/tv/` links detected; `test_instagram_video_post_and_image_post` |
| 32 | The resolver identifies available access methods | **CONTRACT** (platforms) / **PASS** (direct links, uploads, live ingest) | `test_resolver.py` (12 states, checks recorded per method) |
| 33 | Metadata-only, transcript-only and full-media are distinguished | **CONTRACT** / **PASS** | `test_youtube_third_party_video_is_metadata_only`, `test_owned_video_with_captions_is_transcript_only`, `test_owned_video_with_master_file_is_full_media`, full media in the e2e test |
| 34 | Never falsely claims full analysis with only metadata/transcript | **PASS** | Workflow behaviour verified: `test_transcript_only_workflow_never_claims_visual_analysis`, `test_metadata_only_workflow_reports_access_required` (platform responses faked, the honesty logic is real) |
| 35 | Link processing needs no manual download/upload when authorised access exists | **PASS** (direct links, live ingest) / **CONTRACT** (YouTube master files, Drive, Instagram) | e2e from a link without upload; `test_owned_video_with_master_file_is_full_media`, `test_expired_instagram_media_link_is_renewed` |
| 36 | Live processing resumes after temporary interruptions | **PASS** | `test_live_stream_end_to_end`: real SRT stream, encoder disconnects and reconnects, processing continues, replay built |
| 37 | Duplicate link submissions don't repeat expensive processing | **PASS** | `test_duplicate_link_reuses_expensive_work`; run 3 reused transcription and analysis |
| 38 | API usage and processing costs are recorded | **PASS** | AI usage ledger with tokens, latency, cost (estimated vs. known prices flagged) — real local calls in run 3; per-job budgets; job timings |

## Link ingestion tests (31.11)

| # | Case | Status | Evidence |
|---|---|---|---|
| 1 | Valid YouTube long-form URL | **PASS** (detection) / **CONTRACT** (access) | URL forms normalised and de-duplicated; resolver tests |
| 2 | Invalid YouTube URL | **PASS** | `test_invalid_link_explains_limitation` |
| 3 | Transcript available, media not | **CONTRACT** | `test_owned_video_with_captions_is_transcript_only` + workflow honesty test |
| 4 | Authorised full-media access | **CONTRACT** | `test_owned_video_with_master_file_is_full_media` (name **and** length must match) |
| 5 | Scheduled livestream | **CONTRACT** | `test_livestream_states` |
| 6 | Currently live livestream | **CONTRACT** / **PASS** (authorised feed) | `test_livestream_states`; real SRT feed in `test_live_stream_end_to_end` |
| 7 | Ended livestream with replay | **CONTRACT** | `test_owned_video_with_master_file_is_full_media` (replay case) |
| 8 | Livestream without authorised feed | **PASS** (behaviour) | `test_live_without_feed_waits_for_live_source` — waits, never claims monitoring |
| 9 | Instagram Reel URL | **CONTRACT** | `test_instagram_states_and_cdn_guard` |
| 10 | Instagram video post URL | **CONTRACT** | `test_instagram_video_post_and_image_post` |
| 11 | Instagram content requiring authorisation | **CONTRACT** | LOGIN_REQUIRED and PERMISSION_REQUIRED cases in `test_instagram_states_and_cdn_guard` |
| 12 | Private or inaccessible source | **CONTRACT** / **PASS** (direct links) | `test_youtube_without_credentials_uses_oembed_and_detects_private`, `test_direct_link_states` (404/403) |
| 13 | Duplicate source URL | **PASS** | `test_same_source_in_any_url_form_is_deduplicated`, `test_duplicate_link_reuses_expensive_work` |
| 14 | Interrupted processing job | **PASS** | `test_stale_lease_is_recovered`, `test_resume_from_checkpoint_after_failure` |
| 15 | Resumed long-video processing | **PASS** (mechanism) | `test_transcription_resumes_failed_chunk` — chunked transcription resumes at the failed chunk (small chunks forced; multi-hour sources were not run) |
| 16 | Source access that expires during processing | **CONTRACT** | `test_expired_instagram_media_link_is_renewed` (signed media URL refused → fresh URL from the API); OAuth tokens refresh automatically; expired direct links explain what to do |
| 17 | Metadata-only response | **CONTRACT** / **PASS** (behaviour) | `test_metadata_only_workflow_reports_access_required` |
| 18 | Transcript-only response | **CONTRACT** / **PASS** (behaviour) | `test_transcript_only_workflow_never_claims_visual_analysis` |
| 19 | Full-media processing | **PASS** | `test_link_to_rendered_clips`, runs 1–2 |
| 20 | Honest reporting of unsupported or blocked access | **PASS** | unsupported links, SSRF-blocked links, switched-off integrations (`test_integration_switches_are_enforced`), Instagram Stories explanation |

## Defects found and fixed during verification

Real-world testing found problems that unit tests had not:

- Resumed workflows consumed retry attempts, making late transient failures fatal.
- Thumbnails stacked the title text on top of burned-in captions; cover text could end mid-phrase.
- Client IP could be forged with `X-Forwarded-For` (login throttling, audit log).
- Permanent memory failed silently in containers (unwritable home directory).
- Restored memory history was left owned by root.
- Ollama's 4k default context rejected memory-extraction prompts; "remember" blocked the chat
  for ~90 s on CPU and held the memory lock.
- A "second opinion" could come from the same model through another connection; disputes were
  shown without a reason.
- Advice questions ("give me a tip for a Shorts title") were treated as production requests.
- Generated titles could contain newlines and characters the YouTube API rejects.
- Expired Instagram media links were retried instead of renewed.
- Upload endpoints did blocking I/O on the event loop; master files were read fully into memory.
- The integration kill-switch API ignored the integration name.
- Local master files were matched by name only (risk of clipping the wrong video).
- Five settings were stored but never applied: *keep finished clips* and *live stream buffer* are
  now enforced, *show technical details* now controls the diagnostics on project pages, and the
  *cloud transcription* and *cloud vision* switches were removed because no such features exist
  (audio and frames never leave the server).
- Test infrastructure: one test reused a database session after closing it, leaving a transaction
  open until garbage collection; under load this blocked the next test's table reset. The test was
  fixed, and the reset now ends leaked transactions and fails fast instead of waiting.

Each product fix has a regression test.

## What is needed to unblock the remaining tests

| To verify | Needed |
|---|---|
| YouTube OAuth, upload, captions, analytics (13–15, 18, 20 real) | A Google Cloud OAuth client (and optionally an API key), a YouTube channel, a server reachable over HTTPS — see [API_SETUP.md](API_SETUP.md) |
| Instagram authorisation and Reels (16–18, 20 real) | A Meta app with Instagram Login, an Instagram Business/Creator account added as tester |
| Cloud AI providers (2) | API keys for OpenAI, Google Gemini and/or Anthropic |
| WhisperX / faster-whisper transcription | Access to huggingface.co (and ideally a GPU) |
| Comparison and learning on real data (21–22) | Published videos with a few days of analytics |
| Real YouTube/Instagram downloads | A network that can reach youtube.com, googlevideo.com and instagram.com (any normal internet connection) |
| A real one-click sign-in | A registered Google client / Meta app (see [ONE_CLICK_CONNECT.md](ONE_CLICK_CONNECT.md)) and a person at the consent screen |
| The Windows installer on a Windows 10/11 desktop PC, including the speech-model download and the restart path | A Windows 10 (2004+) or 11 PC with virtualization enabled; run `ClipForge-Setup-<version>.exe` (in progress on the owner's PC) |
