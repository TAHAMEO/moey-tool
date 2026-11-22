"""Packaging variants: 2-3 ranked title / first-frame text pairs per clip, each with a
keyword-rich description and hashtags per platform.

Shorts feeds don't show a custom thumbnail, so the first frame *is* the thumbnail: its text is
1-2 big words (readable at ~200 px wide). Titles are 30-50 characters, keyword first, promise
second. Every content word of a title or first-frame text must be spoken in the clip; the only
words added are a few framing words ("why", "how", "here's") — never a fact, number or claim.
Ranking is by rules today (length, keyword first, curiosity, promise, readability); the learning
engine can add a per-template boost later (``boost``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..media import textutils as tu
from . import hook as shook

#: Words a title may add that aren't in the transcript: framing, no claims.
FRAMING_WORDS = {"why", "how", "here's", "heres", "what", "this", "the", "is", "about"}
WEAK_HOOK = {"one", "first", "last", "number", "only", "best"}
CONTRAST = {"not", "don't", "never", "stop", "instead", "wrong", "without", "no"}


@dataclass
class PackagingConfig:
    title_min: int = 30
    title_max: int = 50
    variants: int = 3
    first_frame_words: int = 2
    excerpt_chars: int = 280


@dataclass
class Variant:
    rank: int
    title: str
    first_frame_text: str | None
    template: str
    score: float
    parts: dict[str, float]
    youtube_description: str
    instagram_caption: str
    hashtags: dict[str, list[str]]
    reasons: list[str] = field(default_factory=list)
    burned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _clean(sentence: str) -> str:
    core = shook._strip_fillers(tu.strip_discourse(sentence)).strip()
    return re.sub(r"\s+", " ", core).rstrip(".!…").strip()


def grounded_title(title: str, text: str) -> bool:
    spoken = set(tu.tokens(text))
    return all(t in spoken or t in FRAMING_WORDS for t in tu.tokens(title) if t not in tu.STOPWORDS)


def content_text(text: str) -> str:
    """The clip without greetings and filler-only lines (a "today we'll talk about X" line stays:
    it names the topic)."""
    kept = [s for s in split_sentences(text)
            if not shook.GREETING_RE.match(shook._strip_fillers(s)) and not META_RE.search(s)
            and len(tu.content_words(s)) >= 1]
    return " ".join(kept) or text


TOPIC_RE = re.compile(r"\b(talk(?:ing)? about|(?:video|episode) is about|let'?s (?:talk|look) at)\s+([a-z0-9']+(?:\s+[a-z0-9']+){0,2})",
                      re.IGNORECASE)
#: Lines about the channel, not the topic ("if you found this helpful, subscribe").
META_RE = re.compile(r"\b(this video|this channel|my channel|subscribe|hit the bell|link (in|below)|comment below)\b", re.IGNORECASE)


def _stem(token: str) -> str:
    for suffix in ("ing", "es", "s", "e", "ed"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def focus_keyword(text: str) -> str:
    """The clip's keyword (1-2 words): the content word said most often (word forms counted
    together), with a bonus for what the speaker says the clip is about; a two-word key phrase
    wins when both of its words are repeated."""
    body = content_text(text)
    words = tu.content_words(body)
    if not words:
        return ""
    counts: dict[str, int] = {}
    first_form: dict[str, str] = {}
    for w in words:
        counts[_stem(w)] = counts.get(_stem(w), 0) + 1
        first_form.setdefault(_stem(w), w)
    bonus: dict[str, float] = {}
    for match in TOPIC_RE.finditer(body):
        for w in tu.content_words(match.group(2)):
            bonus[_stem(w)] = bonus.get(_stem(w), 0) + 2.0
    order = {stem: i for i, stem in reversed(list(enumerate(_stem(w) for w in words)))}

    def score(stem: str) -> float:
        return counts[stem] + bonus.get(stem, 0.0) + 0.2 * (len(stem) >= 5) - 0.01 * order[stem]

    best = max(counts, key=score)
    for phrase in tu.keywords(body, top_k=8):
        parts = phrase.split()
        if len(parts) == 2 and all(counts.get(_stem(p), 0) >= 2 for p in parts) and _stem(best) in {_stem(p) for p in parts}:
            return phrase
    return first_form[best]


def fit(prefix: str, body: str, cfg: PackagingConfig) -> str:
    """``prefix + body`` cut on a word boundary to at most ``title_max`` characters, never ending
    on a small word."""
    words = body.split()
    out = prefix + " ".join(words)
    while len(out) > cfg.title_max and len(words) > 1:
        words.pop()
        while len(words) > 1 and tu.tokens(words[-1]) and tu.tokens(words[-1])[-1] in (tu.STOPWORDS | shook.LEAD_FILLERS):
            words.pop()
        out = prefix + " ".join(words)
    return out.rstrip(",;:—- ")


def _strength(sentence: str) -> float:
    core = tu.strip_discourse(sentence)
    toks = tu.tokens(core)
    if not toks or shook.is_runway(sentence) or META_RE.search(sentence) or tu.CTA_RE.search(sentence):
        return -1.0
    return (2.0 * bool(tu.HOOK_OPENERS.search(core)) + 1.5 * ("?" in sentence) + sum(1 for t in toks if t in tu.HOOK_WORDS)
            + 0.8 * bool(tu.NUMBER_RE.search(core)) + 0.4 * sum(1 for t in toks if t in CONTRAST)
            - 0.05 * max(0, len(toks) - 12))


def title_candidates(text: str, *, llm_title: str | None = None, cfg: PackagingConfig | None = None) -> list[tuple[str, str]]:
    """(title, template) candidates, built from the clip's own lines."""
    cfg = cfg or PackagingConfig()
    keyword = focus_keyword(text)
    kw = tu.title_case(keyword) if keyword else ""
    prefix = f"{kw}: " if kw else ""
    sents = [s for s in split_sentences(text) if _strength(s) >= 0]
    ranked = sorted(sents, key=_strength, reverse=True)
    out: list[tuple[str, str]] = []

    def add(title: str, template: str) -> None:
        title = re.sub(r"\s+", " ", title).strip()
        if title and title.lower() not in {t.lower() for t, _ in out}:
            out.append((title, template))

    for n, sent in enumerate(ranked[:2]):
        body = _clean(sent)
        if keyword and body.lower().startswith(keyword.lower()):
            add(fit("", _cap(body), cfg), "keyword_line")
        else:
            add(fit(prefix, _cap(body), cfg), "keyword_promise" if n == 0 else "keyword_second_line")
    question = next((s for s in ranked if s.rstrip().endswith("?")), None)
    if question:
        add(fit(f"{kw} — " if kw else "", _cap(_clean(question)) + "?", cfg), "keyword_question")
    if shook.ANSWER_RE.search(text) and ranked:
        claim = _clean(ranked[0])
        if not claim.endswith("?") and tu.tokens(claim)[:1] != ["why"]:
            lowered = claim[:1].lower() + claim[1:]
            add(fit(f"{prefix}Why " if kw else "Why ", lowered, cfg), "keyword_why")
    number = next((s for s in ranked if tu.NUMBER_RE.search(tu.strip_discourse(s))), None)
    if number and number not in ranked[:2]:
        add(fit(prefix, _cap(_clean(number)), cfg), "keyword_number")
    if llm_title:
        clean = llm_title.replace("#shorts", "").replace("#Shorts", "").strip().rstrip(".")
        if kw and not clean.lower().startswith(keyword.lower()):
            clean = fit(prefix, clean, cfg)
        add(clean, "model")
    return out


