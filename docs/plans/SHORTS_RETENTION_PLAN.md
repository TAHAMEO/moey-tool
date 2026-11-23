# Shorts retention upgrade — plan

Status: **P0, P1, P2 and P3 implemented** — `clipforge/shorts/`, `clipforge/analytics/retention.py`,
switches in Settings → Clips & editing (all off until turned on). P0: hook scorer + reconstruction,
loop seam, review hold. P1: re-hook at 20–25 s, kinetic captions, clip transcript export, packaging
variants. P2: originality guardrail, early-retention feedback (YouTube retention report + engaged
views, CSV/manual import, flop flags, style ranking, learned title ranking). P3: adaptive length,
alternate openings for weak clips, A/B tests across clips, cost per clip / per published Short.

Owner's decisions (2026-11-22): weak-hook clips are **held for review** (manual publish still
possible); **no sentence re-ordering** unless "Open on the question" is switched on; **local AI
first** for hook text and titles (P0 itself uses no AI model — rules over the transcript).

What changed while building P0 (found by tests and a real clip):
- a text overlay never rescues a weak *spoken* opening (the fix-or-hold decision uses the spoken
  opening only);
- runway removal skips only throat-clearing (greetings, "today we're going to…", filler, silence,
  near-empty lines) — never a sentence with substance;
- loop shortening only at a real conclusion with a good loop score (it had cut 20 s of a 33 s clip);
- cuts land inside the pause, never into the next word;
- clips no longer fade in from black (frame one was a black frame);
- the text hook keeps every word in ≤ 3 lines, sized to the width, and sits in the half of the
  middle third away from the speaker's face.

What P1 does (and what was found building it):
- **Re-hook** (`shorts/retention.py`): exit points are sentence ends on the *edited* timeline
  between 15 and 30 s where a thought resolves — a concluding line ("that's why…"), a pause after
  it, a change of topic, or a next line that announces a new point. A topic change alone isn't
  enough. The best one (closest to 20–25 s) gets a 1.5 s punch-in zoom from the exit point and a
  text line from 0.6 s before it: a 2–5 word preview of the speaker's *next* line when that reads
  as a hook, otherwise a neutral framing line ("NOW THE IMPORTANT PART", "STAY FOR THIS ONE") that
  claims nothing. No sentence is moved. Clips under 20 s get none.
