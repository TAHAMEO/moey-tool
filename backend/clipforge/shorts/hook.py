"""First-3-seconds hook: score the opening of a clip and, if it is weak, rebuild it.

The overall moment score (``media/highlights.py``) says whether a stretch of the source is worth a
clip. This module judges only what a viewer sees and hears before deciding to swipe: the first
frame, how soon someone speaks, what the first line promises, whether it gives the answer away,
and the on-screen text hook.

Everything here works on data the pipeline already has (word timestamps, the per-source visual and
audio analysis). Nothing is invented: the on-screen text is built only from the clip's own words.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from ..media import textutils as tu

#: Openings that make people swipe: greetings, channel intros and throat-clearing.
GREETING_RE = re.compile(
    r"^(hey|hi|hello|what'?s up|welcome( back)?|good (morning|evening|afternoon))\b.*?\b(guys|everyone|everybody|folks|"
    r"friends|y'?all|team|back|channel)\b|^welcome( back)?\b", re.IGNORECASE)
INTRO_RE = re.compile(
    r"^(in (this|today'?s) (video|episode)|today,? (i|we)('m|'re| am| are)? (going|gonna)|(i|we)('m|'re| am| are) (going|gonna) "
    r"(talk|look|show|cover|discuss)|before we (start|begin|get started)|let'?s (get started|dive in|jump in|begin)|"
    r"(don'?t forget|make sure) to (like|subscribe))", re.IGNORECASE)
LEAD_FILLERS = {"so", "okay", "ok", "alright", "well", "right", "yeah", "um", "uh", "erm", "hmm", "uhm", "like", "now",
                "basically", "and", "anyway"}
#: The answer arrives early when the opening already explains/resolves.
ANSWER_RE = re.compile(r"\b(because|the (answer|reason|solution|trick|key) is|that'?s (why|how)|it turns out|"
                       r"so the answer|the way to|here'?s how it works)\b", re.IGNORECASE)
CURIOSITY_RE = re.compile(r"\b(why|how|what if|nobody|no one|secret|mistake|truth|never|stop|wrong|actually|"
                          r"the (one|real|biggest|only|worst|best)|most people|you'?re (doing|making)|here'?s)\b",
                          re.IGNORECASE)


@dataclass
class HookConfig:
    threshold: float = 0.55
    max_first_line_s: float = 1.5
    runway_search_s: float = 8.0
    min_duration_s: float = 12.0
    text_words: tuple[int, int] = (1, 5)
    text_seconds: float = 1.5
    tension_first: bool = False
    weights: dict[str, float] = field(default_factory=lambda: {
        "frame_one_clarity": 0.20, "text_hook": 0.20, "spoken_hook_timing": 0.25, "curiosity_gap": 0.20, "swipe_safety": 0.15})


@dataclass
class HookAssessment:
    score: float
    parts: dict[str, float]
    reasons: list[str]
    first_word_s: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HookResult:
    """What the reconstruction decided. ``keep`` is the clip's new outline in source seconds
    (normally one range; two or more only when ``tension_first`` moved the question forward)."""

    start: float
    end: float
    keep: list[tuple[float, float]]
    text_hook: str | None
    before: HookAssessment
    after: HookAssessment
    changes: list[str]
    status: str  # "ok" | "improved" | "held"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["keep"] = [list(k) for k in self.keep]
        return data


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _words_in(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["end"] > start and w["start"] < end]


def _strip_fillers(text: str) -> str:
    toks = text.split()
    while toks and (tu.tokens(toks[0]) or [""])[0] in LEAD_FILLERS:
        toks = toks[1:]
    return " ".join(toks)


def is_runway(sentence_text: str) -> bool:
    """A sentence that only clears the throat: a greeting, a channel intro, filler, or too short to say anything."""
    core = _strip_fillers(sentence_text)
    if not core or GREETING_RE.match(core) or INTRO_RE.match(core):
        return True
    return len(tu.content_words(core)) < 2 and "?" not in sentence_text


def first_content_word(words: list[dict], start: float, end: float) -> dict | None:
    """The first word of real content: after greeting/intro sentences and leading filler words."""
    span = _words_in(words, start, end)
    for sent in tu.sentences(span):
        if is_runway(sent.text):
            continue
        for w in span[sent.first:sent.last + 1]:
            if (tu.tokens(w["text"]) or [""])[0] not in LEAD_FILLERS:
                return w
    return None


def opening_text(words: list[dict], start: float, seconds: float = 3.0) -> str:
    return " ".join(w["text"] for w in _words_in(words, start, start + seconds))


def score_opening(words: list[dict], start: float, end: float, *, visual: list[dict] | None = None,
                  scenes: list[dict] | None = None, has_video: bool = True, text_hook: str | None = None,
                  cfg: HookConfig | None = None) -> HookAssessment:
    """Score the first 0-3 s of a clip that starts at ``start`` (source seconds)."""
    cfg = cfg or HookConfig()
    reasons: list[str] = []
    parts: dict[str, float] = {}

    # 1. How soon a real line lands.
    first = first_content_word(words, start, end)
    first_s = (first["start"] - start) if first else None
    if first_s is None:
        parts["spoken_hook_timing"] = 0.0
        reasons.append("no spoken line in the opening")
    else:
        parts["spoken_hook_timing"] = _clip01(1 - max(0.0, first_s - 0.5) / 2.5)
        if first_s > cfg.max_first_line_s:
            reasons.append(f"first spoken line starts at {first_s:.1f} s")

    # 2. Frame one: sharp, lit, a face, no cut in the first instant.
    if not has_video:
        parts["frame_one_clarity"] = 0.5
    else:
        samples = [v for v in (visual or []) if start - 0.01 <= v["t"] <= start + 0.75]
        if samples:
            sharp = sum(_clip01(math.log10(max(v.get("sharpness", 1), 1)) / 3) for v in samples) / len(samples)
            lit = sum(1.0 if 40 <= v.get("brightness", 0) <= 215 else 0.0 for v in samples) / len(samples)
            face = sum(1.0 if v.get("faces") else 0.0 for v in samples) / len(samples)
            clarity = 0.45 * sharp + 0.2 * lit + 0.35 * face
        else:
            clarity = 0.5
        if any(start < s.get("start", -1) <= start + 0.3 for s in (scenes or [])):
            clarity -= 0.3
            reasons.append("a scene cut in the first 0.3 s")
        parts["frame_one_clarity"] = _clip01(clarity)
        if parts["frame_one_clarity"] < 0.4:
            reasons.append("frame one is unclear (blurred, dark or no face)")

    # 3. Curiosity gap: the opening opens a question or claim, and doesn't answer it straight away.
    opening = opening_text(words, start, 3.0)
    core = tu.strip_discourse(opening)
    opens = bool(tu.HOOK_OPENERS.search(core)) or bool(CURIOSITY_RE.search(core)) or "?" in opening
    numbers = bool(tu.NUMBER_RE.search(core))
    answered = bool(ANSWER_RE.search(opening))
    parts["curiosity_gap"] = _clip01(0.55 * opens + 0.2 * numbers + 0.25 * (not answered))
    if answered:
        reasons.append("the answer comes in the first 3 s")
    if not opens:
        reasons.append("the opening doesn't raise a question or make a claim")

    # 4. Swipe risk: greetings/filler, silence, a dangling start.
    risk = 0.0
    lead = opening_text(words, start, 1.5).strip()
    if lead and (is_runway(lead) or (tu.tokens(lead) or [""])[0] in LEAD_FILLERS):
        risk += 0.5
        reasons.append("opens with a greeting, intro or filler")
    if first_s is None or first_s > 1.0:
        risk += 0.3
    toks = tu.tokens(core)
    if toks and toks[0] in tu.DANGLING_START:
        risk += 0.3
        reasons.append(f"starts mid-thought (\"{toks[0]}…\")")
    parts["swipe_safety"] = _clip01(1 - risk)

    # 5. On-screen text hook.
    parts["text_hook"] = text_hook_strength(text_hook, cfg) if text_hook else 0.0

    weights = cfg.weights
    score = sum(weights.get(k, 0) * v for k, v in parts.items()) / max(1e-9, sum(weights.get(k, 0) for k in parts))
    return HookAssessment(score=round(score, 3), parts={k: round(v, 3) for k, v in parts.items()},
                          reasons=reasons, first_word_s=None if first_s is None else round(first_s, 2))


def text_position(visual: list[dict] | None, start: float) -> float:
    """Where the text hook goes (vertical centre, share of the height): in the middle third, on the
    side away from the speaker's face. A landscape source is cropped to 9:16 at full height, so a
    face keeps its vertical position."""
    faces = [f for v in (visual or []) if start - 0.01 <= v["t"] <= start + 1.0 for f in (v.get("faces") or [])]
    if not faces:
        return 0.42
    face = max(faces, key=lambda f: f.get("area", 0))
    return 0.6 if face.get("cy", 0.4) < 0.5 else 0.38


def text_hook_strength(text: str, cfg: HookConfig | None = None) -> float:
    cfg = cfg or HookConfig()
    toks = tu.tokens(text)
    if not toks:
        return 0.0
    low, high = cfg.text_words
    size = 1.0 if low <= len(toks) <= high else 0.4
    power = min(1.0, sum(1 for t in toks if t in tu.HOOK_WORDS) / 1.5)
    number = 1.0 if tu.NUMBER_RE.search(text) else 0.0
    question = 1.0 if text.strip().endswith("?") or toks[0] in {"why", "how", "what"} else 0.0
    return _clip01(0.4 * size + 0.3 * power + 0.15 * number + 0.15 * question)


def make_text_hook(words: list[dict], start: float, end: float, cfg: HookConfig | None = None) -> str | None:
    """A short on-screen hook built only from the clip's own words: the strongest line, shortened."""
    cfg = cfg or HookConfig()
    span = _words_in(words, start, end)
    if not span:
        return None
    best, best_score = None, -1.0
    for sent in tu.sentences(span):
        core = tu.strip_discourse(sent.text)
        toks = tu.tokens(core)
        if not toks or is_runway(sent.text):
            continue
        strength = (2.0 * bool(tu.HOOK_OPENERS.search(core)) + 1.5 * ("?" in sent.text)
                    + sum(1 for t in toks if t in tu.HOOK_WORDS) + 0.8 * bool(tu.NUMBER_RE.search(core))
                    - 0.05 * max(0, len(toks) - 12) - 0.3 * (sent.start - start) / max(1.0, end - start))
        if strength > best_score:
            best, best_score = core, strength
    if best is None:
        return None
    return shorten(best, cfg.text_words[1])