def score_title(title: str, text: str, keyword: str, cfg: PackagingConfig | None = None) -> tuple[float, dict, list[str]]:
    cfg = cfg or PackagingConfig()
    reasons: list[str] = []
    n = len(title)
    length = 1.0 if cfg.title_min <= n <= cfg.title_max else max(0.0, 1 - min(abs(n - cfg.title_min), abs(n - cfg.title_max)) / 20)
    if length < 1:
        reasons.append(f"{n} characters (aim for {cfg.title_min}-{cfg.title_max})")
    first = 1.0 if keyword and title.lower().startswith(keyword.lower()) else 0.3 if keyword and keyword.lower() in title.lower()[:25] else 0.0
    if first < 1:
        reasons.append("keyword is not first")
    toks = tu.tokens(title)
    curiosity = min(1.0, 0.5 * ("?" in title) + 0.4 * bool(set(toks) & {"why", "how", "what"})
                    + 0.3 * len(set(toks) & tu.HOOK_WORDS) + 0.3 * bool(tu.NUMBER_RE.search(title))
                    + 0.3 * bool(set(toks) & CONTRAST))
    kw_toks = set(tu.tokens(keyword))
    promise = 1.0 if len([t for t in tu.content_words(title) if t not in kw_toks]) >= 2 else 0.3
    if promise < 1:
        reasons.append("no promise after the keyword")
    words = title.split()
    readable = 1.0 if words and sum(len(w) for w in words) / len(words) <= 7 else 0.6
    parts = {"length": round(length, 3), "keyword_first": first, "curiosity": round(curiosity, 3),
             "promise": promise, "readable": readable}
    weights = {"length": 0.25, "keyword_first": 0.2, "curiosity": 0.25, "promise": 0.15, "readable": 0.15}
    return round(sum(weights[k] * parts[k] for k in weights), 3), parts, reasons


