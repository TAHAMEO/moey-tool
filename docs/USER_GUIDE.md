# User guide

CLIPFORGE has six pages: **Brain Chat**, **Dashboard**, **My Videos**, **AI Connections**,
**Learning Center** and **Settings**. Almost everything can be done from Brain Chat; the other
pages show the details and let you fine-tune.

## First steps

1. Open CLIPFORGE and create the owner account (first visit only).
2. The Dashboard shows a short **Get set up** list: install the speech model (server command),
   connect an AI service (optional), connect YouTube and/or Instagram, make your first clips.
3. Paste a link in Brain Chat.

## Brain Chat

Type what you want in plain language. Examples:

| You say | What happens |
|---|---|
| `Make 3 Shorts under 40 seconds from https://youtu.be/…` | checks access, then finds, edits and packages 3 clips |
| `Turn this livestream into Reels https://www.youtube.com/live/…` | waits for an authorised live feed or the replay (see *Live streams*) |
| `Clips about pricing from https://www.instagram.com/reel/…` | focuses the selection on that topic |
| *attach a file* + `make 2 clips from this` | uses your upload (optional fallback) |
| `How's it going?` | status of running work |
| `How much have we spent?` | AI spend today and this month against your budgets |
| `How did my last videos do?` | performance from real platform analytics |
| `What have you learned?` | findings and running experiments |
| `Remember that my audience is mostly small business owners` | saved permanently; used in future work |
| `Forget that I like boxed captions` | shows matching memories to delete |
| `Approve clip 1 and 2` / `publish the best one to YouTube` | approval and publishing |
| `Publish clip 2 tomorrow at 6pm` / `post them every day at 18:00 starting Friday` | schedules posts for exact times (your browser's time zone) |
| `Make 3 Shorts from https://youtu.be/… and post them tomorrow at 9am` | makes the clips and, once approved, schedules them |
| `Process all videos in the Podcast folder: 2 shorts each, post one a day at 6pm` | watches that inbox folder with this recipe (see *Processing a whole folder*) |
| `Hold off posting for now` / `resume publishing` | switches publishing off/on (processing continues) |
| `Pause everything` / `resume everything` / `emergency stop` | automation controls (admins) |
| `Give me a tip for Shorts titles` | answered by your AI connection and saved as knowledge |

Every reply shows **What I did** (the steps taken) and cards: a production card with live
progress (*Checking access → Fetching source → Transcribing → Finding highlights → Rendering →
Ready*), approval cards with checkboxes, suggestions you can apply as defaults, and links.

If a link can't be fully accessed, the card says exactly why and offers the simplest
alternatives (connect your account, add the original file, upload it, …). CLIPFORGE never
pretends: if it only had the transcript, the project says so and no video is rendered. See
[SOURCE_ACCESS.md](SOURCE_ACCESS.md).

## My Videos

Every source you've sent, newest first, filterable by *In progress*, *Ready*, *Published* and
*Needs attention*. Open one to see:

- **Access** — the access state, reasons and alternatives.
- **Clips** — a player per format (9:16, 1:1, 16:9), the potential score with ten criteria
  (hook, clarity, emotion, novelty, information, completeness, visual, audio, relevance, platform
  fit) and the reasons it was chosen. Scores estimate potential from content signals; they are not
  view predictions.
- **Quality** — the automatic checks (format and codecs, resolution, frame rate, duration, file
  size, loudness and true peak, black frames) and
  captions to review: words the recogniser was unsure about or that are sensitive are listed with
  their time, so you can check before publishing.
- **Downloads** — MP4, SRT and VTT.
- **Adjust** — change the start/end or the caption style and re-render.
- **Packages** — the YouTube title, description, tags and visibility, and the Instagram caption
  and hashtags. Edit, save, then **Publish…** (now) or **Schedule…** (an exact date and time —
  see *Scheduling* below). A video is marked *published* only after the platform confirms it;
  uploading the same file twice is blocked.
- **Results** — views, likes, comments and more as the platform reports them. Metrics a platform
  doesn't provide are listed as unavailable, never shown as zero.
- **Advanced diagnostics** — every access check and the job's timeline, shown when *Settings →
  Advanced → Show technical details* is on. Errors and the *Retry* button (which resumes from the
  last completed step) are always shown.

### Scheduling posts

Choose **Schedule…** on a package (or schedule while approving, or in chat) and pick the date
and time; times are in your own time zone.

- **Public YouTube videos** scheduled at least 15 minutes ahead are uploaded right away as
  *private* with that publish time, and **YouTube itself** makes them public — even if your
  computer is off by then. CLIPFORGE checks afterwards and only then marks them published.
- **Instagram** (its API cannot schedule) and **unlisted/private YouTube** videos are prepared
  about 10 minutes early and published by CLIPFORGE at the exact minute, so CLIPFORGE must be
  running then. If it wasn't (PC off), a post that would be more than 3 hours late (Settings →
  Publishing) waits for you instead: it shows *Missed its time* with **Pick a new time** and
  **Publish now**.
