# One-click YouTube and Instagram connection

In **Settings → Accounts** each platform has a **Connect** button. You sign in on Google's or
Instagram's own page in a small pop-up; it closes by itself and the account shows as connected.
CLIPFORGE never sees your password; it stores only encrypted access tokens.

Google and Instagram only allow sign-in for apps registered with them — a *client ID* (Google)
or an *app ID* (Instagram) with its secret. That registration happens **once**:

- **For everyone using your copy** (you distribute CLIPFORGE to others, or install it on several
  PCs): register the apps once and build them in — see [Build it in](#build-it-in). Your users
  then see only the Connect button.
- **Just for your own installation**: click Connect; CLIPFORGE opens a guided set-up with the exact
  steps and the values to copy, and saves what you paste (encrypted, used immediately — no files
  to edit, no restart).

Existing installations that set `CLIPFORGE_GOOGLE_CLIENT_ID`/`_SECRET` and
`CLIPFORGE_META_APP_ID`/`_SECRET` in `.env` keep working. Order of precedence: saved in
CLIPFORGE → built in → environment.

## Google (YouTube and Google Drive)

1. [Create a Google Cloud project](https://console.cloud.google.com/projectcreate).
2. Enable **YouTube Data API v3**, **YouTube Analytics API** and (optional) **Google Drive API**.
3. [Configure the consent screen](https://console.cloud.google.com/auth/branding): *External*; add
   the scopes when asked (youtube.upload, youtube.force-ssl, youtube.readonly,
   yt-analytics.readonly, drive.readonly). While the app is in *Testing*, add every Google account
   that will connect as a *test user* (up to 100).
4. [Create an OAuth client](https://console.cloud.google.com/auth/clients/create):
   - **Desktop app** — for CLIPFORGE running on the user's own computer (the Windows installer,
     or any install opened at `http://localhost`). Nothing else to enter: Google returns to the
     address the user's CLIPFORGE runs on (`http://localhost:<port>`). This is the type to build in.
   - **Web application** — for a server with its own address. Add the authorised redirect URI
     `https://your.domain/api/connections/google/callback` (shown in the set-up dialog).
5. Paste the client ID and secret into CLIPFORGE's set-up (it checks them with Google straight
   away), or put them into `oauth-apps.json`.

**Going beyond 100 users.** Apps in *Testing* allow 100 test users, and their refresh tokens
expire after 7 days (users then reconnect). For a public app, submit it for Google's
[OAuth verification](https://support.google.com/cloud/answer/13463073); YouTube uploads from
unaudited projects are also kept *private* until the project passes YouTube's
[API compliance audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits).
Only the app owner can do these.

A Desktop-app client secret is not confidential by Google's design (it ships inside installed
apps); security comes from the user's consent and PKCE, which CLIPFORGE always uses.

## Instagram

Instagram uses Meta's *Instagram API with Instagram login* (Business or Creator accounts).

1. [Create a Meta app](https://developers.facebook.com/apps/creation/) with the use case *Manage
   messaging & content on Instagram*.
2. In the app: **Instagram → API setup with Instagram login**. Add the Instagram accounts that
   will connect as *Instagram testers* while the app is in development (each accepts the invite
   in Instagram → Settings → Website permissions → Apps and websites).
3. **Business login settings → OAuth redirect URIs.** Instagram only returns to **https://**
   addresses:
   - On a server with HTTPS: `https://your.domain/api/connections/instagram/callback`.
   - On a PC (`http://localhost`): publish the **sign-in relay page**
     [`deploy/oauth-relay/instagram.html`](../deploy/oauth-relay/instagram.html) on any HTTPS host
     and register *its* address. The page is static: it reads the sign-in result and forwards it
     to CLIPFORGE **on the same computer** (`http://localhost:<port>` or `127.0.0.1`) — the port
     comes from the sign-in request CLIPFORGE made; any other destination is refused. It stores
     nothing and makes no network requests. Free hosting that works: GitHub Pages (put the file
     in a public repository's `docs/` folder or a `gh-pages` branch and enable Pages), Netlify,
     Cloudflare Pages.
4. Copy the **Instagram app ID** and **Instagram app secret** into CLIPFORGE's set-up (plus the
   relay page address), or into `oauth-apps.json`.
5. For other people's accounts, the app needs Meta's *App Review* for
   `instagram_business_basic`, `instagram_business_content_publish` and
   `instagram_business_manage_insights` and must be switched to *Live*.

## Build it in

Create `config/oauth-apps.json` (start from `config/oauth-apps.example.json`):

```json
{
  "google": { "client_id": "…apps.googleusercontent.com", "client_secret": "GOCSPX-…", "kind": "desktop" },
  "instagram": { "app_id": "…", "app_secret": "…", "relay_url": "https://you.github.io/clipforge-signin/instagram.html" }
}
```

- **Docker installs** read it from `config/` (mounted read-only at `/config`); no restart of
  anything else is needed beyond `docker compose up -d`.
- **The Windows installer** packs it in when you build it (`installer/windows/build.sh`), so every
  PC installed from that `setup.exe` has one-click Connect.
- The file is git-ignored. Don't commit it.

**What building in means for secrets.** Anyone with the installer can read what's inside it.
That's expected for the Google *Desktop app* secret. The Meta app secret, however, is meant to be
confidential: someone who extracts it can make requests *as your app* (they still cannot reach
anyone's Instagram account without that person signing in). If that's not acceptable, leave
Instagram out of `oauth-apps.json` — each user then registers their own Meta app once with the
guided set-up — or run the code-for-token exchange on a server you control.

## Checking the set-up

- Settings → Accounts shows where each app comes from (*built into this copy*, *set up in
  CLIPFORGE*, *from the server settings*) and lets admins change or remove a saved one.
- `docker compose exec api clipforge doctor` lists the Google and Instagram apps and their type.
- Sign-in errors come back to the Settings page with Google's or Instagram's reason.