- **Kinetic captions** (preset `kinetic` in `vendor/autoclip/captions.py`, words in
  `shorts/captions.py`): up to 3 words per line, 7.4 % of the height, heavy outline; 1 power word
  per line of ≤ 3 words, 2 for longer (never more than 3) in the accent colour, popping when spoken.
  Power = numbers, emotional words, hook words, the clip's focus keyword, and words that are rare in
  the rest of the video (IDF over the source's sentences). Words only said in channel talk
  ("subscribe", "this video") never light up.
- **Transcript** (`seo_transcript`): `clipNN_transcript.txt` (one sentence per line) stored with
  the clip as a `transcript` asset (kept by clean-up, downloadable); the clip's `transcript_text`
  now follows the final cut.
- **Packaging** (`shorts/packaging.py`): 2–3 variants ranked by length (30–50), keyword first,
  curiosity, promise and readability. Titles are the clip's own strongest lines behind the focus
  keyword ("Pricing: The biggest mistake freelancers make"); the only added words are framing words
  (why/how/here's). A model-written title is one candidate and is **dropped if it uses a word the
  speaker never said**. First-frame text: 1–2 words *next to each other* in a strong line ("NEVER
  COMPETE", "BIGGEST MISTAKE"), shown on frame one at up to 11 % of the height — readable at 200 px
  wide; it is the thumbnail on Shorts. Description = title + the most keyword-rich run of sentences
  + hashtags (YouTube: `#shorts` + 2 topic tags; Instagram: up to 4 + `#reels`). The best variant is
  used; the clip page lists all of them with a one-click "Use".
- Found on a real clip: ASR mistakes ("number one wanted") make some lines poor title material — the
  ranking can't know a word was misheard, so the options list matters; channel talk inside a clip
  was leaking into keywords, highlights and hashtags (now filtered); the first keyword picker chose
  "raising" over "price" (now counts word forms together and uses "let's talk about …").

What P2 does:
- **Originality guardrail** (`shorts/originality.py`, in `publish_gate_node`): each clip's
  fingerprint = opening type (question / warning / how-to / story / number / curiosity /
  statement), structure (opening fix, question first, re-hook kind, loop, length band, frame-one
  text), caption style, title shape (template, label prefix, opener, question/statement) and title
  words. Two checks against the last 10 published clips on that platform (plus clips going out in
  the same run): **near duplicate** — weighted similarity ≥ 0.85 (the template alone is 0.75, so it
  also needs largely the same words); **template streak** — the same template as each of the last
  5. A flagged clip first gets its other title options tried; if one passes, it is used ("switched
  to title option #2"). Otherwise the clip is **held for review** — never published automatically.
  Result in `clip.shorts.originality_flags[platform]` and the gate reasons.
- **Early retention** (`analytics/retention.py`): YouTube collection adds `engagedViews` and the
  audience-retention report (`dimensions=elapsedVideoTimeRatio`, `audienceWatchRatio`,
  `relativeRetentionPerformance`) → `first_3s_retention`, `end_retention`, `replay_ratio` (> 1 =
  replays). Each is asked separately, so an unsupported report never loses the others. **"Viewed vs.
  swiped away" is YouTube Studio only** (not in the API — checked against the YouTube Analytics
  metrics reference) and is recorded as unavailable with that reason; it comes in through **CSV
  import** (Studio export columns such as "Stayed to watch (%)", matched by video id or exact
  title; unknown rows are reported back, never guessed) or **manual entry**. Instagram: average watch
  time → share watched. Each published clip gets `clip.shorts.analytics[platform]` (signals, flop
  flags, a suggestion — re-edit the opening / tighten / consider deleting — and what's missing).
  Styles (opening type, title template, frame-one text, re-hook kind, opening fix) are ranked by
  retention relative to the channel's own posts (z-score per platform and metric; 3+ posts before a
  style counts); the strongest source segments are listed. With "Rank title options by what kept
  viewers" on, P1's title ranking gets at most ±0.1 from that.
- Limits: the YouTube Analytics API usually has no data for 24–72 h, so the true "first hour" signal
  is views from the Data API plus whatever you import; nothing is deleted or re-edited automatically.

What P3 does:
- **Adaptive length** (`shorts/length.py`): the clip's kind from its words (list/steps, story, tip,
  explanation, opinion, general) sets a preferred and a maximum length (tip ≤ 25/40 s, list ≤ 35/55 s,
  story ≤ 40/58 s …; Instagram's 90 s respected). With early-retention feedback on, the length band
  that kept your viewers best (3+ posts) nudges the preferred length. It only steers the loop stage's
  end choice, which shortens only at a real conclusion — never mid-thought; no blend unless "Make
  clips loop" is on.
- **Alternate openings** (`shorts/hook.py :: alternates`): for a clip whose opening stays weak, other
  starting points that score better — a stronger line later in the first 40 % of the clip, or a setup
  line up to 15 s before it. Order is never changed. Listed on the clip with **Use** (re-renders from
  there); with "use the best one automatically" on, the best one is applied when it passes the check.
- **A/B tests** (`shorts/abtest.py`, table `ab_tests`, migration 0006): one at a time — titles (best vs
  second option), first-frame text (best vs second), text on frame one vs none. New clips alternate
  between sides (balanced, kept on re-render). Once each side has N clips with early-retention numbers
  (default 5 — with fewer, a permutation test can't reach p < 0.1), the sides are compared; a winner is
  named only at p < 0.1, otherwise "no clear difference". Platforms can't show two titles of one Short
  to different viewers, so this is a test across clips, and it says so.
- **Cost** (`analytics/costs.py`): from the Model Hub's metered usage (tokens, USD, estimated flag) and
  job run times. A clip's own jobs count for it; whole-video work is shared equally by its clips.
  Shown per clip, per project, and per published Short (with cost per 1,000 views when views exist).

Windows render fix (0.3.5): every render on the native Windows app failed with "Cannot select channel
layout … format_out" — the shipped FFmpeg 6.0 re-initialises the filter graph mid-render and can't
match loudnorm's open channel layout to the AAC encoder. Reproduced with the same 6.0 build; the output
audio format is now named explicitly after loudnorm. Windows CI now renders a real clip with the
shipped FFmpeg, and the backend tests pass with FFmpeg 6.0.

## 1. The pipeline today (from the code)

A project runs as one LangGraph workflow, `handlers/video_workflow.py :: graph()`:

```
resolve_node → acquire_node → prepare_node → transcribe_analyze_node → select_node
            → render_node (one media.render child job per clip) → package_node
            → publish_gate_node → publish_node → finalize_node
```

| Stage | Where | What it does now |
|---|---|---|
| Transcription | `media/transcription.py` — `select_engine()`, `WhisperXEngine` / `FasterWhisperEngine` / `VoskEngine`, `restore_punctuation()`; job `handlers/media_jobs.py :: transcribe_job()` | Word-level timestamps, kept per source (`load_words()`). |
| Signals | `media/analysis.py` — `detect_scenes()`, `visual_features()` (sharpness, brightness, faces at 2 fps), `audio_events()` (YAMNet), `audio_profile()` (levels, silences); job `analyze_job()` | Per-source analysis JSON (`load_analysis()`). |
| Moment selection | `media/highlights.py` — `generate()` builds sentence windows, `_window()` scores 10 dimensions, `select()`, optional `llm_rerank()`, `padded_bounds()`; called from `select_node()` | One `hook` score from the **first sentence's words only** (hook phrases, question, number, length, filler start). Weighted total decides. No look at the first frame, timing of the first line, or what is on screen. |
| Edit plan | `media/render.py :: plan_edits()` | Removes internal silences > 0.7 s and filler asides (`_filler_sentence_cuts`). Does **not** trim the opening runway beyond `padded_bounds`. |
| Reframe + render | `render.py :: crop_path_for()`, `build_filtergraph()`, `render()`, `quality_check()`; vendored `vendor/autoclip/reframe` | 9:16 crop following faces, punch-in, loudnorm, fades. |
| Captions | `vendor/autoclip/captions.py` (styles `bold_pop`, `karaoke_fill`, `clean_lower`, `boxed`; `build_ass()`), `render.py :: caption_style_for()`; SRT/VTT via `media/subtitles.py :: build_cues()`, `validate()` | Word-highlight captions exist (`bold_pop`), but the highlight follows the *spoken* word, not *power* words. SRT is uploaded to YouTube. |
| Cover | `media/thumbnails.py :: best_frame_time()`, `cover_text()`, `compose()` | A separate cover image with hook text. Shorts feeds don't show it; **frame 1 of the video has no text hook.** |
| Packaging | `media/metadata.py :: generate()` (LLM via Model Hub, else `local_copy()`); written in `package_node()` | One title/description/tags per platform. No variants, no title-length rule, no ranking. |
| Publish gate | `video_workflow.py :: publish_gate_node()` | Blocks on QC failure, caption review words and third-party sources; otherwise policy decides (auto / approval / draft only). Knows nothing about hook or originality. |
| Publishing | `publishing/service.py :: publish_youtube()`, `publish_instagram()`; scheduling in `publishing/schedule.py` | Unchanged by this plan. |
| Analytics | `analytics/collect.py :: _youtube()` (views, minutes, avg view duration/percentage, likes, comments, shares, subs), `_instagram()`; snapshots at +1 h, 24 h, 72 h, 7 d, 28 d (`publish_jobs`) | Aggregates only. No retention curve, no first-seconds retention, no swipe-away, no CSV import. |
| Learning | `analytics/compare.py :: dataset()`, `learning/engine.py :: analyze()`, `DIMENSIONS` | Compares view counts across a few dimensions (e.g. `_hook_has_number`) and nudges `highlights` weights. |
| Clip metadata | `db/models.py :: Clip` (`scores`, `features`, `edit_plan`, `reasons`, `status`) | No hook/loop/originality/gate fields. |

## 2. Target pipeline

```
transcribe → segment → score moments → HOOK score/reconstruct → retention plan (re-hook)
  → render vertical → kinetic captions → LOOP-SEAM check → PACKAGING variants
  → ORIGINALITY check → publish gate → publish → analytics ingest (incl. first hour)
```

New stages are modules under `backend/clipforge/shorts/` and run inside the existing graph — no new
service, no new queue:

| New module | Called from | Feature flag (Settings → Clips, `clips.*`) |
|---|---|---|
| `shorts/hook.py` — `score_opening()`, `reconstruct()` | after `highlights.select()` in `select_node()`; before `plan_edits()` in `render_job()` | `hook_scorer`, `hook_reconstruct` |
| `shorts/loop.py` — `seam_score()`, `adjust_for_loop()` | `render_job()` (plan) and after render (verify on frames) | `loop_seam` |
| `shorts/retention.py` — `exit_points()`, `plan_rehooks()` | `render_job()` | `rehook` (P1) |
| `shorts/captions.py` — power-word highlighting for `vendor/autoclip/captions` | `render.py :: build_filtergraph()` | `kinetic_captions` (P1) |
| `shorts/packaging.py` — variants + ranking | `package_node()` | `packaging_variants` (P1) |
| `shorts/originality.py` — similarity vs last N published | `publish_gate_node()` | `originality_gate` (P2) |
| `analytics/retention.py` + CSV import | `analytics_jobs`, `/api/analytics/import` | `first_hour_loop` (P2) |

Every flag defaults **off** for existing projects until you switch it on, so current commands and
results stay exactly as they are.

## 3. Phases

### P0 — first 3 seconds + loop (this plan's first code change)

**P0.1 Hook scorer** (`shorts/hook.py :: score_opening(clip, words, analysis, mezzanine)`), five
sub-scores 0–1, all from data the pipeline already has:

| Sub-score | Computed from |
|---|---|
| `frame_one_clarity` | `analysis.visual` samples in the first 0.5 s: sharpness, brightness, face present/centred; scene cut inside the first 0.3 s counts against |
| `text_hook` | strength of the candidate on-screen text (see reconstruction) — hook phrases, specificity, numbers, ≤ 2–5 words |
| `spoken_hook_timing` | time from clip start to the first non-filler word; 1.0 at ≤ 0.5 s, 0 at ≥ 3 s |
| `curiosity_gap` | first line opens a question/claim/conflict ("why", "the mistake", "nobody tells you", contrast words) **and** the answer is not in the first 3 s (answer markers: "because", "the answer is", resolution sentence) |
| `swipe_risk` (inverted) | filler/greeting at start ("hey guys", "so", "um"), silence or music-only first second, logo/intro (static frames + no speech), slow pan (low motion + no face) |

`hook_score = weighted mean` (weights in config). Stored on the clip with the reasons.

**P0.2 Hook reconstruction** (`reconstruct()`), only when `hook_score < hook.threshold`:
1. **Runway removal**: move the clip start to the strongest opening inside the first ~8 s — the
   first sentence that scores best on the spoken-hook rules — dropping greetings, filler, silence,
   intros/logos and slow pans before it. Never cut mid-word (word timestamps), never past the
   point where the clip would fall below `duration.min_s`.
2. **First line ≤ 1.5 s**: if speech still starts later than 1.5 s, trim the leading silence to
   0.15 s.
3. **Frame-one text hook**: 1–5 words, bold, middle third of the frame, readable at ~200 px wide,
   on screen for the first ~1.5 s (burned into the render, separate from captions). Text comes
   **only from the clip's own words** (a shortened form of its strongest line or question) — with
   an LLM if allowed, else a local rule set; any LLM output is checked so every content word appears
   in the transcript.
4. **Tension first** (opt-in, see question 2): if the premise resolves in the first 3 s and a
   question/setup sentence exists later in the same clip, open with that sentence and continue in
   order — still only the speaker's own words, cut on sentence boundaries.
5. Re-score. If still below threshold → clip status `needs_review`, gate result `held` with the
   reasons; it is never published automatically.

**P0.3 Loop seam** (`shorts/loop.py`): after the edit plan, before render —
- `seam_score` from (a) audio: last 0.3 s vs first 0.3 s loudness (no hard cut mid-sound), (b) text:
  the last sentence ends a thought and the first starts one (sentence boundaries, no dangling
  "and"), (c) visual: frame histogram/SSIM similarity of the last and first frame (OpenCV, already
  installed).
- If below `loop.threshold`: try moving the end to a nearby sentence end (±3 s) that scores better;
  else add a 0.2–0.5 s bridge (crossfade of the last frame into the first, audio fade) so the replay
  doesn't jump.
- Length preference: shortest version that is still complete; `< 30 s` unless the clip's pacing
  score is high, then up to 50–60 s.
- `loop_score` and `loopable` stored on the clip; with two otherwise equal cuts the loopable one wins.

**P0 data model** — migration `0005`: `clips.hook_score`, `clips.loop_score`, `clips.shorts` (JSON:
`hook`, `retention_plan`, `packaging_variants`, `originality_flags`, `publish_gate_result`,
`analytics`), status `needs_review`. Existing rows stay valid (all nullable).

**P0 gate**: `publish_gate_node()` gains one rule — when `hook_gate` is on and a clip is `held`, it
is left out of automatic publishing and shown for review with its reasons. (Needs your OK: it changes
publishing behaviour.)

### P1 — retention inside the clip
- **Re-hook at 20–25 s** (`shorts/retention.py`): find exit points (a thought resolves: conclusion
  markers + pause + topic shift) between 15–30 s; place a pattern interrupt there — punch-in/zoom,
  a re-hook text line from the clip's own words ("But here's the part that matters" style framing is
  allowed only as framing, never as a claim), or move the next setup sentence earlier if it exists.
- **Kinetic captions**: phrase-by-phrase (2–4 words), large, high contrast; 1–3 power words per line
  get colour + scale pop (power words = numbers, rare content words by IDF, emotion lexicon, the
  focus keyword). New preset `kinetic` in `vendor/autoclip/captions.py`; old presets unchanged.
- **SEO transcript**: the full clip transcript is stored (it already is: `Clip.transcript_text` +
  per-source words) and exported as `.txt` next to SRT/VTT; descriptions use a keyword-rich excerpt.
- **Packaging variants** (`shorts/packaging.py`): 2–3 variants per clip of title (30–50 chars,
  keyword first, promise second), first-frame text (1–2 words, high contrast), description with
  transcript excerpt, hashtags per platform; ranked by rules + (later) learned performance. The
  first frame *is* the thumbnail for Shorts; the cover image stays for Instagram and long-form.

### P2 — guardrails and feedback
- **Originality gate**: compare hook type, structure (re-hook/loop pattern), caption preset and
  title pattern with the last 5–10 published clips (Jaccard over pattern tokens + embedding
  similarity of titles via the existing local text embedder). Too similar → pick another variant;
  none left → `held` for review.
- **First-hour loop**: add the YouTube Analytics **audience-retention report**
  (`dimensions=elapsedVideoTimeRatio`, `metrics=audienceWatchRatio,relativeRetentionPerformance`)
  per published Short — that gives retention at the first seconds and replays (ratio > 1).
  "Viewed vs swiped away" is a YouTube Studio number not offered by the API as far as I know; I'll
  confirm when implementing and otherwise import it from a Studio CSV. Instagram: Reels insights
  (average watch time, plays, saves, shares). CSV/manual import into a local `clip_metrics` store
  for anything the APIs don't give. The learning engine then ranks hook types/first-frame styles by
  early retention (not views) and flags flops (low first-3-second retention) for re-edit.

### P3 — later
Adaptive length per content type/platform, A/B of hooks/titles/first-frame text (the learning
engine already runs randomized experiments — reused), alternate hooks for low scorers, cost per clip
and per published Short (the Model Hub already meters every paid call per job).

## 4. Config (example, Settings → Clips; also `CLIPFORGE_SHORTS__*` env)

```json
"shorts": {
  "hook_scorer": true, "hook_reconstruct": true, "hook_gate": true,
  "hook": { "threshold": 0.55, "max_first_line_s": 1.5, "runway_search_s": 8.0,
            "weights": { "frame_one_clarity": 0.2, "text_hook": 0.2, "spoken_hook_timing": 0.25,
                         "curiosity_gap": 0.2, "swipe_risk": 0.15 },
            "text_hook_words": [1, 5], "text_hook_seconds": 1.5, "tension_first": false },
  "loop_seam": true,
  "loop": { "threshold": 0.6, "search_s": 3.0, "bridge_s": [0.2, 0.5] },
  "duration": { "min_s": 12, "prefer_max_s": 30, "max_s": 58, "long_needs_pacing": 0.7 },
  "rehook": true, "rehook_from_s": 15, "rehook_to_s": 30, "rehook_min_clip_s": 20,
  "rehook_zoom": true, "rehook_text": true, "rehook_framing": true,
  "kinetic_captions": true, "seo_transcript": true,
  "packaging_variants": true, "title_min_chars": 30, "title_max_chars": 50, "first_frame_words": 2,
  "originality_gate": true, "originality_threshold": 0.85, "originality_compare_last": 10, "originality_streak": 5,
  "retention_loop": true, "flop_viewed_rate": 0.55, "flop_first_3s_retention": 0.6, "flop_avg_viewed_pct": 40,
  "learn_from_retention": true
}
```

## 5. Status output (example)

```
Clip 2 — "Why your prices scare clients away"        HELD for review
  hook 0.41 < 0.55 after reconstruction
    - first spoken line at 2.8 s (removed 2.1 s of "so, um, hey guys"; still 0.7 s of music)
    - frame 1: no face, low sharpness (slow pan)
    - premise answered at 2.0 s ("…because they compare you to…")
  loop 0.72 (loopable: ends on "…and that's the mistake", opens on "The mistake…")
  → not published automatically. Publish anyway / Re-edit / Discard

Clip 1 — "Pricing mistake #1"                        PUBLISHED (scheduled 18:00)
  hook 0.78 (runway removed 1.4 s; text hook "PRICE MISTAKE"); loop 0.81

Clip 3 — "Price: Never compete on price"             Waiting for your approval
  first 3 s 0.76 · loop 0.70 · re-hook at 23.1 s
    - re-hook at 23.1 s (0.9 s pause after it; the next sentence changes topic)
    - punch-in zoom 23.1-24.6 s
    - text "NOW THE IMPORTANT PART" from 22.5 s (framing text (no claim))
  Title and first-frame options
    #1 Price: Here is my number one wanted   (35 chars · 98)  On frame one: NEVER COMPETE   In use
    #2 Price: Never compete on price         (29 chars · 89)  Frame text: NUMBER ONE        [Use]
```

Chat ("why was clip 2 held?") and the project page read the same `publish_gate_result`.

## 6. Tests and acceptance (P0)

| Test | Accepts when |
|---|---|
| low-hook rejection | a clip starting with 2.5 s of filler + no question scores < threshold; with reconstruction off it is `held` with reasons; it is never in the auto-publish list |
| runway removal | "hey guys, so, um, … The biggest mistake is X" → new start at "The biggest", within 0.2 s of the word, not mid-word, duration ≥ min |
| first line ≤ 1.5 s | after reconstruction the first non-filler word starts ≤ 1.5 s |
| frame-one text | 1–5 words, every content word present in the transcript, rendered in the middle third (ASS position), present in the rendered frame 0 (OCR-free check: render with/without and diff the middle third) |
| tension-first | with the flag on, a clip whose answer comes first opens on its question sentence; off → order unchanged |
| loop seam | a cut ending mid-sentence is moved to the nearest sentence end within 3 s; when impossible a bridge of 0.2–0.5 s appears in the edit plan; `loop_score` stored |
| no fabrication | text hooks/variants with words not in the source are rejected |
| existing behaviour | all current tests pass with every flag off; with flags on, existing commands ("create 5 Shorts…") still work |

Real-video check: run the pipeline on the test talk video before and after, compare hook scores,
first-line time and the first frames side by side.

## 7. Models

Nothing new is required for P0: hook/loop scoring uses the transcript, the existing MediaPipe face
detector and audio analysis, and OpenCV. Optional, for better judgement and wording:
- a **local language model** through the existing Model Hub + Ollama (e.g. Qwen 2.5 7B or Llama
  3.1 8B, ~5 GB; a 3B model works on 8 GB PCs) for text hooks, curiosity/tension judgement, title
  variants — no paid API needed;
- a better **word-timing transcription** (faster-whisper/WhisperX, already supported) makes kinetic
  captions tighter than Vosk;
- **effects** (zoom punch-ins, motion blur on cuts, bridges) are FFmpeg filters — no model.

## 8. Questions for the owner (before P0 code)

1. **Hold low-hook clips from automatic publishing?** (changes publishing behaviour; proposed: yes
   for auto-publish and batches; manual "Publish" still allowed with a warning).
2. **Re-ordering sentences** to open with the question ("tension first"): allowed by default, or only
   with your approval per clip? (Proposed: off by default — it changes what the speaker appears to
   say first.)
3. **Language model for hooks/packaging:** local only (Ollama), or may it use your paid AI
   connections within your budget? (Proposed: local first, paid if you've allowed it.)