- **Change time**, **Publish now** and **Cancel schedule** are on the package. Edits saved before
  the time are used, also for videos already waiting on YouTube.
- Approvals can spread several clips out (*one per hour/day/week*); both platforms of one clip go
  out together. Daily limits are checked for the day you choose.
- **Dashboard → Upcoming posts** lists everything scheduled, and how each one goes out.
- *Hold publishing* keeps the times but nothing goes out (videos waiting on YouTube are switched
  back to private meanwhile); *emergency stop* cancels every schedule. *Pause* only stops
  CLIPFORGE's own work — posts already handed to YouTube still go live.

### Processing a whole folder

**My Videos → Process a folder** runs one recipe over many videos:

- **A folder on this device** — choose a folder; every video in it is uploaded and processed as
  soon as it arrives (keep the tab open while it uploads).
- **Watch a CLIPFORGE folder** — choose a folder in CLIPFORGE's inbox (on Windows
  `Videos\ClipForge`; on a server the `inbox` folder next to `docker-compose.yml`). Videos
  already in it (optional) and every video you put there later are processed automatically. A
  file is taken only once it has stopped changing, so copying a large file in is safe. Your files
  are never changed unless you ask CLIPFORGE to move finished ones into a `Processed` sub-folder.

The recipe: what to make (plain language, like a chat message), clips per video, platforms,
what happens when clips are ready (*Keep as drafts*, *Ask me first*, *Post automatically*),
YouTube visibility and **posting times** — e.g. one clip at 09:00 and one at 18:00 every day,
lined up across all videos of the batch and skipping days that already reach your daily limit.
Duplicate videos are skipped. Each batch shows its progress on My Videos, with *Show its videos*,
*Pause*, *Resume*, *Stop watching* and *Cancel*.

You can also just tell Brain Chat: *"Process all videos in the Podcast folder: make 2 shorts each
and post one every day at 6pm"* (add *automatically* to post without asking). If the folder doesn't
exist yet, it tells you where the inbox is and which folders are there.

### Links from other creators

With **Settings → Sources → Download public YouTube and Instagram videos** on (the default),
CLIPFORGE downloads videos anyone can watch without signing in (with yt-dlp) when it can't get
your own original file. It never signs in, uses cookies or fakes its location: private,
members-only, age-restricted, paid and DRM-protected videos are reported, not worked around, and
Instagram posts that need a sign-in say so. **Only use videos you own or have permission to
reuse.** Clips from another creator's video are never published automatically — you confirm
them, with a reminder about rights.

## AI Connections

Add OpenAI, Google Gemini, Anthropic, any OpenAI-compatible endpoint or a local Ollama server.
Each connection is tested when added (it lists the models and asks for a one-word reply).

- **On/Off** per connection; **Test** re-checks it; **Advanced settings** per model: use it or
  not, its role (*Best quality*, *Balanced*, *Fast & cheap*), exact prices, measured speed and
  reliability.
- **Routing & spending:** prefer local models, allow paid services, AI second look at clip
  choices, daily/monthly/per-video limits, and how many models give second opinions on important
  questions. Audio and video frames never leave your server; only text (your messages, relevant
  memories, transcript excerpts, titles) is sent to the AI services you connect.
- **Usage this month:** calls, tokens and cost per model.

