# config/

Mounted read-only into the CLIPFORGE containers at `/config`.

* `oauth-apps.json` — optional. Google and Instagram app credentials built into this copy, so
  every user can connect YouTube and Instagram with one click instead of registering their own
  apps. Start from `oauth-apps.example.json`; the steps are in
  [docs/ONE_CLICK_CONNECT.md](../docs/ONE_CLICK_CONNECT.md). The real file is git-ignored:
  never commit it.
