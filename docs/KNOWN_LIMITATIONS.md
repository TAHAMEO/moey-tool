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
- **Cloud AI providers** (OpenAI, Gemini, Anthropic) are verified with contract tests only. Real
  model calls were verified with local models (Llama 3.2 1B and Qwen 2.5 3B through Ollama).
- **WhisperX**: its isolated environment installs from the hashed lock file and imports
  correctly, but its model weights (Hugging Face) could not be downloaded, so WhisperX
  transcription itself was not run. The same applies to faster-whisper models. Real transcription
  was verified with Vosk.

## Platform rules (by design, not bugs)

- **Other people's YouTube videos** give metadata only: YouTube's API has no download endpoint,
  caption downloads require the owner's authorisation, and CLIPFORGE does not use unofficial
  downloaders. Your own videos work when the original file is in the media library or Google Drive.
- **YouTube livestreams** have no live media feed for apps. Live clipping needs your encoder to
  send the stream to CLIPFORGE (SRT); otherwise the replay is processed once its file is available.
- **Instagram** only gives apps the media of the connected professional account. It may withhold
  the media file of posts with licensed music.
- **Unverified Google projects** upload to YouTube as private until the project passes YouTube's
  API audit; apps in *Testing* get refresh tokens that expire after 7 days.
- **YouTube API quota**: 10,000 units/day by default ≈ four uploads with captions and thumbnails.
- **Instagram** has a rolling 24-hour publishing limit and no caption-file upload (captions are
  always burned in).

## Functional limits

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
  stream keys), 4 concurrent sessions per main worker, OBS or another SRT-capable encoder.
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
