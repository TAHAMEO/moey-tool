# Known limitations

Honest list of what CLIPFORGE does not do (yet), what could not be verified, and the platform
rules that shape it. See [TEST_RESULTS.md](TEST_RESULTS.md) for how each feature was tested.

## Not verified against the real services

The build environment had no credentials for YouTube, Instagram or cloud AI providers, and could
not reach `youtube.com`, `graph.instagram.com`, `graph.facebook.com`, `api.openai.com` or
`huggingface.co`. Therefore:

- **YouTube** (OAuth, resumable upload, caption track, thumbnail, playlist, processing checks,
  analytics) and **Instagram** (OAuth, Reels container, resumable upload, publishing, insights)
  are implemented against the official API documentation and verified with *contract tests*
  against local fakes of those APIs — **not** against the real platforms. Expect to find small
  differences on first real use (field names, error codes, timing); the error handling surfaces
  them clearly rather than failing silently.
- **Public video downloads** (yt-dlp) were verified end to end against a local web server (download,
  limits, storage, media-worker job, honest error states, workflow routing), but **not** against
  YouTube or Instagram, which were unreachable. YouTube changes its player regularly; keep yt-dlp
  and its challenge-solver package (`yt-dlp-ejs`) current — rebuilding the images picks up the
  pinned versions, and newer ones are a one-line change in `backend/pyproject.toml`.
- **Scheduled publishing** — YouTube's `publishAt` flow, moving/cancelling/holding a waiting
  video, and Instagram's publish-at-the-minute flow — is verified with contract tests against
  the local fakes, for the same reason.
- **One-click connect** (Desktop-app loopback sign-in, the Instagram relay page, in-app app set-up)
  is verified against local fakes of Google's and Instagram's OAuth endpoints and, for the relay
  page, in a real Chromium browser. A real sign-in needs a registered app and a person at the
  consent screen.
