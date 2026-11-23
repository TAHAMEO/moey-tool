"""Adaptive length (P3): how long a clip should be, from what kind of clip it is and where it goes.

A quick tip loses viewers after ~30 s; a story needs room to land. ``content_type`` reads the
clip's transcript (steps / a list / a story / an explanation / an opinion); ``target`` turns that
and the platforms into a preferred and a maximum length. When your own retention numbers (P2) show
that a length band keeps viewers better (3+ posts per band), the preferred length moves toward it.

The result only steers the loop stage's end choice (it shortens only at a real conclusion), so a
clip is never cut mid-thought to hit a number.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..media import textutils as tu

#: Preferred and maximum seconds per content type (Shorts/Reels norms; adjustable by your data).
TARGETS = {
    "tip": (25.0, 40.0),
    "list": (35.0, 55.0),
    "story": (40.0, 58.0),
    "explainer": (30.0, 50.0),
    "opinion": (20.0, 35.0),
    "general": (30.0, 45.0),
}
#: Hard limits per platform (seconds) for a Short / Reel to be treated as one.
PLATFORM_MAX = {"youtube": 180.0, "instagram": 90.0}
BANDS = ((0, 20, "<20 s"), (20, 35, "20-35 s"), (35, 60, "35-60 s"), (60, 10_000, "60 s+"))

STEP_RE = re.compile(r"\b(first(ly)?|second(ly)?|third(ly)?|step (one|two|three|\d)|next|finally|number (one|two|three|\d))\b", re.IGNORECASE)
STORY_RE = re.compile(r"\b(i (was|went|had|got|lost|met|remember|decided|quit|started)|when i|years? ago|one day|then (i|we|he|she))\b", re.IGNORECASE)
HOWTO_RE = re.compile(r"\b(how to|here'?s how|you (can|should|need to)|try this|do this|the trick|tip)\b", re.IGNORECASE)
OPINION_RE = re.compile(r"\b(i think|i believe|in my opinion|honestly|the truth is|stop|never|overrated|nobody)\b", re.IGNORECASE)
EXPLAIN_RE = re.compile(r"\b(because|which means|the reason|that'?s why|so that|works by|what happens)\b", re.IGNORECASE)


@dataclass
class LengthPlan:
    content_type: str
    prefer_max_s: float
    max_s: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def band(duration: float) -> str:
    return next(label for lo, hi, label in BANDS if lo <= duration < hi)


def content_type(text: str) -> str:
    """The kind of clip, from the words: steps/list, story, tip, explanation, opinion."""
    steps = len(STEP_RE.findall(text))
    scores = {
        "list": 1.5 * steps,
        "story": 1.2 * len(STORY_RE.findall(text)),
        "tip": 1.0 * len(HOWTO_RE.findall(text)),
        "explainer": 0.8 * len(EXPLAIN_RE.findall(text)),
        "opinion": 0.8 * len(OPINION_RE.findall(text)),
    }
    best = max(scores, key=scores.get)
    if scores[best] < 1.5:
        return "general"
    return best if not (best == "list" and steps < 2) else "tip"


def target(text: str, platforms: list[str], *, learned_band: str | None = None, floor_s: float = 12.0,
           ceiling_s: float = 58.0) -> LengthPlan:
    kind = content_type(text)
    prefer, hard = TARGETS[kind]
    reasons = [f"{kind.replace('_', ' ')} clip: aim for ≤ {prefer:.0f} s, at most {hard:.0f} s"]
    limit = min((PLATFORM_MAX.get(p, ceiling_s) for p in platforms), default=ceiling_s)
    hard = min(hard, limit, ceiling_s)
    if learned_band:
        lo, hi = next((lo, hi) for lo, hi, label in BANDS if label == learned_band)
        nudged = max(lo + 5, min(prefer, hi))
        if abs(nudged - prefer) >= 1:
            reasons.append(f"your {learned_band} clips kept viewers best: aim for ≤ {nudged:.0f} s")
            prefer = nudged
    prefer = max(floor_s, min(prefer, hard))
    if tu.CTA_RE.search(text):
        reasons.append("ends with channel talk: a shorter cut usually loops better")
    return LengthPlan(kind, round(prefer, 1), round(hard, 1), reasons)