def shorten(sentence: str, max_words: int = 5) -> str:
    """Keep the words that carry the line: drop leading discourse, keep content words and the
    question/claim words, cut to ``max_words``. Never adds a word that isn't in ``sentence``."""
    raw = [w for w in re.findall(r"[A-Za-z0-9'%$]+", tu.strip_discourse(sentence))]
    if not raw:
        return ""
    lowered = [w.lower() for w in raw]
    keep_small = {"why", "how", "what", "never", "stop", "no", "not", "don't", "you", "your", "one", "only"}
    picked = [w for w, lw in zip(raw, lowered, strict=False)
              if lw in tu.HOOK_WORDS or lw in keep_small or (lw not in tu.STOPWORDS and len(lw) > 2) or tu.NUMBER_RE.fullmatch(lw)]
    if len(picked) > max_words:
        # Prefer hook words and numbers, then the earliest content words, in the original order.
        ranked = sorted(range(len(picked)), key=lambda i: (-(picked[i].lower() in tu.HOOK_WORDS or bool(tu.NUMBER_RE.fullmatch(picked[i].lower()))), i))
        chosen = sorted(ranked[:max_words])
        picked = [picked[i] for i in chosen]
    # A leading article/pronoun alone reads badly ("THE MISTAKE" is fine, "YOU" alone is not).
    while picked and picked[-1].lower() in {"the", "you", "your", "one", "not", "no"} and len(picked) > 1:
        picked.pop()
    text = " ".join(picked).upper()
    if sentence.strip().endswith("?") and not text.endswith("?"):
        text += "?"
    return text


