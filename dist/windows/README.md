# CLIPFORGE for Windows — installer

`ClipForge-Setup-0.3.2.exe` installs CLIPFORGE on Windows 10 (1809 or later) and Windows 11 as an
ordinary Windows app: **no Docker, no Linux, no WSL, no administrator rights, no sign-in**. Download
it, double-click it and follow the steps — see [docs/WINDOWS.md](../../docs/WINDOWS.md).

- SHA-256: see `ClipForge-Setup-0.3.2.exe.sha256`.
- Not code-signed: Windows SmartScreen may show "Windows protected your PC" — choose
  *More info → Run anyway*. Signing it needs a code-signing certificate from the publisher.
- Rebuild after changing CLIPFORGE: `installer/windows/build.sh` (on Linux with `nsis` and Node.js 22).

## Tested

The same `setup.exe` (built from the same source by CI) installed silently on real Windows Server
2022 and 2025 machines in GitHub Actions, run
[44980699387](https://github.com/TAHAMEO/moey-tool/actions/runs/44980699387): CLIPFORGE answered on
http://localhost:3000 without a sign-in; database, FFmpeg, workers, memory and downloader all OK;
listening on 127.0.0.1 only; stopped and started again; updated in place keeping its data; and
uninstalled. Not covered there: the 1.8 GB speech model download and Windows 10/11 desktop editions.

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
