# CLIPFORGE on Windows 10 and 11

A normal installer, `ClipForge-Setup-<version>.exe`, sets everything up: you click *Next*, wait,
and CLIPFORGE opens in your browser. Since version 0.3.0 CLIPFORGE runs as an **ordinary Windows
app** — no Docker, no Linux, no WSL, no administrator rights, and no sign-in page: it is your app
on your PC.

## Requirements

| | |
|---|---|
| Windows | Windows 11, or Windows 10 version 1809 or later — 64-bit, Intel or AMD processor (not ARM) |
| Memory | 4 GB minimum, 8 GB or more recommended (the speech model uses about 1 GB while it transcribes; local AI for Brain Chat about 4 GB more) |
| Disk | about 10 GB free, plus room for your videos |
| Internet | about 1.7 GB is downloaded during the first installation (480 MB of it is the speech model) |

## Install

1. Run **ClipForge-Setup-&lt;version&gt;.exe**. If Windows SmartScreen says "Windows protected
   your PC", choose *More info → Run anyway* (the installer isn't code-signed).
2. Choose the options: a desktop shortcut, and whether CLIPFORGE should **start when you sign in**
   (recommended if you schedule Instagram posts or watch folders — those need CLIPFORGE running).
3. Pick the folder (default `%LOCALAPPDATA%\ClipForge`), click **Install** and leave the PC on.
   The window shows each step:

   | Step | What happens | Time |
   |---|---|---|
   | 1 | checks Windows, memory and disk | seconds |
   | 2 | stops a running CLIPFORGE (and the older Linux-based version, see below) | seconds |
   | 3 | downloads Python and CLIPFORGE's components — its database (PostgreSQL), video tools (FFmpeg) and web server (Node.js) — about 1.2 GB, every file checked against its checksum | 5–15 min |
   | 4 | creates CLIPFORGE's settings and database | seconds |
   | 5 | downloads the AI models, including the 480 MB speech model (faster-whisper; progress shown) | 2–15 min |
   | 6 | starts CLIPFORGE and checks that it answers | under a minute |

4. On the last page, keep *Open CLIPFORGE now* ticked. CLIPFORGE opens at http://localhost:3000 —
   no account to create. Follow the *Get set up* list on the Dashboard.

The log is `install.log` in the installation folder. If anything fails, run setup again: it
continues where it stopped and keeps everything already done.

**Coming from version 0.2.x** (which ran in a Linux environment under WSL): setup stops the old
version and asks whether to delete it. Its projects and settings are not moved over; answer *No*
to keep it (stopped) for now. The old version's WSL features stay turned on; they do no harm.

## Everyday use

| Start menu → CLIPFORGE | |
|---|---|
| **CLIPFORGE** | starts it if needed (a small "Starting…" window, usually 10–30 seconds) and opens it in your browser |
| **Stop CLIPFORGE** | stops it and frees the memory it uses |
| **CLIPFORGE watched folders** | opens `Videos\ClipForge` |
| **Uninstall CLIPFORGE** | removes it (you choose whether your data is kept) |

- **Scheduling posts:** open **Schedule** in the menu. *Ready to schedule* lists every finished
  clip: pick a date and time (or tick several and space them out). *Upcoming posts* shows what goes
  out when, by day, where you can change a time or cancel. Public YouTube videos go out at their time
  even when the PC is off (YouTube publishes them). Instagram posts need CLIPFORGE running at their
  time — keep the PC on and CLIPFORGE started (or turn on *start when I sign in*). A post that is
  more than 3 hours late waits for you instead.
