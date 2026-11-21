# CLIPFORGE for Windows — installer

`ClipForge-Setup-0.2.0.exe` installs CLIPFORGE on Windows 10 (version 2004 or later) and
Windows 11. Download it, double-click it and follow the steps — see [docs/WINDOWS.md](../../docs/WINDOWS.md).

- Built from commit `48afeed` with `installer/windows/build.sh` (NSIS 3.09).
- SHA-256: see `ClipForge-Setup-0.2.0.exe.sha256`.
- Not code-signed: Windows SmartScreen may show "Windows protected your PC" — choose
  *More info → Run anyway*. Signing it needs a code-signing certificate from the publisher.
- Rebuild after changing CLIPFORGE: `installer/windows/build.sh` (on Linux with `nsis` installed).