- **The Windows installer** is tested on real Windows machines in GitHub Actions (Windows Server
  2022 and 2025, run [44955908487](https://github.com/TAHAMEO/moey-tool/actions/runs/44955908487)):
  the real `setup.exe` runs silently through the whole installation — WSL 2, Ubuntu, Docker Engine,
  the CLIPFORGE build — and CLIPFORGE then answers on `http://localhost:3000` from Windows. Not
  covered there: the 1.8 GB speech-model download (skipped in CI; it is slow from its publisher's
  server), the restart path on a PC that has never used WSL (those machines already had the WSL
  features on), and Windows 10/11 desktop editions. On a real Windows 11 PC, 0.2.0 failed with
  "'wsl.exe' is not recognized" and 0.2.1 got as far as creating the Linux environment, where
  Windows could not yet run virtual machines; 0.2.2 detects that case. The installer is not
  code-signed, so SmartScreen asks once before it runs.
- **Cloud AI providers** (OpenAI, Gemini, Anthropic) are verified with contract tests only. Real
  model calls were verified with local models (Llama 3.2 1B and Qwen 2.5 3B through Ollama).
- **WhisperX**: its isolated environment installs from the hashed lock file and imports
  correctly, but its model weights (Hugging Face) could not be downloaded, so WhisperX
  transcription itself was not run. The same applies to faster-whisper models. Real transcription
  was verified with Vosk.

## Platform rules (by design, not bugs)

- **Other people's videos** are downloaded only when anyone can watch them without signing in,
  and only while *Settings → Sources → public downloads* is on. Whether you may reuse them is your
  responsibility (YouTube's and Instagram's terms restrict downloading other people's content;
  copyright applies). Age-restricted, members-only, private, paid and DRM videos are never
  downloaded. YouTube sometimes asks data-centre IP addresses to sign in ("confirm you're not a
  bot"); CLIPFORGE reports that rather than working around it — home connections are rarely asked.
- **Instagram** shows many posts only to signed-in users; for those the project reports
  `LOGIN_REQUIRED`. Your own Reels always work through the official API once Instagram is connected.
- **YouTube livestreams** have no live media feed for apps. Live clipping needs your encoder to
  send the stream to CLIPFORGE (SRT); otherwise the replay is processed once its file is available.
- **Instagram's API** only gives apps the media of the connected professional account. It may
  withhold the media file of posts with licensed music.
- **Instagram has no scheduling in its API**, so scheduled Reels are published by CLIPFORGE at the
  time: CLIPFORGE must be running then. Public YouTube videos are scheduled with YouTube itself and
  go out even when CLIPFORGE is off.
- **Instagram sign-in needs an HTTPS redirect address**; installations on `http://localhost` use
  the static relay page (`deploy/oauth-relay/instagram.html`), which you publish once on any HTTPS
  host. Google Desktop-app clients need nothing extra.
- **Unverified Google projects** upload to YouTube as private until the project passes YouTube's
  API audit; apps in *Testing* get refresh tokens that expire after 7 days.
- **YouTube API quota**: 10,000 units/day by default ≈ four uploads with captions and thumbnails.
- **Instagram** has a rolling 24-hour publishing limit and no caption-file upload (captions are
  always burned in).

## Functional limits

- **No cloud speech or vision services**: audio and frames are always processed on your server
  (only text is sent to AI providers), so transcription quality is bounded by the local engines.
- **Speech recognition**: the bundled Vosk model is English-only, adds no punctuation (CLIPFORGE
  restores basic punctuation heuristically) and needs ~5.5 GB of RAM per media worker. Other
  languages need faster-whisper or WhisperX models. No speaker diarisation.
- **Caption accuracy** is automatic. Uncertain and sensitive words are flagged for review, but
  nothing is corrected automatically; check flagged words before publishing. No translation.
- **Local language models on a CPU are slow** (1–3B models: seconds for short answers, ~1 minute
  for memory extraction) and noticeably weaker than cloud models: titles can be generic and
  second opinions often disagree. Long AI tasks run in the background, but a busy main worker
  delays other background work; add a GPU, use a cloud model, or run more workers.
- **Analytics and learning need time and data**: platform analytics arrive with a delay
  (YouTube Analytics typically 1–3 days), Instagram no longer reports impressions for Reels, and
  an experiment needs at least 8 published videos per arm before it concludes. Until then the
  Learning Center shows only correlations, labelled as such.
- **Scores are heuristics**: the 10 "potential" scores rank moments within a video; they are not
  view predictions.
- **Music, B-roll and logos** come only from the owner's own library (rights confirmed). There is
  no stock media library.
- **Live ingest**: SRT only (RTMP isn't offered because FFmpeg's RTMP listener does not check
  stream keys), 4 concurrent sessions per main worker, OBS or another SRT-capable encoder. On
  Windows, WSL forwards TCP but not UDP to `localhost`, so SRT ingest from the same PC needs WSL's
  mirrored networking (Windows 11 22H2+) or a server.
- **Watched folders** are checked every minute (no instant file-system events), take a file once
  it has stopped changing, and on Windows read `Videos\ClipForge` through WSL's file sharing,
  which is slower than a Linux disk for very large files.
- **Chat time phrases** cover the common forms ("tomorrow at 6pm", "Friday 9:30", "on 25 November
  at 18:00", "in 2 hours", "every day at 6pm"); unusual wordings are not understood — use the
  date picker. Bare hours 1–7 are read as afternoon/evening. CLIPFORGE always repeats the exact
  time it understood.
- **Backups** cover the database and memory, not rendered media files (snapshot the data volume
  or use S3 with versioning). Local-volume storage can't be shared between servers; multi-server
  setups need S3 storage.
- **One organisation per installation**, English interface, no native mobile app (the web app
  works on phones).
- **WhisperX is not in the Docker images** (it brings PyTorch and GPU libraries); run it in its own
  environment on the host.
- **Frontend tests** are a browser smoke test (`frontend/e2e/smoke.mjs`) rather than a full UI
  test suite; the API behind every page is covered by the backend tests.

## Environment notes from the build

- Building llama.cpp from source was not permitted in the build environment, so local models were
  served with the official Ollama image instead (supported in `docker-compose.yml` as the
  `local-ai` profile).
- Docker Hub rate-limited anonymous pulls during the build; images were pulled through Google's
  Docker Hub mirror (`mirror.gcr.io`). This affects only how the build machine fetched base images.
