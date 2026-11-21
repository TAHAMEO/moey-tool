# CLIPFORGE on Windows 10 and 11

A normal installer, `ClipForge-Setup-<version>.exe`, prepares everything: you click *Next*, wait,
and CLIPFORGE opens in your browser.

## Requirements

| | |
|---|---|
| Windows | Windows 11, or Windows 10 version 2004 (build 19041) or later — 64-bit, Intel or AMD processor (not ARM) |
| Memory | 8 GB minimum, 16 GB recommended (the speech model uses about 5.5 GB) |
| Disk | about 25 GB free, plus room for your videos |
| Virtualization | switched on in the PC's BIOS/UEFI (*Intel VT-x* or *AMD-V/SVM*) — on most PCs it already is |
| Internet | about 5 GB is downloaded during the first installation |

## Install

1. Run **ClipForge-Setup-&lt;version&gt;.exe** and allow it to make changes (it needs administrator
   rights once, to turn on WSL 2).
2. Choose the options: a desktop shortcut, and whether CLIPFORGE should **start when you sign in**
   (recommended if you schedule Instagram posts or watch folders — those need CLIPFORGE running).
3. Click **Install** and leave the PC on. The window shows each step and its progress:

   | Step | What happens | Time |
   |---|---|---|
   | 1 | checks Windows, memory, disk and virtualization | seconds |
   | 2 | turns on **WSL 2** (Windows Subsystem for Linux) if it isn't already | 1–5 min |
   | 3 | downloads Ubuntu 24.04 for WSL (≈340 MB, checksum verified) and creates CLIPFORGE's own Linux environment, *ClipForge* | 2–5 min |
   | 4 | installs **Docker Engine** inside it (free and open source — Docker Desktop is *not* needed) | 2–4 min |
   | 5 | copies CLIPFORGE in and builds it | 10–30 min |
   | 6 | downloads the speech-recognition model (1.8 GB, every file verified) | 3–15 min |
   | 7 | starts CLIPFORGE and checks that it answers | about 1 min |

   If Windows has to **restart** to finish turning on WSL 2 (common on PCs that never used it),
   setup asks you; after you sign in again it **continues by itself** where it stopped. (It does
   that through a one-time Task Scheduler entry, *CLIPFORGE setup*, which runs setup with
   administrator rights at your next sign-in and is removed as soon as setup continues. If setup
   doesn't reappear within a minute of signing in, run it again yourself.)
4. On the last page, keep *Open CLIPFORGE now* ticked. Create your owner account in the browser
   (http://localhost:3000), then follow the *Get set up* list on the Dashboard.

The full log is `install.log` in the installation folder (`%LOCALAPPDATA%\ClipForge` unless you
chose another). If anything fails, run setup again: it continues where it stopped, keeps
everything already done, and offers the same folder.

## Everyday use

| Start menu → CLIPFORGE | |
|---|---|
| **CLIPFORGE** | starts it if needed (a small "Starting…" window, usually under a minute) and opens it in your browser |
| **Stop CLIPFORGE** | stops it and frees the memory it uses |
| **CLIPFORGE watched folders** | opens `Videos\ClipForge` |
| **Uninstall CLIPFORGE** | removes it (you choose whether your data is kept) |

- **Watched folders:** make a folder inside `Videos\ClipForge` (for example `Podcast`), then in
  CLIPFORGE choose *My Videos → Process a folder → Watch a CLIPFORGE folder*. Every video you put
  in it is processed with the settings you chose. See the [user guide](USER_GUIDE.md#processing-a-whole-folder).
- **Scheduled posts:** public YouTube videos go out at their time even when the PC is off (YouTube
  publishes them). Instagram posts need CLIPFORGE running at their time — keep the PC on and
  CLIPFORGE started (or turn on *start when I sign in*). A post that is more than 3 hours late
  waits for you instead.
- **Connecting YouTube and Instagram** works from CLIPFORGE on the same PC (http://localhost:3000).
  Instagram additionally needs either an HTTPS address or the small sign-in relay page — see
  [ONE_CLICK_CONNECT.md](ONE_CLICK_CONNECT.md).
- **Updating:** run the newer `ClipForge-Setup-<version>.exe`. Your projects, settings, accounts
  and secrets are kept.

## Uninstall

*Settings → Apps → CLIPFORGE → Uninstall* (or the Start menu entry). You're asked whether to delete
everything CLIPFORGE stored (projects, clips, settings, connected accounts and its Linux
environment). Answer *No* to keep it for a later re-install. Your `Videos\ClipForge` folder is
never deleted.

## How it works

CLIPFORGE is made of Linux services (database, workers, web app). On Windows they run in a
dedicated WSL 2 environment named **ClipForge**, separate from any other Linux you use, with the
free Docker Engine inside. Windows forwards `http://localhost:3000` to it. Data lives in
`%LOCALAPPDATA%\ClipForge\wsl` (a virtual disk). The installer only adds a `%USERPROFILE%\.wslconfig`
memory setting if you don't have that file yet.

Useful commands (PowerShell):

```powershell
wsl --list --verbose
```

```powershell
& "$env:LOCALAPPDATA\ClipForge\app\installer\windows\scripts\clipforge.ps1" status
```

```powershell
wsl -d ClipForge -- docker compose --project-directory /opt/clipforge logs --tail 100 api
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "Hardware virtualization looks switched off" / WSL can't start | Turn on *Intel VT-x* / *AMD-V (SVM)* in the BIOS/UEFI, then run setup again |
| Setup 0.2.0 stopped with "The term 'wsl.exe' is not recognized" right after turning on the Windows features | A bug in that version (it looked for WSL as a 32-bit program). Run **ClipForge-Setup-0.2.1.exe** or later: the features already turned on are kept; restart when it asks and it continues after you sign in |
| Setup keeps asking to restart | Install *Windows Subsystem for Linux* from the Microsoft Store (or run Windows Update), restart, run setup again |
| Port 3000 already in use | Another program uses it; quit that program, or change `CLIPFORGE_HTTP_PORT` in `/opt/clipforge/.env` inside the ClipForge environment and use that port |
| CLIPFORGE didn't start in time | Use the CLIPFORGE shortcut again; the log is `clipforge.log` in the installation folder |
| Company network with a TLS-inspecting proxy | Setup's downloads use Windows' settings; builds inside Linux may need the proxy's CA — see [DEPLOYMENT.md](DEPLOYMENT.md) |
| Live streaming from OBS on the same PC | WSL forwards TCP but not UDP (SRT) to `localhost`; use a server for live ingest, or turn on WSL's mirrored networking (`networkingMode=mirrored` in `.wslconfig`, Windows 11 22H2+) |

## Building the installer

On Linux with NSIS 3 (`sudo apt-get install -y nsis`):

```bash
installer/windows/build.sh
```

It packs the files tracked by git (plus `config/oauth-apps.json` if you created one to build
one-click Connect in) into `installer/windows/build/ClipForge-Setup-<version>.exe` and prints its
SHA-256. The *Windows installer* GitHub workflow builds the same file on every change to the
installer and checks the scripts with Windows PowerShell 5.1.