def first_frame_texts(text: str, cfg: PackagingConfig | None = None) -> list[str]:
    """1-2 word first-frame texts, best first: a word or two words *next to each other* in one of
    the clip's strongest lines (a phrase the speaker said, so it reads on its own)."""
    from .captions import power_scores

    cfg = cfg or PackagingConfig()
    keyword = focus_keyword(text)
    power = power_scores(content_text(text), keywords=[keyword] if keyword else [])
    focus = set(tu.tokens(keyword))
    scored: dict[str, float] = {}
    for sent in split_sentences(text):
        strength = _strength(sent)
        if strength < 0:
            continue
        raw = re.findall(r"[A-Za-z0-9'%$]+", tu.strip_discourse(sent))
        for size in range(1, cfg.first_frame_words + 1):
            for i in range(len(raw) - size + 1):
                gram = raw[i:i + size]
                low = [g.lower() for g in gram]
                if any(t in tu.STOPWORDS and t not in {"never", "no", "not", "stop", "why", "how"} for t in low):
                    continue
                vals = [min(0.6, power.get(t, 0.0)) if t.isdigit() or tu.NUMBER_RE.fullmatch(t) else power.get(t, 0.0)
                        for t in low]
                if min(vals) <= 0 and not set(low) & {"never", "no", "not", "stop", "why", "how"}:
                    continue
                hooks = sum(1 for t in low if t in tu.HOOK_WORDS and t not in WEAK_HOOK)
                value = sum(vals) + 0.25 * strength + 0.3 * bool(set(low) & focus) + 0.3 * hooks
                phrase = " ".join(gram).upper()
                scored[phrase] = max(scored.get(phrase, 0.0), round(value, 3))
    ranked = sorted(scored, key=lambda p: (-scored[p], len(p)))
    out: list[str] = []
    for phrase in ranked:
        if not any(set(phrase.split()) <= set(o.split()) for o in out):
            out.append(phrase)
        if len(out) >= 5:
            break
    return out


def excerpt(text: str, keywords: list[str], max_chars: int = 280) -> str:
    """The run of consecutive sentences with the most keyword hits that fits ``max_chars``."""
    sents = [_cap(_clean(s)) + (s.strip()[-1] if s.strip()[-1:] in ".!?" else ".") for s in split_sentences(text)
             if len(s.split()) >= 4 and not shook.is_runway(s)]
    kw = {t for k in keywords for t in tu.tokens(k)}
    best, best_hits = "", -1
    for i in range(len(sents)):
        chunk = ""
        for s in sents[i:]:
            if len(chunk) + len(s) + 1 > max_chars:
                break
            chunk = f"{chunk} {s}".strip()
            hits = sum(1 for t in tu.tokens(chunk) if t in kw)
            if hits > best_hits:
                best, best_hits = chunk, hits
    return best or (text or "")[:max_chars].strip()


def _hashtag(phrase: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", phrase)
    return "#" + "".join(w[:1].upper() + w[1:] for w in words)[:30] if words else ""


def hashtags(keywords: list[str], text: str = "") -> dict[str, list[str]]:
    """Topic hashtags from the clip's own key phrases, per platform: the focus keyword (first) and
    1-2 word phrases with a word the speaker repeats."""
    counts: dict[str, int] = {}
    for w in tu.content_words(content_text(text)) if text else []:
        counts[_stem(w)] = counts.get(_stem(w), 0) + 1
    picked = [k for n, k in enumerate(keywords)
              if len(k.split()) <= 2 and (n == 0 or not text or any(counts.get(_stem(w), 0) >= 2 for w in k.split()))]
    topic = [h for h in dict.fromkeys(_hashtag(k) for k in picked) if len(h) > 3]
    return {"youtube": ["#shorts", *topic[:2]], "instagram": [*topic[:4], "#reels"]}


def build(text: str, *, llm_title: str | None = None, first_frames: list[str] | None = None,
          burned: str | None = None, channel: str = "", boost: dict[str, float] | None = None,
          cfg: PackagingConfig | None = None) -> list[Variant]:
    """2-3 ranked variants. ``first_frames`` is the ranked first-frame text list chosen at render
    time; ``burned`` the one actually on frame one (it goes with the best title)."""
    cfg = cfg or PackagingConfig()
    keyword = focus_keyword(text)
    keywords = tu.keywords(content_text(text), top_k=8)
    if keyword:
        keywords = [keyword] + [k for k in keywords if k != keyword]
    tags = hashtags(keywords, text)
    body = excerpt(text, keywords, cfg.excerpt_chars)
    scored = []
    for title, template in title_candidates(text, llm_title=llm_title, cfg=cfg):
        if not grounded_title(title, text):
            continue  # a word the speaker never said: not used
        score, parts, reasons = score_title(title, text, keyword, cfg)
        score = round(score + (boost or {}).get(template, 0.0), 3)
        scored.append((score, title, template, parts, reasons))
    scored.sort(key=lambda x: -x[0])
    frames = list(first_frames or first_frame_texts(text, cfg))
    if burned:
        frames = [burned] + [f for f in frames if f != burned]
    follow = f"\n\nFollow {channel} for more." if channel else ""
    variants = []
    for rank, (score, title, template, parts, reasons) in enumerate(scored[:cfg.variants], start=1):
        frame = frames[rank - 1] if rank - 1 < len(frames) else (frames[0] if frames else None)
        variants.append(Variant(
            rank=rank, title=title, first_frame_text=frame, template=template, score=score, parts=parts,
            youtube_description=f"{title}\n\n{body}\n\n{' '.join(tags['youtube'])}",
            instagram_caption=f"{title}\n\n{body}{follow}\n\n{' '.join(tags['instagram'])}",
            hashtags=tags, reasons=reasons, burned=bool(burned) and frame == burned and rank == 1))
    return variants
