"""Highlight discovery: candidate windows, scoring, validation and selection (Part 11).

Every candidate starts at a sentence start and ends at a sentence end. Ten
interpretable 0–1 scores are combined with the *active strategy's* weights,
so the learning engine can shift what "good" means using evidence. An LLM may
re-rank only the top few candidates when one is connected and within budget;
the local score always exists, so the pipeline never depends on paid APIs.

Scores are potential estimates, never predictions of virality.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.embeddings import get_embedder
from ..providers.hub import TaskSpec, hub
from ..security.untrusted import GUARD_INSTRUCTION, fence, injection_signals
from . import textutils as tu

log = logging.getLogger(__name__)

SCORE_KEYS = ("hook", "clarity", "emotion", "novelty", "information", "completeness", "visual", "audio",
              "relevance", "platform")
DEFAULT_WEIGHTS = {"hook": 0.20, "clarity": 0.08, "emotion": 0.10, "novelty": 0.08, "information": 0.14,
                   "completeness": 0.14, "visual": 0.06, "audio": 0.06, "relevance": 0.08, "platform": 0.06}
REASONS = {
    "hook": "Strong opening hook",
    "clarity": "Clear, confident delivery",
    "emotion": "Emotional language or audience reaction",
    "novelty": "Distinctive, non-generic content",
    "information": "Specific, information-dense",
    "completeness": "Self-contained thought with a clear ending",
    "visual": "Sharp, well-lit frames with a visible speaker",
    "audio": "Clean, consistent audio",
    "relevance": "Matches your requested topic or proven interests",
    "platform": "Length fits the target platform",
}


@dataclass
class SourceSignals:
    duration: float
    levels_db: list[float] = field(default_factory=list)
    level_window_s: float = 0.5
    silences: list[tuple[float, float]] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)
    visual: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    has_video: bool = True


@dataclass
class SelectionRequest:
    count: int
    min_s: float
    max_s: float
    focus: str | None = None
    interests: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    target_s: float | None = None
    llm_rerank: bool = True
    job_id: Any = None
    project_id: Any = None


@dataclass
class Candidate:
    first: int
    last: int
    start: float
    end: float
    text: str
    hook: str
    scores: dict[str, float]
    total: float
    reasons: list[str]
    features: dict[str, Any]
    method: str = "local_heuristic"
    llm: dict[str, Any] | None = None
    adjusted: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _span_values(times_values: list[tuple[float, Any]], start: float, end: float) -> list[Any]:
    return [v for t, v in times_values if start <= t <= end]


class _Index:
    """Precomputed per-sentence features so thousands of windows score fast."""

    def __init__(self, words: list[dict], sigs: SourceSignals):
        self.words = words
        self.sents = tu.sentences(words)
        self.sigs = sigs
        docs = [s.text for s in self.sents]
        self.idf = tu.idf_table(docs)
        all_idf = [self.idf.get(t, 1.0) for d in docs for t in tu.content_words(d)]
        self.idf_lo, self.idf_hi = (np.percentile(all_idf, 10), np.percentile(all_idf, 90)) if all_idf else (1.0, 2.0)
        levels = np.asarray(sigs.levels_db or [0.0])
        voiced = levels[levels > np.percentile(levels, 15)] if levels.size > 4 else levels
        self.level_mean = float(voiced.mean()) if voiced.size else 0.0
        self.level_std = float(voiced.std()) or 1.0
        embedder = get_embedder()
        self.sent_vecs = np.asarray(embedder.embed(docs), dtype=np.float32) if docs else np.zeros((0, 356))
        self.per_sentence = [self._sentence_features(s) for s in self.sents]

    def _sentence_features(self, s: tu.Sentence) -> dict[str, Any]:
        span = self.words[s.first:s.last + 1]
        toks = tu.tokens(s.text)
        content = tu.content_words(s.text)
        core = tu.strip_discourse(s.text)
        first_tokens = tu.tokens(core)[:2]
        return {
            "n": len(span),
            "dur": max(0.01, s.end - s.start),
            "conf": float(np.mean([w.get("conf", 1.0) for w in span])) if span else 0.0,
            "fillers": sum(1 for t in toks if t in tu.FILLERS),
            "content": len(content),
            "idf": float(np.mean([self.idf.get(t, 1.0) for t in content])) if content else 0.0,
            "emotion": max([tu.EMOTION.get(t, 0.0) for t in toks] or [0.0]),
            "info": len(tu.INFO_MARKERS.findall(s.text)),
            "numbers": len(tu.NUMBER_RE.findall(s.text)),
            "question": s.text.rstrip().endswith("?"),
            "hook_open": bool(tu.HOOK_OPENERS.search(core)),
            "hook_words": sum(1 for t in toks if t in tu.HOOK_WORDS),
            "conclusion": bool(tu.CONCLUSION_MARKERS.search(s.text)),
            "dangling": (bool(first_tokens) and first_tokens[0] in tu.DANGLING_START) or bool(tu.CALLBACK_RE.search(s.text)),
            "filler_start": bool(toks) and toks[0] in tu.START_FILLERS,
            "cta": bool(tu.CTA_RE.search(s.text)),
            "transition": bool(tu.TRANSITION_RE.search(s.text)),
        }

    def levels_between(self, start: float, end: float) -> np.ndarray:
        w = self.sigs.level_window_s
        a, b = int(start / w), max(int(start / w) + 1, int(math.ceil(end / w)))
        return np.asarray(self.sigs.levels_db[a:b] or [self.level_mean])


def _window(idx: _Index, i: int, j: int, req: SelectionRequest, focus_vec, interest_vecs) -> Candidate:
    sents = idx.sents[i:j + 1]
    feats = idx.per_sentence[i:j + 1]
    start, end = sents[0].start, sents[-1].end
    duration = max(0.1, end - start)
    text = " ".join(s.text for s in sents)
    first = feats[0]
    n_words = sum(f["n"] for f in feats)
    wps = n_words / duration
    scores: dict[str, float] = {}

    scores["hook"] = _clip01(0.45 * first["hook_open"] + 0.15 * first["question"] + 0.15 * min(1.0, first["hook_words"] / 2)
                             + 0.10 * (first["numbers"] > 0) + 0.15 * (first["n"] <= 14)
                             - 0.45 * first["filler_start"] - 0.30 * first["dangling"])
    filler_ratio = sum(f["fillers"] for f in feats) / max(1, n_words)
    wps_fit = math.exp(-((wps - 2.7) ** 2) / (2 * 0.8 ** 2))
    conf = float(np.average([f["conf"] for f in feats], weights=[f["n"] for f in feats]))
    scores["clarity"] = _clip01(0.5 * conf + 0.3 * wps_fit + 0.2 * (1 - min(1.0, filler_ratio * 5)))

    lex = max(f["emotion"] for f in feats)
    levels = idx.levels_between(start, end)
    energy_z = (float(levels.mean()) - idx.level_mean) / idx.level_std
    reactions = [e for e in idx.sigs.events if start <= e["t"] <= end]
    reaction = max([max(e.get("laughter", 0), e.get("applause", 0), e.get("cheering", 0)) for e in reactions] or [0.0])
    scores["emotion"] = _clip01(0.5 * lex + 0.25 * _clip01(0.5 + energy_z / 2) + 0.25 * reaction)

    idf_vals = [f["idf"] for f in feats if f["content"]]
    idf_mean = float(np.mean(idf_vals)) if idf_vals else idx.idf_lo
    scores["novelty"] = _clip01((idf_mean - idx.idf_lo) / max(1e-6, idx.idf_hi - idx.idf_lo))

    info = sum(f["info"] for f in feats)
    numbers = sum(f["numbers"] for f in feats)
    density = sum(f["content"] for f in feats) / max(1, n_words)
    cta = sum(f["cta"] for f in feats)
    scores["information"] = _clip01(0.4 * min(1.0, info / 3) + 0.3 * min(1.0, density / 0.5) + 0.3 * min(1.0, numbers / 2)
                                    - 0.35 * min(1, cta))

    last_pause = 0.0
    if idx.sents[j].last + 1 < len(idx.words):
        last_pause = idx.words[idx.sents[j].last + 1]["start"] - idx.sents[j].end
    long_gaps = sum(1 for a, b in idx.sigs.silences if a > start + 0.5 and b < end - 0.5 and b - a > 2.5)
    # A clip should end before the speaker moves on to a new topic.
    topic_shift = any(f["transition"] for f in feats[1:])
    scores["completeness"] = _clip01(0.40 * (not first["dangling"] and not first["filler_start"]) + 0.20
                                     + 0.25 * any(f["conclusion"] for f in feats[-2:])
                                     + 0.15 * (last_pause >= 0.4 or j == len(idx.sents) - 1)
                                     - 0.15 * long_gaps - 0.35 * topic_shift - 0.15 * feats[-1]["cta"])

    if idx.sigs.has_video and idx.sigs.visual:
        vis = [v for v in idx.sigs.visual if start <= v["t"] <= end]
        if vis:
            sharp = float(np.median([v["sharpness"] for v in vis]))
            bright = float(np.median([v["brightness"] for v in vis]))
            face_ratio = sum(1 for v in vis if v["faces"]) / len(vis)
            cuts = sum(1 for s in idx.sigs.scenes if start < s["start"] < end)
            scores["visual"] = _clip01(0.4 * _clip01(math.log10(max(sharp, 1)) / 3) + 0.2 * (40 <= bright <= 215)
                                       + 0.25 * face_ratio + 0.15 * _clip01(1 - cuts / max(1, duration / 4)))
            features_faces = face_ratio
        else:
            scores["visual"] = 0.5
            features_faces = 0.0
    else:
        scores["visual"] = 0.5
        features_faces = 0.0

    silent = sum(max(0.0, min(b, end) - max(a, start)) for a, b in idx.sigs.silences)
    stability = _clip01(1 - float(levels.std()) / 12) if levels.size > 2 else 0.5
    clipping = float(np.mean(levels > -1.0)) if levels.size else 0.0
    scores["audio"] = _clip01(0.5 * (1 - silent / duration) + 0.3 * (1 - clipping * 10) + 0.2 * stability)

    window_vec = idx.sent_vecs[i:j + 1].mean(axis=0)
    norm = float(np.linalg.norm(window_vec)) or 1.0
    window_vec = window_vec / norm
    relevance = 0.5
    if focus_vec is not None:
        relevance = _clip01((float(window_vec @ focus_vec) - 0.35) / 0.4)
    if interest_vecs is not None and len(interest_vecs):
        best = float(np.max(interest_vecs @ window_vec))
        relevance = 0.7 * relevance + 0.3 * _clip01((best - 0.35) / 0.4)
    scores["relevance"] = relevance

    target = req.target_s or min(req.max_s, max(req.min_s, (req.min_s + req.max_s) / 2))
    spread = max(6.0, (req.max_s - req.min_s) / 2.5)
    scores["platform"] = _clip01(math.exp(-((duration - target) ** 2) / (2 * spread ** 2)))

    weights = {k: req.weights.get(k, DEFAULT_WEIGHTS[k]) for k in SCORE_KEYS}
    total = sum(weights[k] * scores[k] for k in SCORE_KEYS) / max(1e-9, sum(weights.values()))
    top = sorted(SCORE_KEYS, key=lambda k: weights[k] * scores[k], reverse=True)
    reasons = [REASONS[k] for k in top if scores[k] >= 0.6][:3]
    return Candidate(
        first=i, last=j, start=start, end=end, text=text, hook=sents[0].text, scores={k: round(v, 3) for k, v in scores.items()},
        total=round(total, 4), reasons=reasons,
        features={"duration": round(duration, 2), "words": n_words, "wps": round(wps, 2), "sentences": len(sents),
                  "first_word": tu.tokens(sents[0].text)[:1], "question_hook": first["question"],
                  "number_in_hook": first["numbers"] > 0, "face_ratio": round(features_faces, 2),
                  "energy_z": round(energy_z, 2), "reactions": round(reaction, 2), "filler_ratio": round(filler_ratio, 3),
                  "_vec": window_vec},
    )


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def generate(words: list[dict], sigs: SourceSignals, req: SelectionRequest, *, max_windows: int = 8000) -> list[Candidate]:
    idx = _Index(words, sigs)
    if not idx.sents:
        return []
    embedder = get_embedder()
    focus_vec = np.asarray(embedder.embed_one(req.focus), dtype=np.float32) if req.focus else None
    interest_vecs = np.asarray(embedder.embed(req.interests[:20]), dtype=np.float32) if req.interests else None
    out: list[Candidate] = []
    for i in range(len(idx.sents)):
        start = idx.sents[i].start
        for j in range(i, len(idx.sents)):
            duration = idx.sents[j].end - start
            if duration > req.max_s:
                break
            if duration >= req.min_s:
                out.append(_window(idx, i, j, req, focus_vec, interest_vecs))
                if len(out) >= max_windows:
                    return out
    if not out:
        # Source shorter than the minimum: offer the whole thing as one clip.
        out.append(_window(idx, 0, len(idx.sents) - 1, req, focus_vec, interest_vecs))
    return out


def validate_context(cand: Candidate, idx_sents: list[tu.Sentence], words: list[dict], req: SelectionRequest) -> Candidate:
    """Fix clips that open mid-thought by pulling in the preceding sentence when it fits."""
    first_tok = tu.tokens(tu.strip_discourse(cand.hook))[:1]
    callback = bool(tu.CALLBACK_RE.search(cand.hook))
    if ((first_tok and first_tok[0] in tu.DANGLING_START) or callback) and cand.first > 0:
        prev = idx_sents[cand.first - 1]
        if cand.end - prev.start <= req.max_s:
            cand.adjusted.append(f"extended start to include context (“{prev.text[:60]}…”)")
            cand.first -= 1
            cand.start = prev.start
            cand.text = prev.text + " " + cand.text
            cand.hook = prev.text
    return cand


class LLMRating(BaseModel):
    id: int
    hook: float = Field(ge=0, le=10)
    standalone: float = Field(ge=0, le=10)
    value: float = Field(ge=0, le=10)
    overall: float = Field(ge=0, le=10)
    title: str = Field(max_length=100)
    reason: str = Field(max_length=200)


class LLMRatings(BaseModel):
    ratings: list[LLMRating]


def llm_rerank(cands: list[Candidate], req: SelectionRequest) -> str | None:
    """Optional: ask one connected model to rate the shortlisted windows."""
    if not cands:
        return None
    listing = "\n\n".join(f"[{n}] ({c.duration:.0f}s) {c.text[:900]}" for n, c in enumerate(cands))
    signals = injection_signals(listing)
    if signals:
        log.warning("Transcript contains instruction-like text (treated as data): %s", signals[:3])
    messages = [
        ChatMessage("system", "You evaluate candidate short-form video clips cut from a longer video. For each "
                              "numbered candidate rate 0-10: hook (first 3 seconds grab attention), standalone (makes "
                              "sense without the rest of the video), value (useful, surprising or entertaining), and "
                              "overall. Suggest a truthful title (no clickbait claims the clip doesn't support) and a "
                              "one-line reason. Return JSON {\"ratings\":[...]} only. " + GUARD_INSTRUCTION),
        ChatMessage("user", (f"Owner's focus: {req.focus}\n\n" if req.focus else "") + fence("transcript_candidates", listing)),
    ]
    try:
        parsed, result = hub.complete_json(
            TaskSpec("clip_rerank", quality=2, temperature=0.2, max_tokens=1500, cache_ttl_s=30 * 24 * 3600,
                     job_id=req.job_id, project_id=req.project_id, max_providers=2), messages, LLMRatings)
    except NoProviderAvailable as exc:
        log.info("LLM re-rank skipped: %s", exc)
        return None
    by_id = {r.id: r for r in parsed.ratings}
    for n, cand in enumerate(cands):
        rating = by_id.get(n)
        if rating is None:
            continue
        cand.llm = rating.model_dump()
        cand.total = round(0.65 * cand.total + 0.35 * (rating.overall / 10), 4)
        cand.method = f"local_heuristic+llm:{result.model}"
        if rating.reason:
            cand.reasons = [rating.reason[:160], *cand.reasons][:3]
    return result.model


def select(words: list[dict], sigs: SourceSignals, req: SelectionRequest) -> tuple[list[Candidate], dict]:
    cands = generate(words, sigs, req)
    stats: dict[str, Any] = {"candidates": len(cands), "llm_model": None}
    if not cands:
        return [], stats
    sents = tu.sentences(words)
    cands.sort(key=lambda c: c.total, reverse=True)
    shortlist: list[Candidate] = []
    for cand in cands:
        if all(_overlap(cand, kept) < 0.3 for kept in shortlist):
            shortlist.append(cand)
        if len(shortlist) >= max(req.count * 3, req.count + 4):
            break
    if req.llm_rerank:
        stats["llm_model"] = llm_rerank(shortlist[: min(12, max(req.count * 2, 6))], req)
        shortlist.sort(key=lambda c: c.total, reverse=True)
    chosen: list[Candidate] = []
    for cand in shortlist:
        if any(_overlap(cand, kept) >= 0.3 for kept in chosen):
            continue
        if any(float(cand.features["_vec"] @ kept.features["_vec"]) > 0.95 for kept in chosen):
            continue
        chosen.append(validate_context(cand, sents, words, req))
        if len(chosen) >= req.count:
            break
    for cand in chosen:
        cand.features.pop("_vec", None)
    for cand in shortlist:
        cand.features.pop("_vec", None)
    stats["shortlist"] = len(shortlist)
    return chosen, stats


def padded_bounds(cand: Candidate, words: list[dict], duration: float) -> tuple[float, float]:
    first_word = words[next(i for i, w in enumerate(words) if w["start"] >= cand.start - 1e-3)] if words else None
    start_idx = words.index(first_word) if first_word else 0
    prev_end = words[start_idx - 1]["end"] if start_idx > 0 else 0.0
    last_idx = max(i for i, w in enumerate(words) if w["end"] <= cand.end + 1e-3)
    next_start = words[last_idx + 1]["start"] if last_idx + 1 < len(words) else duration
    start = max(prev_end + 0.05, cand.start - 0.25, 0.0)
    end = min(next_start - 0.05, cand.end + 0.4, duration)
    return round(start, 3), round(max(end, cand.end), 3)