CLIPFORGE picks a model per task automatically and moves to the next connection if one fails or
runs out of credit; a failing connection is paused briefly and retried later. Without any AI
connection CLIPFORGE still works: transcription, analysis, editing and captions are local, and
titles come from templates.

## Learning Center

- **Insights** — performance per platform (median views, engagement, best videos), *findings*
  (patterns in your results, labelled *Pattern (correlation)* until tested), and a table of every
  published video's latest numbers. *Analyse now* runs the analysis immediately (it also runs
  every 6 hours). Dismiss findings you disagree with.
- **Strategy & tests** — experiments (a share of new clips uses a candidate strategy chosen at
  random so the comparison is fair) and every strategy version; older versions can be made
  active again.
- **Memory** — everything CLIPFORGE remembers about you and its own notes. Search, correct,
  delete, add, or export everything as JSON.
- **Knowledge** — answers from AI models kept for reuse. Important questions get a second
  opinion from a *different* model; disagreements are shown and both answers can be compared.
  Mark answers correct or wrong.

## Settings

| Section | |
|---|---|
| Publishing | Ask me first / Publish automatically / Drafts only; platforms; time zone; late-post limit; daily limits; YouTube visibility, playlist, caption track; Instagram feed sharing; made-for-kids |
| Accounts | Connect or disconnect YouTube, Instagram and Google Drive with one click (sign-in on the platform's own page in a pop-up); a guided one-time set-up if this copy doesn't have its Google/Instagram app built in |
| Sources | Download public YouTube/Instagram videos (on/off) |
| Clips & editing | Clips per video, length range, formats, caption style and languages, burned-in captions, silence removal, zoom, loudness, licensed music |
| Brand | Channel name, accent colour (highlighted caption word), watermark and its position |
| Live streams | Create live sessions and get the SRT address for OBS |
| Media library | Music, sound effects, B-roll, logos and original masters — only files you confirm you have the rights to |
| Automation & safety | Pause, emergency stop, per-integration switches, storage clean-up |
| Account & security | Password, people and roles (owner), activity log (admins) |
| Your data | Export everything, export memory, back up now, delete all content (owner) |
| Advanced | Technical details and system diagnostics |

## Live streams

Platforms don't let apps watch a livestream, so CLIPFORGE receives the stream from your encoder:

1. Settings → Live streams → **New live session** (optionally paste your YouTube live link).
2. Copy the stream address (`srt://…`). It contains a secret passphrase — treat it like a password.
3. In OBS: *Settings → Stream → Service: Custom…*, paste the address as *Server*, leave
   *Stream Key* empty. To stream to YouTube at the same time, add CLIPFORGE as a second output.
4. Start streaming. CLIPFORGE transcribes and scores the stream as it arrives, survives short
   disconnections, and builds the replay when you stop.

## Roles

| Role | Can |
|---|---|
| Viewer | see everything, change nothing |
| Editor | process videos, edit clips and packages, publish, manage memory and the media library |
| Admin | also AI connections, accounts, settings, budgets and automation controls |
| Owner | also add people and delete all content |

## Questions

**Why does a YouTube link only show "Details only"?** Downloading public videos is switched off in
Settings → Sources, or the video isn't public (private, age-restricted, members-only). For your
own videos you can also add the original file to the media library or connect Google Drive.

**Why did an Instagram link say "sign-in required"?** Instagram shows many posts only to signed-in
users, and CLIPFORGE doesn't sign in to see other accounts' posts. If it's your Reel, connect your
Instagram account; otherwise upload the file.

**Do I have to keep my PC on for scheduled posts?** For public YouTube videos, no: YouTube
publishes them. For Instagram (and unlisted/private YouTube), yes — CLIPFORGE publishes those at
the time. On Windows, choose *Start CLIPFORGE when I sign in* in setup.

**Why are my uploads private on YouTube?** CLIPFORGE uploads as private by default, and Google keeps
uploads from unverified API projects private until the project passes YouTube's API audit
([API_SETUP.md](API_SETUP.md#limits-you-should-know)).

**Can I undo a strategy change?** Yes: Learning Center → Strategy & tests → *Make active* on any
earlier version.

**How do I stop everything immediately?** Say `emergency stop` in Brain Chat or use the button on
the Dashboard. It cancels running jobs and blocks publishing and external AI until you clear it.
