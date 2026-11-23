"""Originality guardrail: before a clip goes out automatically, compare how it is *made* with the
channel's last published clips, so the feed doesn't fill up with the same template.

A clip's fingerprint is what a viewer would recognise as a template: the kind of hook it opens
with, its structure (opening fix, question first, re-hook and its line, loop, length), the caption
style, the title's shape, and the title's words. Two checks:

* **near duplicate** — weighted similarity with any of the last N clips at or above the threshold
  (same template *and* largely the same words);
* **template streak** — the same template (everything except the words) as each of the last K
  clips in a row.

When a clip is flagged the gate first tries the clip's other title options (P1 packaging
variants); if one breaks the pattern it is used. Otherwise the clip is held for review — it is
never published automatically. Nothing here rewrites what the speaker said.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..media import textutils as tu

#: The template alone (everything but the words) adds up to 0.75, so a near duplicate (threshold
#: 0.85) needs largely the same words too; a repeated template is the streak check's job.
WEIGHTS = {"hook_type": 0.2, "structure": 0.2, "caption": 0.1, "title_pattern": 0.2, "title_words": 0.25, "texts": 0.05}


@dataclass
class OriginalityConfig:
    threshold: float = 0.85
    compare_last: int = 10
    streak: int = 5


@dataclass
class Fingerprint:
    hook_type: str
    structure: list[str]
    caption: str
    title_pattern: list[str]
    title_words: list[str]
    texts: list[str]
    title: str = ""
    ref: str = ""  # clip / publication this describes

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OriginalityResult:
    flagged: bool
    max_similarity: float
    reasons: list[str] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)
    compared: int = 0
    switched_to: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def hook_type(text: str) -> str:
    """What kind of opening this is (question, number, warning, how-to, story, curiosity, statement)."""
    core = tu.strip_discourse(text or "").lower()
    toks = tu.tokens(core)
    if not toks:
        return "none"
    if "?" in core or toks[0] in {"why", "how", "what", "who", "when", "where", "which", "is", "are", "do", "does", "can"}:
        return "question"
    if re.match(r"(here'?s|here is|this is) how|how to\b", core):
        return "how_to"
    if set(toks) & {"mistake", "mistakes", "stop", "never", "wrong", "don't", "avoid", "warning"}:
        return "warning"
    if re.match(r"i (was|made|lost|found|quit|tried|spent|got|had)\b|when i\b|last (year|week|month)", core):
        return "story"
    if tu.NUMBER_RE.search(core) and any(t.isdigit() or t in {"two", "three", "four", "five", "ten", "percent"} for t in toks):
        return "number"
    if set(toks) & {"secret", "truth", "nobody", "everyone", "actually", "real", "hidden"}:
        return "curiosity"
    return "statement"


def title_pattern(title: str, keyword: str = "", template: str = "") -> list[str]:
    """The shape of a title, without its words: prefix style, punctuation, opener, length."""
    t = (title or "").replace("#shorts", "").strip()
    toks = tu.tokens(t)
    out = [f"template:{template or 'none'}"]
    # "Keyword: promise" and "Label: promise" look the same in a feed.
    labelled = (keyword and t.lower().startswith(keyword.lower())) or re.match(r"^[^:—-]{2,24}[:—-]\s", t)
    out.append("prefix:label" if labelled else "prefix:none")
    out.append(f"opener:{toks[0] if toks and toks[0] in {'why', 'how', 'what', 'stop', 'never', 'the', 'this', 'here'} else 'other'}")
    out.append("question" if "?" in t else "statement")
    out.append("number" if tu.NUMBER_RE.search(t) else "no_number")
    out.append(f"words:{min(len(toks) // 3, 4)}")
    return out


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def fingerprint(clip, package, *, caption_style: str | None, duration: float, ref: str = "") -> Fingerprint:
    """The template of one clip as posted (``clip`` and ``package`` are DB rows or look-alikes)."""
    shorts = clip.shorts or {}
    hook = shorts.get("hook") or {}
    rehook = shorts.get("retention_plan") or {}
    loop = shorts.get("loop") or {}
    variants = shorts.get("packaging_variants") or []
    title = (package.title or (package.caption or "").split("\n")[0]).replace("#shorts", "").strip()
    used = next((v for v in variants if v.get("title") == title), None)
    template = used["template"] if used else (package.metadata_method or "").split(":")[-1].split("+")[0]
    keyword = ((shorts.get("seo") or {}).get("keyword")) or ""
    structure = [
        f"hook:{hook.get('status', 'none')}",
        "order:question_first" if len(hook.get("keep") or []) > 1 else "order:as_said",
        f"rehook:{rehook.get('text_kind') or ('zoom' if rehook.get('zoom') else 'none')}",
        f"loop:{'blend' if loop.get('bridged') else 'end' if loop else 'none'}",
        f"len:{'<20' if duration < 20 else '20-35' if duration < 35 else '35-60' if duration < 60 else '60+'}",
        f"frame_text:{'yes' if shorts.get('first_frame_text') or hook.get('text_hook') else 'no'}",
    ]
    texts = [t for t in (shorts.get("first_frame_text") or hook.get("text_hook"),
                         rehook.get("text") if rehook.get("text_kind") == "framing" else None) if t]
    return Fingerprint(hook_type=hook_type(clip.hook or ""), structure=structure, caption=caption_style or "none",
                       title_pattern=title_pattern(title, keyword, template),
                       title_words=sorted({_stem(w) for w in tu.content_words(title)}), texts=texts, title=title, ref=ref)


def _jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def similarity(a: Fingerprint, b: Fingerprint) -> tuple[float, dict]:
    parts = {
        "hook_type": float(a.hook_type == b.hook_type),
        "structure": _jaccard(a.structure, b.structure),
        "caption": float(a.caption == b.caption),
        "title_pattern": _jaccard(a.title_pattern, b.title_pattern),
        "title_words": _jaccard(a.title_words, b.title_words),
        "texts": _jaccard([t.upper() for t in a.texts], [t.upper() for t in b.texts]) if (a.texts or b.texts) else 0.0,
    }
    return round(sum(WEIGHTS[k] * v for k, v in parts.items()), 3), {k: round(v, 2) for k, v in parts.items()}


def _core(pattern: list[str]) -> list[str]:
    """The template part of a title's shape (not length or whether a number appears)."""
    return [p for p in pattern if p.split(":")[0] in {"template", "prefix", "opener"} or p in {"question", "statement"}]