- **Watched folders:** make a folder inside `Videos\ClipForge` (for example `Podcast`), then in
  CLIPFORGE choose *My Videos → Process a folder → Watch a CLIPFORGE folder*. Every video you put
  in it is processed with the settings you chose. See the [user guide](USER_GUIDE.md#processing-a-whole-folder).
- **Connecting YouTube and Instagram:** *Settings → Accounts → Connect* — see
  [ONE_CLICK_CONNECT.md](ONE_CLICK_CONNECT.md).
- **Updating:** run the newer `ClipForge-Setup-<version>.exe`. Your projects, settings, accounts
  and keys are kept.

## Local AI (free, on this PC)

Brain Chat's assistant — "make clip 2 punchier", "why is clip 1 weak?", "use karaoke captions on all
clips" — works best with an AI model. A local one costs nothing and keeps everything on your PC:

1. Install **Ollama** for Windows from [ollama.com/download](https://ollama.com/download) (no
   administrator rights needed) and open it; it stays in the background (llama icon by the clock).
2. In CLIPFORGE open **AI Connections**. The *Local AI on this PC* card shows *Ollama is running*.
3. Click **Download** under *Qwen 2.5 3B* (1.9 GB; recommended). The card shows the progress; when
   it finishes the model is connected and Brain Chat uses it.

A running Ollama that already has a model is connected by itself the first time Brain Chat needs
one. LM Studio and Jan are found too. On a PC with 16 GB of memory or more, *Qwen 2.5 7B* gives
better answers. Without any model, the chat still does specific edits ("make clip 2 5 seconds
shorter") and everything else in CLIPFORGE works.

## Uninstall

*Settings → Apps → CLIPFORGE → Uninstall* (or the Start menu entry). You're asked whether to delete
everything CLIPFORGE stored (projects, clips, settings, connected accounts). Answer *No* to keep it
for a later re-install. Your `Videos\ClipForge` folder is never deleted.

## How it works

CLIPFORGE is made of a few programs that all run from its installation folder and listen on this PC
only (`127.0.0.1` — nothing is reachable from the network):

| Program | What it is |
|---|---|
| PostgreSQL 16 with pgvector | CLIPFORGE's database (from the `pgserver` package), on port 38101 |
| API and two job workers | CLIPFORGE itself (Python 3.12), the API on port 38100 |
| Web app | the user interface (Node.js), on http://localhost:3000 |

`runtime\venv\Scripts\pythonw.exe -m clipforge.cli desktop run` starts them in order (database,
schema update, API, workers, web app) and restarts one that crashes; *Stop CLIPFORGE* stops them
again. Everything they need came from `app\backend\uv.lock`, where every package is pinned by
version and SHA-256; `uv` itself was downloaded from PyPI and checked against its SHA-256.

The installation folder holds:

| Folder / file | |
|---|---|
| `app\` | CLIPFORGE (replaced by each update) |
| `runtime\` | Python and its packages (replaced by each update) |
| `data\` | your projects, clips, models and the database |
| `config.env` | settings for this PC, including the key that encrypts your connected accounts — readable only by you |
| `logs\` | one log per program, plus `install.log` |

Because only you use CLIPFORGE on this PC, it has no sign-in: the browser on this PC is the owner.
(Server installations — Docker, see [INSTALL.md](INSTALL.md) — keep sign-in and user roles.)

Useful commands (PowerShell):

```powershell
& "$env:LOCALAPPDATA\ClipForge\app\installer\windows\scripts\clipforge.ps1" status
```

```powershell
Get-Content "$env:LOCALAPPDATA\ClipForge\logs\api.log" -Tail 50
```

Extra settings (for example `CLIPFORGE_OPENAI_API_KEY=...`) can be added to `config.env`; they
apply after *Stop CLIPFORGE* and starting it again.

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 3000 already in use | Another program uses it (for example the older Linux-based CLIPFORGE: *Stop CLIPFORGE* from 0.2.x, or run setup again, which stops it). Or change `CLIPFORGE_WEB_PORT` in `config.env` |
| CLIPFORGE didn't start | Use the CLIPFORGE shortcut again; the logs are in the installation folder's `logs` |
| "Setup can't write to …" | An older version was installed there with administrator rights: run setup again and choose another folder, such as `%LOCALAPPDATA%\ClipForge` |
| Company network with a TLS-inspecting proxy | Setup's downloads use Windows' settings; if Python packages fail to download, set `UV_NATIVE_TLS=1` (uses Windows' certificate store) and run setup again |
| Speech model download failed or was slow | CLIPFORGE downloads it by itself the first time a video needs it (the job card shows *Downloading the speech model* with its progress) — or run setup again. It comes from its publisher on Hugging Face; if that is blocked, setup falls back to the 1.8 GB Vosk model from alphacephei.com |
| A project failed and *Retry* failed again at once (0.3.x) | Fixed in 0.4.0: *Retry from checkpoint* now runs the failed step again (for example transcription once the speech model is there) |
| Uploading a video from the PC never finished (0.3.x) | Fixed in 0.4.0: videos are sent in 8 MB pieces with a percentage; the web server cut single uploads off at 10 MB |
| Brain Chat says it has no AI model | Set up *Local AI* (below) or add a connection in *AI Connections* |

## Building the installer

On Linux with NSIS 3 and Node.js 22 (`sudo apt-get install -y nsis`):

```bash
installer/windows/build.sh
```

It packs CLIPFORGE's backend, these scripts and the docs (files tracked by git, plus
`config/oauth-apps.json` if you created one to build one-click Connect in) and the web app built as a
standalone Node.js server into `installer/windows/build/ClipForge-Setup-<version>.exe`, and prints its
SHA-256. The *Windows installer* GitHub workflow builds it on every change and installs it silently on
Windows Server 2022 and 2025: it checks that CLIPFORGE answers without a sign-in, stops and starts
again, updates in place and uninstalls.
