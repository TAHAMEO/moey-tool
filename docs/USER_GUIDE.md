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
  and hashtags. Edit, save, then **Publish…**. A video is marked *published* only after the
  platform confirms it; uploading the same file twice is blocked.
- **Results** — views, likes, comments and more as the platform reports them. Metrics a platform
  doesn't provide are listed as unavailable, never shown as zero.
- **Advanced diagnostics** — every access check and the job's timeline, shown when *Settings →
  Advanced → Show technical details* is on. Errors and the *Retry* button (which resumes from the
  last completed step) are always shown.

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
| Publishing | Ask me first / Publish automatically / Drafts only; platforms; daily limits; YouTube visibility, playlist, caption track; Instagram feed sharing; made-for-kids |
| Accounts | Connect or disconnect YouTube, Instagram and Google Drive (OAuth on the platform's own page) |
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

**Why does a YouTube link only show "Details only"?** YouTube's official API doesn't offer video
downloads, and CLIPFORGE doesn't use unofficial downloaders. For your own videos, add the original
file to the media library or connect Google Drive; otherwise upload a file you have rights to.

**Why are my uploads private on YouTube?** CLIPFORGE uploads as private by default, and Google keeps
uploads from unverified API projects private until the project passes YouTube's API audit
([API_SETUP.md](API_SETUP.md#limits-you-should-know)).

**Can I undo a strategy change?** Yes: Learning Center → Strategy & tests → *Make active* on any
earlier version.

**How do I stop everything immediately?** Say `emergency stop` in Brain Chat or use the button on
the Dashboard. It cancels running jobs and blocks publishing and external AI until you clear it.
