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
| `Make clip 2 5 seconds shorter` / `cut the first 3 seconds of clip 1` | changes the clip and renders it again |
| `Use karaoke captions on all clips` / `remove the captions from clip 3` | new caption style (or none), re-rendered |
| `Rename clip 1 to "Stop undercharging"` | new title on the clip and its unpublished posts |
| `Clip 2 starts too slow, fix it` / `how can I improve these clips?` | the **assistant** (needs an AI model, see *Local AI*) decides the change, or explains each clip's weak points |

Every reply shows **What I did** (the steps taken) and cards: a production card with live
progress (*Checking access → Fetching source → Transcribing → Finding highlights → Rendering →
Ready*), approval cards with checkboxes, suggestions you can apply as defaults, and links. Under
the steps, the card shows the step running now with its own bar: while a video downloads, how much
has arrived, the speed and the time left (*120 MB of 260 MB · 5.2 MB/s · 35 s left*); while clips
render, how many are done.

**Attaching a video from your computer:** the paperclip uploads it in pieces of 8 MB with a
percentage (*45% · 820 MB of 1.8 GB*); the × cancels. A dropped connection resumes where it
stopped. Uploads of several GB work, up to the limit in `CLIPFORGE_MAX_UPLOAD_MB` (4 GB by default).

**The assistant.** Ask for changes to the clips of the project you're working on — shorter,
longer, a later start, another caption style, a new title, render again — or for feedback on
them. With an AI model connected (a free local one is enough, see *Local AI*), it reads your
request together with each clip's scores, length, captions and quality checks, decides what to
change and does it through the same steps as the buttons in My Videos; the reply says what it
changed and *What I did* shows which model understood it. Without a model, specific requests
(clip numbers, seconds, caption styles, quoted titles) still work, and *how can I improve…* lists
each clip's weakest scores. Settings it suggests for future videos are shown as a card for you
to apply. Viewers can ask; changing clips needs the editor role, pausing needs admin.

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

Open **Schedule** in the menu. It has everything in one place:

- **Ready to schedule** — every finished clip. Press **Schedule…** and pick the date and time (or
  a quick pick such as *Tomorrow 18:00*), or tick several clips, press **Schedule selected** and
  space them out (*one every hour/day/week*).
- **Upcoming posts** — what goes out when, grouped by day, with **Change** and cancel.
- **Recently posted** — what went out in the last two weeks.

You can also schedule from a clip's package in My Videos (**Schedule…**), while approving, or in
chat ("publish clip 1 tomorrow at 6pm"). Times are in your own time zone.

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
- **Schedule** and **Dashboard → Upcoming posts** list everything scheduled, and how each one goes out.
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

### Local AI on this PC

The card at the top of AI Connections sets up a free AI model that runs on your own computer, so
Brain Chat can understand anything you ask and the assistant can improve your clips — no API key,
no cost, nothing leaves the PC.

1. **Get Ollama** (free, for Windows, macOS and Linux) and open it; it runs in the background.
2. **Check again**: the card shows *Ollama is running* and its models.
3. **Download** a model: *Qwen 2.5 3B* (1.9 GB, about 4 GB of memory) is recommended for most PCs;
   *Qwen 2.5 7B* answers better but needs about 8 GB of free memory. The card shows the download's
   progress (size, speed, time left); when it finishes the model is connected by itself.

LM Studio, Jan and a llama.cpp server are found too (*Use it*). On the Windows app a running
Ollama that already has a model is connected automatically the first time Brain Chat needs one.
Docker installs: start the optional server with `docker compose --profile local-ai up -d`
(`CLIPFORGE_OLLAMA_URL` points elsewhere if you run Ollama yourself).

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
| Clips & editing | Clips per video, length range, formats, caption style and languages, burned-in captions, silence removal, zoom, loudness, licensed music; **Shorts** switches (all off until you turn them on): score and fix the first 3 seconds, hold weak openings for review, make clips loop, re-hook at 20–25 s, kinetic captions, save each clip's transcript, 2–3 title and first-frame options, check against my last posts, early-retention feedback, length by kind of clip, other openings, A/B tests |
| Brand | Channel name, accent colour (highlighted caption word), watermark and its position |
| Live streams | Create live sessions and get the SRT address for OBS |
| Media library | Music, sound effects, B-roll, logos and original masters — only files you confirm you have the rights to |
| Automation & safety | Pause, emergency stop, per-integration switches, storage clean-up |
| Account & security | Password, people and roles (owner), activity log (admins) |
| Your data | Export everything, export memory, back up now, delete all content (owner) |
| Advanced | Technical details and system diagnostics |

### Shorts: what each switch does

- **Score the first 3 seconds / Fix weak openings / Hold for review** — cuts greetings and slow
  starts, lands the first line within 1.5 s, puts bold text from the clip's own words on frame one;
  a clip whose opening stays weak is never published automatically.
- **Make clips loop** — ends on a sentence that flows back into the start, or blends the last
  frames into the first.
- **Re-hook at 20–25 seconds** — where a thought finishes (the moment people swipe away), a quick
  zoom and a text line: a preview of the speaker's next line, or a neutral line such as "Now the
  important part". Nothing is moved and nothing is claimed that wasn't said.
- **Kinetic captions** — big captions a few words at a time; the strongest 1–3 words of each line
  (numbers, the topic, strong words) light up in your accent colour and pop as they're said.
- **Save each clip's transcript** — a "Transcript" (.txt) download on each clip, kept with it.
- **Make 2–3 title and first-frame options** — each clip lists its options, best first (title
  30–50 characters, keyword first; 1–2 big words on frame one; description; hashtags). The best is
  used; press **Use** on another to switch the title, description and hashtags of posts that
  haven't gone out yet. The words on frame one are part of the video, so they change only when the
  clip is re-rendered.
- **Check against my last posts before publishing** — if a clip is made the same way as your recent
  posts (same kind of opening, structure, captions and title shape — five times in a row — or nearly
  the same title), another title option is used when that fixes it; otherwise it waits for you.
- **Early-retention feedback** — each posted clip shows how many viewers stayed (viewed vs. swiped
  away, still watching at 3 s, share watched, replays) and a flag with a suggestion when it flopped.
  YouTube's numbers arrive after 1–3 days; "viewed vs. swiped away" only exists in YouTube Studio:
  export it there (Analytics → Content → Shorts → Export) and use **Import CSV** under Learning
  Center → Insights → Early retention. Nothing is deleted or re-edited for you.
- **Rank title options by what kept viewers** — title styles that held viewers better on your earlier
  posts move up a little (after 3+ posts of a style).
- **Pick the length from the kind of clip** — a quick tip aims for about 25 s, a list or story gets
  more room; your own retention numbers can move it. A clip still ends only at a finished thought.
- **Find other openings for weak clips** — lists better starting points (a stronger line a little
  later, or a setup line just before); press **Use** on the clip to re-render from there, or let the
  best one be used automatically.
- **A/B test** — titles, first-frame text, or text on frame one vs none. New clips alternate between
  the two; after enough clips with retention numbers on each side (5 by default), the Learning Center
  shows which kept viewers better — or that there's no clear difference yet.
- **Cost** — each clip shows what its AI calls cost (local models are free) and how long it took to
  make; the Learning Center shows the cost per published Short.

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