def same_template(a: Fingerprint, b: Fingerprint) -> bool:
    return a.hook_type == b.hook_type and a.structure == b.structure and a.caption == b.caption \
        and _core(a.title_pattern) == _core(b.title_pattern)


def check(candidate: Fingerprint, recent: list[Fingerprint], cfg: OriginalityConfig | None = None) -> OriginalityResult:
    """``recent`` = the channel's last published clips on this platform, newest first."""
    cfg = cfg or OriginalityConfig()
    recent = recent[: cfg.compare_last]
    result = OriginalityResult(flagged=False, max_similarity=0.0, compared=len(recent))
    for other in recent:
        score, parts = similarity(candidate, other)
        result.max_similarity = max(result.max_similarity, score)
        if score >= cfg.threshold:
            result.matches.append({"ref": other.ref, "title": other.title, "similarity": score, "parts": parts})
    if result.matches:
        best = max(result.matches, key=lambda m: m["similarity"])
        result.reasons.append(f"too similar to a recent post (“{best['title'][:60]}”, {best['similarity']:.0%} alike)")
    streak = recent[: cfg.streak]
    if cfg.streak and len(streak) >= cfg.streak and all(same_template(candidate, other) for other in streak):
        result.reasons.append(f"same template as your last {cfg.streak} posts (hook type, structure, captions and title shape)")
    result.flagged = bool(result.reasons)
    return result
