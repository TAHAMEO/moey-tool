# API setup: YouTube, Instagram and AI providers

CLIPFORGE talks to platforms only through their official APIs, using apps that **you** own.
This is a one-time setup. Values go into `.env` (then `docker compose up -d`); AI keys are
entered in the app under **AI Connections** and stored encrypted.

Your public address matters: OAuth redirect URIs are derived from `CLIPFORGE_PUBLIC_BASE_URL`.
Google and Meta require **HTTPS** redirect URIs except for `http://localhost`, so set up HTTPS
first (see [DEPLOYMENT.md](DEPLOYMENT.md)) if CLIPFORGE runs on a server.

---

## 1. YouTube and Google Drive (Google Cloud)

### Create the project and enable APIs

1. Open the Google Cloud Console and create a project (e.g. *clipforge*).
2. **APIs & Services → Library**, enable:
   - **YouTube Data API v3** (uploads, captions, thumbnails, playlists, video details)
   - **YouTube Analytics API** (watch time, retention, subscribers gained)
   - **Google Drive API** — optional, only if CLIPFORGE should fetch your original video files from Drive

### OAuth consent screen

3. Configure the consent screen (Google Auth Platform): user type **External** (or *Internal* if
   you use Google Workspace and only your organisation's accounts will connect).
4. Add these scopes (they are exactly what CLIPFORGE requests):

   | Scope | Why |
   |---|---|
   | `https://www.googleapis.com/auth/youtube.upload` | upload videos |
   | `https://www.googleapis.com/auth/youtube.force-ssl` | caption tracks, thumbnails, playlists |
   | `https://www.googleapis.com/auth/youtube.readonly` | your channel and video details |
   | `https://www.googleapis.com/auth/yt-analytics.readonly` | analytics for your videos |
   | `https://www.googleapis.com/auth/drive.readonly` | *(optional)* read your original files in Drive |

5. While the app is in **Testing**, add your Google account under *Test users*.

> **Testing mode caveat:** Google expires refresh tokens of apps in *Testing* after 7 days, so
> CLIPFORGE would ask you to reconnect weekly. For permanent connections set the publishing
> status to **In production**. Unverified apps show a "Google hasn't verified this app" screen
> (choose *Advanced → continue*); that's acceptable for your own channel. YouTube and Drive scopes
> are *sensitive/restricted*, so Google's verification is needed before other people's accounts
> can connect.

### OAuth client

6. **Credentials → Create credentials → OAuth client ID**, type **Web application**.
7. Authorised redirect URI — exactly (no trailing slash):
   ```
   <CLIPFORGE_PUBLIC_BASE_URL>/api/connections/google/callback
   ```
   e.g. `https://clips.example.com/api/connections/google/callback` or
   `http://localhost:3000/api/connections/google/callback`.
8. Put the client ID and secret into `.env`:
   ```
   CLIPFORGE_GOOGLE_CLIENT_ID=…apps.googleusercontent.com
   CLIPFORGE_GOOGLE_CLIENT_SECRET=…
   ```

### Optional API key

9. **Create credentials → API key**, restrict it to *YouTube Data API v3*, and set
   `CLIPFORGE_YOUTUBE_API_KEY`. It is only used to read public details of videos you don't own;
   without it CLIPFORGE falls back to YouTube's public oEmbed endpoint.

### Connect

10. Restart (`docker compose up -d`), then **Settings → Accounts → YouTube → Connect** (and
    *Google Drive → Connect* if wanted). You sign in on Google's page; CLIPFORGE stores only the
    encrypted tokens.

### Limits you should know

- **Public uploads need a YouTube API audit.** Google restricts videos uploaded through
  *unverified* API projects (created after 28 July 2020) to **private** visibility until the
  project passes YouTube's API compliance audit. CLIPFORGE uploads as *private* by default
  (Settings → Publishing); request the audit through Google's YouTube API Services form before
  switching to public uploads.
- **Daily quota.** Projects start with 10,000 quota units per day. With Google's published costs
  an upload with caption track and thumbnail uses about 2,100 units — roughly four Shorts a day.
  CLIPFORGE tracks usage (Pacific-time days, like Google) and reschedules uploads that would
  exceed the quota instead of failing. Request more via the quota extension form.

---

## 2. Instagram (Meta)

Publishing uses the **Instagram API with Instagram Login**. Requirements: an Instagram
**professional account** (Business or Creator). A Facebook Page is not needed.

1. At developers.facebook.com create an app (type *Business*) and add the **Instagram** product,
   then open **API setup with Instagram business login**.
2. Under *Set up Instagram business login → Business login settings*, add the OAuth redirect URI:
   ```
   <CLIPFORGE_PUBLIC_BASE_URL>/api/connections/instagram/callback
   ```
3. Copy the **Instagram app ID** and **Instagram app secret** shown on that page (not the Meta
   app ID at the top of the dashboard) into `.env`:
   ```
   CLIPFORGE_META_APP_ID=<Instagram app ID>
   CLIPFORGE_META_APP_SECRET=<Instagram app secret>
   ```
4. Permissions CLIPFORGE requests (nothing else — no comments or messages):
   `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_insights`.
5. While the app is in **development mode**, only accounts with a role on the app can connect:
   **App roles → Roles → Add Instagram tester**, then accept the invite in the Instagram app
   (*Settings → Website permissions / Apps and websites → Tester invites*). Connecting other
   people's accounts requires Meta App Review for these permissions and business verification.
6. Restart, then **Settings → Accounts → Instagram → Connect**.

Notes:

- Reels are uploaded with Instagram's resumable upload, so your media doesn't need to be on a
  public URL.
- Instagram enforces a rolling 24-hour publishing limit per account. CLIPFORGE reads the current
  allowance from the API before each post, and your own daily limit applies too
  (Settings → Publishing).
- Instagram has no separate caption-file upload: Reels always get burned-in captions.
- Long-lived tokens are refreshed automatically before they expire.

---

## 3. AI providers

Add these in **AI Connections → Add connection**. Keys are validated with a real request
(model listing plus a one-word reply) and stored encrypted; only the last four characters are
ever shown again.

| Provider | Where to get a key | Notes |
|---|---|---|
| OpenAI | platform.openai.com → API keys | |
| Google Gemini | Google AI Studio → Get API key | |
| Anthropic Claude | console.anthropic.com → API keys | |
| OpenAI-compatible | your server | Any endpoint with `/v1/chat/completions` (vLLM, LM Studio, llama.cpp server, gateways); base URL ends in `/v1` |
| Ollama (local) | none | Free, private. Run `docker compose --profile local-ai up -d`, add it with `http://ollama:11434/v1` |

Local models with Ollama: pull models from the Ollama library
(`docker compose exec ollama ollama pull qwen2.5:3b`) or import a GGUF file you already have
(`ollama create <name> -f Modelfile` with `FROM /path/model.gguf`). CLIPFORGE gives Ollama a
context window sized to each request (Ollama's 4k default is too small for memory extraction).
On a CPU-only server, small models (1–3B) answer chat questions in seconds but take a minute or
more for long tasks; those run in the background.

Spending limits (daily, monthly, per video), *prefer local models* and *allow paid AI services*
are on the same page. Prices for known models come from a built-in list; set exact prices per
model under *Advanced settings* — calls priced with estimates are flagged in the usage table.
