"""Re-hook at ~20-25 s: keep viewers past the moment a thought resolves.

Retention on Shorts drops where a thought finishes, because that's a natural place to swipe.
``exit_points`` finds those places on the clip's edited timeline (a sentence that concludes,
a pause after it, a change of topic, a "now let's talk about…" next). ``plan_rehook`` puts a
pattern interrupt at the best one between 15 and 30 s: a short punch-in zoom as the next thought
starts and a text line just before the exit.

The text line is made from the clip's own next sentence (a preview of what's coming) when that
reads as a hook; otherwise from a short list of framing lines that make no claim about the
content ("NOW THE IMPORTANT PART"). Nothing is invented and no sentence is moved.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from ..media import textutils as tu
from . import hook as shook

#: Framing only: these say "keep watching", never what the speaker said or claims.
FRAMINGS = ("BUT HERE'S THE PART THAT MATTERS", "NOW THE IMPORTANT PART", "STAY FOR THIS ONE")


@dataclass
class RehookConfig:
    #: Exit points are looked for in this part of the edited clip (seconds).
    window_s: tuple[float, float] = (15.0, 30.0)
    #: Where a re-hook works best; points here get a bonus.
    target_s: tuple[float, float] = (20.0, 25.0)
    #: Clips shorter than this don't get a re-hook.
    min_clip_s: float = 20.0
    #: At least this much of the clip must follow the re-hook.
    min_tail_s: float = 4.0
    zoom: bool = True
    zoom_s: float = 1.5
    text: bool = True
    text_seconds: float = 2.0
    #: Use a framing line when the next sentence doesn't make a good text hook.
    framing: bool = True
    #: A point needs at least this strength to count as a place where a thought resolves (a topic
    #: change alone is not enough; it needs a conclusion, a pause or a new point announced too).
    min_exit_score: float = 0.3


@dataclass
class ExitPoint:
    t: float
    #: How clearly a thought resolves here (0-1), and that plus closeness to the 20-25 s target.
    strength: float
    score: float
    reasons: list[str]
    sentence: str
    next_sentence: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RehookPlan:
    at: float | None
    text: str | None = None
    text_kind: str | None = None  # "own_words" | "framing"
    text_start: float | None = None
    text_seconds: float = 0.0
    zoom: tuple[float, float] | None = None
    exit: ExitPoint | None = None
    candidates: list[ExitPoint] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    skipped: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["zoom"] = list(self.zoom) if self.zoom else None
        return data


def _overlap(a: str, b: str) -> float:
    x, y = set(tu.content_words(a)), set(tu.content_words(b))
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


def exit_points(words: list[dict], duration: float, cfg: RehookConfig | None = None) -> list[ExitPoint]:
    """Places on the edited timeline where a thought resolves, best first. ``words`` are on the
    clip's own (output) timeline."""
    cfg = cfg or RehookConfig()
    sents = tu.sentences(words)
    points: list[ExitPoint] = []
    lo, hi = cfg.window_s
    for i, sent in enumerate(sents[:-1]):
        nxt = sents[i + 1]
        t = round(sent.end + min(0.12, max(0.0, (nxt.start - sent.end) / 2)), 3)
        if not lo <= t <= hi or duration - t < cfg.min_tail_s:
            continue
        reasons: list[str] = []
        score = 0.0
        finished = bool(re.search(r"[.!?…]['\"]?$", sent.text.strip()))
        if not finished:
            continue  # never interrupt mid-sentence
        if tu.CONCLUSION_MARKERS.search(sent.text) or shook.ANSWER_RE.search(sent.text):
            score += 0.35
            reasons.append("a point is concluded")
        pause = nxt.start - sent.end
        if pause >= 0.35:
            score += min(0.25, 0.25 * pause / 0.6)
            reasons.append(f"{pause:.1f} s pause after it")
        context = " ".join(s.text for s in sents[max(0, i - 1):i + 1])
        if _overlap(context, nxt.text) <= 0.2:
            score += 0.2
            reasons.append("the next sentence changes topic")
        if tu.TRANSITION_RE.search(nxt.text) or (tu.tokens(nxt.text) or [""])[0] in {"now", "next", "another", "also"}:
            score += 0.25
            reasons.append("the next sentence starts a new point")
        if score <= 0:
            continue
        a, b = cfg.target_s
        closeness = 1.0 if a <= t <= b else max(0.0, 1 - min(abs(t - a), abs(t - b)) / 5)
        points.append(ExitPoint(t=t, strength=round(score, 3), score=round(score + 0.3 * closeness, 3), reasons=reasons,
                                sentence=sent.text, next_sentence=nxt.text))
    return sorted(points, key=lambda p: -p.score)


def _framing(seed: str) -> str:
    return FRAMINGS[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(FRAMINGS)]


def plan_rehook(words: list[dict], duration: float, *, cfg: RehookConfig | None = None, seed: str = "") -> RehookPlan:
    """Where and how to re-hook a clip (output seconds). ``seed`` varies the framing line between clips."""
    cfg = cfg or RehookConfig()
    if duration < cfg.min_clip_s:
        return RehookPlan(at=None, skipped=f"clip is {duration:.0f} s (re-hook from {cfg.min_clip_s:.0f} s)")
    points = exit_points(words, duration, cfg)
    strong = [p for p in points if p.strength >= cfg.min_exit_score]
    if not strong:
        return RehookPlan(at=None, candidates=points[:3],
                          skipped=f"no point between {cfg.window_s[0]:.0f} and {cfg.window_s[1]:.0f} s where a thought resolves")
    best = strong[0]
    plan = RehookPlan(at=best.t, exit=best, candidates=strong[:3])
    plan.changes.append(f"re-hook at {best.t:.1f} s ({'; '.join(best.reasons)})")
    if cfg.zoom:
        end = min(duration - 0.5, best.t + cfg.zoom_s)
        if end - best.t >= 0.4:
            plan.zoom = (round(best.t, 3), round(end, 3))
            plan.changes.append(f"punch-in zoom {best.t:.1f}-{end:.1f} s")
    if cfg.text:
        teaser = shook.shorten(best.next_sentence, 5)
        spoken = [w for w in words if w["start"] >= best.t - 0.01]
        strength = shook.text_hook_strength(teaser) if teaser else 0.0
        if teaser and len(tu.tokens(teaser)) >= 2 and strength >= 0.5 and shook.grounded(teaser, spoken):
            plan.text, plan.text_kind = teaser, "own_words"
        elif cfg.framing:
            plan.text, plan.text_kind = _framing(seed or best.sentence), "framing"
        if plan.text:
            plan.text_start = round(max(0.0, best.t - 0.6), 3)
            plan.text_seconds = round(min(cfg.text_seconds, duration - plan.text_start - 0.3), 2)
            what = "a preview of the next line" if plan.text_kind == "own_words" else "framing text (no claim)"
            plan.changes.append(f"text \"{plan.text}\" from {plan.text_start:.1f} s ({what})")
    return plan


def source_time(keep: list[tuple[float, float]], t_out: float) -> float:
    """Source second shown at output second ``t_out`` of an edit that keeps ``keep``."""
    offset = 0.0
    for a, b in keep:
        if t_out <= offset + (b - a):
            return a + (t_out - offset)
        offset += b - a
    return keep[-1][1] if keep else t_out
