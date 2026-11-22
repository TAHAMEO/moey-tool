"""Power words for kinetic captions: which 1-3 words of each caption line get colour and a pop.

A word earns emphasis by what it carries, not by being spoken right now: numbers, emotional
words, hook words ("mistake", "never"), the clip's focus keyword, and content words that are rare
in the rest of the video (inverse document frequency over the source transcript's sentences).
Small words ("the", "and", "so") never light up.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..media import textutils as tu

#: Below this a word is not worth highlighting on its own.
MIN_POWER = 0.5
#: Hook words that are too common to be worth a highlight on their own.
WEAK_HOOK_WORDS = {"one", "first", "last", "number", "only", "how", "why", "best"}
#: Never highlighted: channel talk and small verbs the stopword list doesn't cover.
NEVER = {"will", "can", "could", "would", "shall", "may", "might", "must", "get", "got", "thing", "things", "video",
         "videos", "channel", "subscribe", "comment", "follow", "like", "link", "bell", "description", "today"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9'%$]", "", text.lower()).strip("'")


def is_number(token: str) -> bool:
    return bool(re.search(r"\d", token)) or bool(tu.NUMBER_RE.fullmatch(token)) and token not in {"one"}


def power_scores(text: str, *, keywords: list[str] | None = None, idf: dict[str, float] | None = None) -> dict[str, float]:
    """Score (0-1) every distinct word of a clip's transcript."""
    from .packaging import content_text, focus_keyword

    if keywords is None:
        keywords = [focus_keyword(text)]
    focus = {t for phrase in keywords[:1] for t in tu.tokens(phrase)}
    # Words only said in channel talk ("subscribe for more") score 0: they are never highlighted.
    toks = {_norm(t) for t in content_text(text).split()} - {""}
    if idf:
        known = [idf[t] for t in toks if t in idf]
        lo, hi = (min(known), max(known)) if known else (0.0, 1.0)
    scores: dict[str, float] = {}
    for tok in toks:
        if tok in tu.STOPWORDS or tok in tu.FILLERS or tok in NEVER:
            scores[tok] = 0.0
            continue
        score = 0.0
        if is_number(tok):
            score = 1.0
        if tok in tu.EMOTION:
            score = max(score, 0.6 + 0.4 * tu.EMOTION[tok])
        if tok in focus:
            score = max(score, 0.85)
        if tok in tu.HOOK_WORDS:
            score = max(score, 0.45 if tok in WEAK_HOOK_WORDS else 0.7)
        if len(tok) > 3:
            if idf and tok in idf and hi > lo:
                rarity = (idf[tok] - lo) / (hi - lo)
            else:
                rarity = min(1.0, (len(tok) - 3) / 7)  # no reference text: longer words carry more
            score = max(score, 0.3 + 0.45 * rarity)
        scores[tok] = round(score, 3)
    for tok in {_norm(t) for t in text.split()} - set(scores) - {""}:
        scores[tok] = 0.0
    return scores


def pick(tokens: list[str], scores: dict[str, float], *, max_words: int | None = None) -> list[int]:
    """Indices of the power words in one caption line: 1 for lines of up to 3 words, 2 for longer
    ones (never more than 3). A line with no word above ``MIN_POWER`` still lights its best
    content word, so every line has its emphasis; a line of only small words gets none."""
    limit = max_words or (1 if len(tokens) <= 3 else 2)
    limit = max(1, min(3, limit))
    ranked = sorted(((scores.get(_norm(t), 0.0), i) for i, t in enumerate(tokens)), key=lambda x: (-x[0], x[1]))
    chosen = [i for s, i in ranked if s >= MIN_POWER][:limit]
    if not chosen and ranked and ranked[0][0] > 0:
        chosen = [ranked[0][1]]
    return sorted(chosen)


def emphasis(scores: dict[str, float]) -> Callable[[list[str]], list[int]]:
    """A chooser for ``vendor.autoclip.captions.build_ass(emphasis=...)``."""
    return lambda tokens: pick(tokens, scores)


def source_idf(words: list[dict]) -> dict[str, float]:
    """Word rarity over the whole source (its sentences are the documents)."""
    return tu.idf_table([s.text for s in tu.sentences(words)]) if words else {}
