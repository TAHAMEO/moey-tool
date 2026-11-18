# Source access: what CLIPFORGE can and cannot fetch

Every link you paste goes through the **Source Access Resolver** before anything else happens.
It works out which *authorised* ways exist to get the source, picks the best one, and records
exactly what it checked. The result decides what CLIPFORGE can honestly do with the source.

CLIPFORGE never bypasses DRM, logins, paywalls, private-account restrictions or other platform
protections, never uses undocumented platform APIs, and does not use third-party downloaders
(such as yt-dlp) — even where they would technically work. If the best authorised method only
gives the words, it says so and works with the words; it never pretends it saw the video.

## The 12 access states

| State | Meaning | What CLIPFORGE does |
|---|---|---|
| `FULL_MEDIA_ACCESS` | Audio and video frames are available through an authorised method | Full analysis (speech, scenes, faces, audio events), rendering, captions, publishing |
| `REPLAY_ACCESSIBLE` | A livestream has ended and its recording is available | Processes the recording like a normal video |
| `AUTHORIZED_LIVE_FEED` | Your encoder is sending an authenticated live feed to CLIPFORGE | Transcribes and scores the stream in near real time; builds the replay when it ends |
| `TRANSCRIPT_ONLY` | Only the spoken words are available (e.g. the caption track of your own video) | Finds candidate moments from the words alone; **no visual analysis, no rendering** — the project says so |
| `METADATA_ONLY` | Only public details (title, channel, duration, thumbnail) | Reports what it would need; offers the simplest alternative |
| `LOGIN_REQUIRED` | An account connection is needed before anything can be checked | Asks you to connect the account (Settings → Accounts) |
| `PERMISSION_REQUIRED` | The platform only allows access to the account owner's own content | Explains; suggests asking the creator for permission and the original file |
| `PLATFORM_APPROVAL_REQUIRED` | The needed API feature requires the platform to approve this app | Explains which approval is needed |
| `SOURCE_PRIVATE` | The source is private | Stops; nothing is fetched |
| `SOURCE_UNAVAILABLE` | Removed, not found, or temporarily unreachable | Temporary network problems are retried automatically; otherwise stops |
| `UNSUPPORTED_SOURCE` | Not a supported kind of link (or not a video) | Explains what is supported |
| `ACCESS_DENIED` | Access was refused (blocked by safety rules, the platform withheld the file, or you switched the integration off) | Explains the exact reason |

Every project shows its access state, the reasons, the alternatives and — under *Advanced
diagnostics* — every check the resolver made.

## By source

### YouTube video (long-form or Short)

| Situation | Result |
|---|---|
| Your own video and its original file is in your **media library** (Settings → Media library, kind *master*) or your connected **Google Drive** — matched by name **and** length | `FULL_MEDIA_ACCESS` |
| Your own video, no original file found, captions exist | `TRANSCRIPT_ONLY` (downloaded through the YouTube API with your channel's authorisation) |
| Someone else's public video | `METADATA_ONLY` — YouTube's official API does not provide video downloads, and caption downloads require the video owner's authorisation |
| Private / removed video | `SOURCE_PRIVATE` / `SOURCE_UNAVAILABLE` |

Simplest alternatives offered: connect Google Drive or add the original to the media library
(your own videos), upload the file, or ask the creator for permission and the original.

### YouTube livestream

| Situation | Result |
|---|---|
| Scheduled or currently live, no live feed connected | Project waits (*Waiting for live source*); CLIPFORGE does **not** claim to be watching the stream |
| A live ingest session is linked to the stream and your encoder is sending | `AUTHORIZED_LIVE_FEED` — clipping while live |
| Ended, and the recording is in your media library / Drive | `REPLAY_ACCESSIBLE` |
| Ended, no recording available to CLIPFORGE | Same as a video you don't have the file for (`METADATA_ONLY`) |

YouTube's API does not offer a live media feed to third-party apps. To clip a stream while it is
live, create a live session in **Settings → Live streams** and add the SRT address to OBS (or
your encoder) as a second output. The feed is encrypted with a per-session passphrase.

### Instagram Reel or video post

| Situation | Result |
|---|---|
| No Instagram account connected | `LOGIN_REQUIRED` |
| Your connected professional account posted it | `FULL_MEDIA_ACCESS` (media URL from the official API; downloads only from Instagram's own CDN hosts) |
| Instagram withholds the media file (e.g. copyrighted audio) | `ACCESS_DENIED`, with the reason |
| Another account's Reel | `PERMISSION_REQUIRED` (or `METADATA_ONLY` if oEmbed details are available) |
| Not a video post | `UNSUPPORTED_SOURCE` |

### Google Drive link and direct file links

A Drive link is fetched through your connected Drive account (read-only scope). A direct link
(`https://…/file.mp4`) is checked with a safe `HEAD` request and downloaded only if it is a
public address, an allowed media type and within the size limit.

### Uploads

Uploading a file is an optional fallback for when no authorised link method exists. You confirm
you have the rights to the file; it is validated (it must really be audio/video) and
de-duplicated by checksum.

## Safety rules applied to every fetch

- Only `http`/`https`; credentials in URLs are rejected.
- Hostnames are resolved and **every** address checked: private, loopback, link-local and cloud
  metadata addresses are blocked (server-side request forgery protection). The checked address is
  pinned for the connection, so DNS can't be switched between check and use.
- Redirects are followed manually and each hop is re-checked.
- Content type, size (`CLIPFORGE_MAX_DOWNLOAD_MB`) and duration (`CLIPFORGE_MAX_SOURCE_DURATION_HOURS`)
  limits; downloads that exceed them are aborted and deleted.
- Probing, decoding, transcription and rendering run only in the media worker, which holds no
  secrets; FFmpeg there is restricted to local file protocols and a whitelist of container formats.
  The secret-holding worker only downloads bytes (checked by file signature, never decoded) and,
  for live sessions, receives the encrypted SRT stream and cuts it into segments without decoding.
- Titles, descriptions, captions, transcripts and comments are treated as untrusted text:
  instructions embedded in them can't change what the Brain does.
- Per-source switches in **Settings → Automation & safety** turn fetching from YouTube,
  Instagram, Google Drive or direct links off entirely.
