# CLIPFORGE for Windows — installer

`ClipForge-Setup-0.2.1.exe` installs CLIPFORGE on Windows 10 (version 2004 or later) and
Windows 11. Download it, double-click it and follow the steps — see [docs/WINDOWS.md](../../docs/WINDOWS.md).

- Built from commit `adb796d` with `installer/windows/build.sh` (NSIS 3.09).
- SHA-256: see `ClipForge-Setup-0.2.1.exe.sha256`.
- Not code-signed: Windows SmartScreen may show "Windows protected your PC" — choose
  *More info → Run anyway*. Signing it needs a code-signing certificate from the publisher.
- Rebuild after changing CLIPFORGE: `installer/windows/build.sh` (on Linux with `nsis` installed).

## Changes in 0.2.1

- Fixed: 0.2.0 stopped right after turning on the Windows features with
  "The term 'wsl.exe' is not recognized". Setup now runs its steps in 64-bit PowerShell and finds
  WSL wherever Windows keeps it. If 0.2.0 already turned the features on, 0.2.1 sees that, asks
  for the restart, and continues after you sign in again.
- Fixed: a step could stop early when a program printed progress messages to its error output.
- After the restart, setup continues through a one-time sign-in task that has administrator rights.
- Running setup again offers the folder you installed into before.
- Folder names with characters such as `'`, `&` or `$` are passed to Linux unchanged.
- *Open CLIPFORGE now* opens your browser without administrator rights.
