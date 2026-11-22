"""Loop seam: does the end of a Short flow back into its start when it replays?

Shorts replay automatically; a clip whose last line leads into its first one gets watched twice.
``seam_score`` judges a cut from the transcript (a finished thought at the end, a fresh one at the
start, an echo between them, no cut mid-word); ``choose_end`` moves the end to a nearby sentence
end that loops better (and prefers the shortest complete version); when no cut loops well enough
the render gets a short bridge (``bridge``): the last fraction of a second blends into the first
frames, so the replay doesn't jump.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..media import textutils as tu

DANGLING_END = {"and", "but", "so", "because", "or", "the", "a", "to", "of", "with", "that", "which", "if"}
END_PUNCT = re.compile(r"[.!?…]['\"]?$")


@dataclass
class LoopConfig:
    threshold: float = 0.6
    search_s: float = 3.0
    min_duration_s: float = 12.0
    prefer_max_s: float = 30.0
    max_duration_s: float = 58.0
    bridge_s: tuple[float, float] = (0.2, 0.5)


@dataclass
class LoopAssessment:
    score: float
    parts: dict[str, float]
    reasons: list[str]
    loopable: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoopPlan:
    end: float
    assessment: LoopAssessment
    changes: list[str] = field(default_factory=list)
    bridge_s: float = 0.0

    def to_dict(self) -> dict:
        return {"end": self.end, "assessment": self.assessment.to_dict(), "changes": self.changes, "bridge_s": self.bridge_s}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _span(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["end"] > start + 1e-6 and w["start"] < end - 1e-6]


def seam_score(words: list[dict], start: float, end: float, *, visual_similarity: float | None = None,
               cfg: LoopConfig | None = None) -> LoopAssessment:
    cfg = cfg or LoopConfig()
    span = _span(words, start, end)
    reasons: list[str] = []
    parts: dict[str, float] = {}
    if not span:
        return LoopAssessment(0.0, {}, ["no speech"], False)
    last, first = span[-1], span[0]
    # The end: a finished sentence, not mid-word, not a dangling "and…".
    cut_mid_word = last["end"] > end + 0.05
    finished = bool(END_PUNCT.search(last["text"]))
    dangling = (tu.tokens(last["text"]) or [""])[-1] in DANGLING_END
    after = next((w for w in words if w["start"] >= end - 0.01), None)
    pause_after = (after["start"] - last["end"]) if after else 1.0
    parts["clean_end"] = _clip01(0.5 * finished + 0.3 * (pause_after >= 0.25) + 0.2 - 0.6 * cut_mid_word - 0.4 * dangling)
    if cut_mid_word:
        reasons.append("ends mid-word")
    elif not finished:
        reasons.append("ends mid-sentence")
    if dangling:
        reasons.append(f"ends on \"{tu.tokens(last['text'])[-1]}\"")
    # The start: a fresh thought (not "and…", not mid-sentence).
    before = [w for w in words if w["end"] <= start + 0.01]
    starts_sentence = not before or bool(END_PUNCT.search(before[-1]["text"]))
    first_tok = (tu.tokens(first["text"]) or [""])[0]
    parts["clean_start"] = _clip01(0.7 * starts_sentence + 0.3 * (first_tok not in tu.DANGLING_START))
    # The echo: the last line comes back to what the first line was about.
    first_sent = tu.sentences(span)[0].text if span else ""
    last_sent = tu.sentences(span)[-1].text if span else ""
    a, b = set(tu.content_words(first_sent)), set(tu.content_words(last_sent))
    parts["echo"] = _clip01(len(a & b) / max(1, min(len(a), len(b)) / 2)) if a and b else 0.0
    weights = {"clean_end": 0.45, "clean_start": 0.25, "echo": 0.30}
    if visual_similarity is not None:
        parts["visual"] = _clip01(visual_similarity)
        weights = {"clean_end": 0.35, "clean_start": 0.2, "echo": 0.25, "visual": 0.2}
    score = sum(weights[k] * parts[k] for k in weights) / sum(weights.values())
    loopable = score >= cfg.threshold and not cut_mid_word
    return LoopAssessment(round(score, 3), {k: round(v, 3) for k, v in parts.items()}, reasons, loopable)


def choose_end(words: list[dict], start: float, end: float, *, pacing: float = 0.5,
               cfg: LoopConfig | None = None) -> LoopPlan:
    """Pick the end that loops best among nearby sentence ends; prefer the shortest complete cut
    (under ``prefer_max_s`` unless the pacing is strong). Adds a bridge when nothing loops."""
    cfg = cfg or LoopConfig()
    current = seam_score(words, start, end, cfg=cfg)
    span = _span(words, start, end + cfg.search_s)
    allow_long = pacing >= 0.7
    candidates: list[tuple[float, float, LoopAssessment]] = []
    for sent in tu.sentences(span):
        following = next((w for w in words if w["start"] >= sent.end - 1e-6 and w["end"] > sent.end + 1e-6), None)
        gap = (following["start"] - sent.end) if following else 0.24
        t = round(sent.end + min(0.12, max(0.0, gap / 2)), 3)  # inside the pause, never into the next word
        duration = t - start
        if duration < cfg.min_duration_s or duration > cfg.max_duration_s:
            continue
        near = abs(t - end) <= cfg.search_s
        # Shorter than asked only at a real conclusion ("that's why…", "the key is…"), never just
        # because a sentence ended: cutting a clip short must not throw its point away.
        shorter_complete = (t < end - cfg.search_s and duration <= cfg.prefer_max_s and not allow_long
                            and bool(tu.CONCLUSION_MARKERS.search(sent.text)))
        if not (near or shorter_complete):
            continue
        assessment = seam_score(words, start, t, cfg=cfg)
        if shorter_complete and not near and assessment.score < cfg.threshold:
            continue
        length_bonus = 0.08 if duration <= cfg.prefer_max_s or allow_long else 0.0
        candidates.append((assessment.score + length_bonus, t, assessment))
    plan = LoopPlan(end=end, assessment=current)
    if candidates:
        best_value, best_t, best = max(candidates, key=lambda c: (c[0], -c[1]))
        current_value = current.score + (0.08 if end - start <= cfg.prefer_max_s or allow_long else 0.0)
        if best_value > current_value + 0.02 and abs(best_t - end) > 0.05:
            plan = LoopPlan(end=best_t, assessment=best,
                            changes=[f"moved the end {best_t - end:+.1f} s to a sentence end that loops back"
                                     + (" (shorter, still complete)" if best_t < end - cfg.search_s else "")])
    if not plan.assessment.loopable:
        plan.bridge_s = round(min(cfg.bridge_s[1], max(cfg.bridge_s[0], 0.3)), 2)
        plan.changes.append(f"adds a {plan.bridge_s:.1f} s blend from the last frame into the first")
    return plan


def frame_similarity(video: Path) -> float | None:
    """Similarity (0-1) of a rendered clip's first and last frames (colour histograms)."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return None
    cap = cv2.VideoCapture(str(video))
    try:
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, first = cap.read()
        if not ok or count < 2:
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, count - 2))
        ok, last = cap.read()
        if not ok:
            return None
        hists = []
        for frame in (first, last):
            hsv = cv2.cvtColor(cv2.resize(frame, (160, 284)), cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            hists.append(cv2.normalize(hist, hist).flatten())
        return round(max(0.0, float(cv2.compareHist(hists[0], hists[1], cv2.HISTCMP_CORREL))), 3)
    finally:
        cap.release()


def bridge(video: Path, out: Path, bridge_s: float, duration_s: float, *, has_audio: bool = True) -> Path:
    """Blend the last ``bridge_s`` seconds into the clip's first frames (length unchanged), so the
    replay starts where the blend ends."""
    b = max(0.1, min(bridge_s, duration_s / 4))
    offset = max(0.0, duration_s - b)
    graph = (f"[0:v]split[main][head];[head]trim=0:{b:.3f},setpts=PTS-STARTPTS[h];"
             f"[main]trim=0:{duration_s:.3f},setpts=PTS-STARTPTS[m];"
             f"[m][h]xfade=transition=fade:duration={b:.3f}:offset={offset:.3f}[vout]")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video), "-filter_complex"]
    maps = ["-map", "[vout]"]
    if has_audio:
        graph += (f";[0:a]asplit[am][ah];[ah]atrim=0:{b:.3f},asetpts=PTS-STARTPTS[ahh];"
                  f"[am]atrim=0:{duration_s:.3f},asetpts=PTS-STARTPTS[amm];[amm][ahh]acrossfade=d={b:.3f}[aout]")
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += [graph, *maps, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True, capture_output=True, timeout=600)
    return out