def grounded(text: str, words: list[dict]) -> bool:
    """True when every content word of ``text`` is spoken in ``words`` (no invented claims)."""
    spoken = set(tu.tokens(" ".join(w["text"] for w in words)))
    return all(t in spoken for t in tu.tokens(text) if t not in tu.STOPWORDS)


def reconstruct(words: list[dict], start: float, end: float, *, visual: list[dict] | None = None,
                scenes: list[dict] | None = None, has_video: bool = True, cfg: HookConfig | None = None,
                rebuild: bool = True) -> HookResult:
    """Score the opening. When the *spoken* opening is below the threshold and ``rebuild`` is on:
    skip the runway (greetings, intros, filler, silence — never a sentence with substance), make the
    first line land within ``max_first_line_s``, add a frame-one text hook from the clip's own words,
    and (only with ``tension_first``) open on the clip's own question. Held when still too weak."""
    cfg = cfg or HookConfig()
    kw = {"visual": visual, "scenes": scenes, "has_video": has_video, "cfg": cfg}
    before = score_opening(words, start, end, text_hook=None, **kw)
    if not rebuild:
        status = "ok" if before.score >= cfg.threshold else "held"
        return HookResult(start=start, end=end, keep=[(start, end)], text_hook=None, before=before, after=before,
                          changes=[], status=status)

    changes: list[str] = []
    new_start = start
    if before.score < cfg.threshold:
        # 1. Runway: skip leading sentences that only clear the throat.
        latest = min(start + cfg.runway_search_s, end - cfg.min_duration_s)
        for sent in tu.sentences(_words_in(words, start, end)):
            if sent.start > latest or not is_runway(sent.text):
                break
            new_start = sent.end
        first = first_content_word(words, max(start, new_start), end)
        if first and first["start"] <= max(latest, start):
            new_start = max(new_start, first["start"]) if new_start > start else first["start"]
        # 2. The first line lands quickly: start just before the first real word.
        if first and new_start != start:
            prev = [w for w in words if w["end"] <= first["start"] + 1e-6]
            gap = first["start"] - prev[-1]["end"] if prev else 0.3
            new_start = round(max(start, first["start"] - min(0.15, max(0.0, gap / 2))), 3)
        if new_start > start + 0.05:
            removed = opening_text(words, start, new_start - start).strip()
            changes.append(f"removed {new_start - start:.1f} s of runway" + (f" (\"{removed[:40]}\")" if removed else " (silence)"))
        else:
            new_start = start
    keep = [(new_start, end)]

    # 3. Frame-one text hook, from the clip's own words only.
    text = make_text_hook(words, new_start, end, cfg)
    if text and not grounded(text, _words_in(words, new_start, end)):
        text = None
    if text:
        changes.append(f"frame-one text hook \"{text}\"")

    # 4. Tension first (opt-in): open on the clip's own question when the answer comes first.
    if cfg.tension_first and ANSWER_RE.search(opening_text(words, new_start, 3.0)):
        question = next((st for st in tu.sentences(_words_in(words, new_start, end))
                         if st.start > new_start + 3.0 and ("?" in st.text or CURIOSITY_RE.search(st.text))), None)
        if question is not None:
            keep = [(question.start, question.end), (new_start, question.start), (question.end, end)]
            keep = [(round(a, 3), round(b, 3)) for a, b in keep if b - a > 0.2]
            changes.append(f"opens on its own question (\"{question.text[:50]}\"), then continues in order")

    spoken_after = score_opening(words, keep[0][0], end, text_hook=None, **kw)
    after = score_opening(words, keep[0][0], end, text_hook=text, **kw)
    if before.score >= cfg.threshold:
        status = "ok"
    elif spoken_after.score >= cfg.threshold - 0.05 and after.score >= cfg.threshold:
        status = "improved"
    else:
        status = "held"  # a text overlay alone doesn't rescue a weak spoken opening
    return HookResult(start=new_start, end=end, keep=keep, text_hook=text, before=before, after=after,
                      changes=changes, status=status)
