# CLIPFORGE for Windows — installer

`ClipForge-Setup-0.4.0.exe` installs CLIPFORGE on Windows 10 (1809 or later) and Windows 11 as an
ordinary Windows app: **no Docker, no Linux, no WSL, no administrator rights, no sign-in**. Download
it, double-click it and follow the steps — see [docs/WINDOWS.md](../../docs/WINDOWS.md).

- SHA-256: see `ClipForge-Setup-0.4.0.exe.sha256`. (`ClipForge-Setup-0.3.5.exe` is the previous version, kept for reference.)
- Not code-signed: Windows SmartScreen may show "Windows protected your PC" — choose
  *More info → Run anyway*. Signing it needs a code-signing certificate from the publisher.
- Rebuild after changing CLIPFORGE: `installer/windows/build.sh` (on Linux with `nsis` and Node.js 22).

## Tested

**0.4.0** was built from this source with `installer/windows/build.sh`, and the same app (backend,
workers and the standalone web server inside the installer) was run in its Windows-app mode
(`clipforge desktop run`) on Linux and driven in a real browser: a 54 MB and a 1.5 GB video
uploaded through the web app with progress (both hung before at 10 MB); a project that had failed at
transcription was retried and went through transcription, highlights, rendering and packaging; a
download showed size, speed and time left; Local AI found a (simulated) Ollama, downloaded a model
with progress and connected it, and the assistant re-cut and re-rendered a clip from a chat request.
The backend test suite passes. The *Windows installer* GitHub workflow installs this setup.exe on
real Windows Server 2022/2025 and now also uploads a 30 MB video in pieces through the web app and
checks the Local AI page; it runs when this change is pushed. Not covered: Windows 10/11 desktop
editions, and real downloads of the speech model and of Ollama models (blocked in the build
environment; both use their publishers' official download APIs).

**0.3.x:** the same kind of `setup.exe` installed silently on real Windows Server 2022 and 2025
machines in GitHub Actions, run
[44980699387](https://github.com/TAHAMEO/moey-tool/actions/runs/44980699387): CLIPFORGE answered on
http://localhost:3000 without a sign-in; database, FFmpeg, workers, memory and downloader all OK;
listening on 127.0.0.1 only; stopped and started again; updated in place keeping its data; and
uninstalled.

## Changes in 0.4.0

- **Fixed: videos from your PC didn't upload.** The web server keeps only the first 10 MB of a
  request it forwards, so every real video upload hung. Videos (and media-library files) are now
  sent in 8 MB pieces, with a percentage and the size sent so far, a cancel button, and resume
  after a dropped connection. Tested with a 1.5 GB file.
- **Fixed: projects failed before or during rendering.**
  - *Retry from checkpoint* replayed the old failure instead of running the failed step again.
  - With no speech model installed, every video failed at transcription. The speech model is now
    faster-whisper (480 MB, about 1 GB of memory instead of the 1.8 GB / 5.5 GB Vosk model), and if
    setup could not download it, CLIPFORGE downloads it the first time a video needs it, with progress.
  - The speech model's memory is freed after transcribing, so rendering in the same worker has room.
  - A video processed again after storage clean-up "reused" files that were gone: rendering failed
    for every clip. Files are now checked and made again.
  - After *Re-render*, the clip's posts still pointed at the replaced video and were refused for
    publishing. They now use the new render.
  - FFmpeg and other programs' output, and text files, are read and written as UTF-8 (Windows'
    own code page broke on names and titles in other languages).
- **Download progress:** the job card and the Dashboard show the step running now: for a download,
  the size arrived, speed and time left (*120 MB of 260 MB · 5.2 MB/s · 35 s left*); for renders,
  how many clips are done. Direct links no longer stop after 10 minutes.
- **Local AI and the assistant:** *AI Connections → Local AI on this PC* finds Ollama (or LM Studio,
  Jan), connects it and downloads a recommended model with progress. Brain Chat's assistant then
  edits clips from plain language — "make clip 2 punchier", "cut the first 3 seconds", "karaoke
  captions on all clips", "why is clip 1 weak?" — and re-renders them. Specific edits work without
  any model. See [docs/WINDOWS.md](../../docs/WINDOWS.md#local-ai-free-on-this-pc).
- "Create the best Shorts from this upload" no longer reads as a request to publish.
- The live-stream listener stops correctly on Windows.

## Changes in 0.3.5

- **Fixed: rendering failed for every clip** ("Cannot select channel layout … format_out") — the
  Windows build of FFmpeg needed the output audio format spelled out. Every new build now renders a
  real clip on Windows before it is released.
- Shorts P3 (off until you switch it on): length chosen from the kind of clip, other openings for
  weak clips (with one-click Use), A/B tests of titles / first-frame text / text on frame one, and
  cost per clip and per published Short.

## Changes in 0.3.4

- Shorts, guardrails and feedback: a check against your last posts before anything is published
  automatically (another title option, or held for you), and early-retention feedback — viewed vs.
  swiped away, still watching at 3 s, replays — with flop flags, a ranking of what kept viewers, and
  YouTube Studio CSV import in the Learning Center. Off until you switch them on.

## Changes in 0.3.3

- Shorts, keep them watching: a re-hook at 20–25 s (quick zoom and a text line where a thought
  finishes), kinetic captions (big, a few words at a time, 1–3 power words pop), a transcript
  download for each clip, and 2–3 ranked title / first-frame options per clip with one-click "Use".
  All off until you switch them on in Settings → Clips & editing.

## Changes in 0.3.2

- Shorts: first-3-seconds hook score and fix (slow starts removed, first line within 1.5 s, bold text
  hook on frame one from the clip's own words), loop check (ends that flow back into the start, or a
  short blend), and clips with a weak opening held for your review. Switch them on in
  Settings → Clips & editing. Clips no longer fade in from black.

## Changes in 0.3.1

- Fixed: downloading or uploading a video failed with "storage error: key escapes storage root"
  (storage paths were checked as text with "/" — Windows uses "\\").

## Changes in 0.3.0

- Runs as an ordinary Windows app: Python, its own PostgreSQL (with pgvector), FFmpeg and Node.js,
  all from checksum-pinned packages, listening on this PC only. No Docker, Linux or WSL.
- No administrator rights needed; installs into your own folder.
- No sign-in: the browser on this PC is you.
- New **Schedule** page: every finished clip ready to schedule, upcoming posts by day (change or
  cancel), recently posted.
- Setup stops an older Linux-based CLIPFORGE (0.2.x) and offers to remove it.

## Earlier versions

- 0.2.3: the speech-model download shows progress.
- 0.2.2: checks that Windows can run WSL 2's virtual machine.
- 0.2.1: fixed "'wsl.exe' is not recognized" on 64-bit Windows.
