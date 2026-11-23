"""Local, deterministic request understanding (no model required).

Covers the operational vocabulary of the product — links, counts, platforms,
durations, aspect ratios, publishing, status, memory, controls — so the Brain
works fully offline. An LLM is consulted only when this parser is unsure.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

URL_RE = re.compile(
    r"(https?://[^\s<>\"'`]+|(?<![\w/])(?:www\.|m\.)?(?:youtube\.com|youtu\.be|instagram\.com|drive\.google\.com)/[^\s<>\"'`]+)",
    re.IGNORECASE,
)
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "a couple of": 2, "a couple": 2,
    "a few": 3, "several": 5, "a dozen": 12,
}
_NUM = r"(\d{1,2}|" + "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True)) + r")"
COUNT_RE = re.compile(_NUM + r"\s+(?:\w+\s+){0,3}?(shorts?|clips?|reels?|videos?|highlights?|moments?|cuts?)\b", re.IGNORECASE)
DURATION_MAX_RE = re.compile(r"(?:under|less than|max(?:imum)?|at most|no longer than|up to|<)\s*(\d{1,3})\s*(s|sec|secs|seconds|m|min|mins|minutes)\b", re.IGNORECASE)
DURATION_RANGE_RE = re.compile(r"(\d{1,3})\s*(?:-|to|–)\s*(\d{1,3})\s*(s|sec|secs|seconds|m|min|mins|minutes)\b", re.IGNORECASE)
DURATION_MIN_RE = re.compile(r"(?:at least|over|more than|min(?:imum)?)\s*(\d{1,3})\s*(s|sec|secs|seconds|m|min|mins|minutes)\b", re.IGNORECASE)
FOCUS_RE = re.compile(r"\b(?:about|on|focus(?:ing)? on|related to|where (?:he|she|they) talks? about)\s+(?:the\s+)?([\w\s'-]{3,60}?)(?:[.,;!?]|$|\s+(?:from|in|for|and|with)\b)", re.IGNORECASE)


# "all videos in the Podcast folder", "the folder called Gaming", "my 'Interviews' folder", "watch the Vlog folder"
_FOLDER_NAMED = re.compile(r"\bfolder\s+(?:called|named)\s+[\"'“‘]?(?P<name>[\w][\w .&-]{0,58}?)[\"'”’]?(?=$|[\s,.;:!?])",
                           re.IGNORECASE)
_FOLDER_QUOTED = re.compile(r"\bfolder\s+[\"'“‘](?P<name>[^\"'”’]{1,60})[\"'”’]", re.IGNORECASE)
_FOLDER_BEFORE = re.compile(
    r"\b(?:in|from|of|watch|process|use)\s+(?:all\s+(?:the\s+)?(?:videos?\s+)?(?:in\s+)?)?(?:the\s+|my\s+)?"
    r"[\"'“‘]?(?P<name>[\w][\w .&-]{0,58}?)[\"'”’]?\s+folder\b", re.IGNORECASE)
_NOT_A_NAME = {"the", "my", "a", "this", "that", "your", "inbox", "watched", "same", "new", "whole", "every", "each"}


def folder_name(text: str) -> str | None:
    """The folder a chat message names, if any ("process all videos in the Podcast folder" → Podcast)."""
    for pattern in (_FOLDER_NAMED, _FOLDER_QUOTED, _FOLDER_BEFORE):
        for match in pattern.finditer(text or ""):
            name = match.group("name").strip().strip(".")
            if name and name.lower() not in _NOT_A_NAME:
                return name
    return None


# --- references to existing clips -------------------------------------------
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9, "tenth": 10}


def clip_refs(text: str) -> list[int]:
    """Clip numbers named in a request; [0] for every clip."""
    lower = text.lower()
    if re.search(r"\b(all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+)?(clips?|shorts?|reels?|videos?)\b|\ball of them\b", lower):
        return [0]
    refs = [int(n) for n in re.findall(r"\b(?:clip|short|reel|video|#)\s*#?(\d{1,2})\b", lower)]
    refs += [_ORDINALS[w] for w in re.findall(r"\b(" + "|".join(_ORDINALS) + r")\s+(?:clip|short|reel|one)\b", lower)]
    if re.search(r"\b(last|final)\s+(?:clip|short|reel|one)\b", lower):
        refs.append(-1)
    return list(dict.fromkeys(refs))


def wants_edit(text: str) -> bool:
    """Does this message ask to change or judge existing clips (rather than make new ones)?"""
    lower = text.lower()
    signal = re.search(
        r"\b(shorter|longer|trim|extend|re-?render|render (it|them|again)|rename|retitle|title|captions?|subtitles?"
        r"|start (later|earlier)|end (later|earlier|sooner)|cut (the )?(first|last|opening|ending|beginning|end)"
        r"|improve|better|feedback|review|weak(est)?|what('?s| is) wrong)\b", lower)
    reference = clip_refs(text) or re.search(
        r"\b(the|this|that|these|those|my)\s+(clips?|shorts?|reels?)\b|\b(it|them)\b", lower)
    return bool(signal and reference)


@dataclass
class ParsedRequest:
    intent: str
    text: str
    urls: list[str] = field(default_factory=list)
    count: int | None = None
    platforms: list[str] = field(default_factory=list)
    aspects: list[str] = field(default_factory=list)
    min_seconds: float | None = None
    max_seconds: float | None = None
    publish: bool = False
    live_hint: bool = False
    focus: str | None = None
    caption_style: str | None = None
    burn_captions: bool | None = None
    languages: list[str] = field(default_factory=list)
    control_action: str | None = None
    folder: str | None = None
    clip_refs: list[int] = field(default_factory=list)
    confidence: float = 0.9

    def to_dict(self) -> dict:
        return asdict(self)


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(").,;!?]'\"")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if url not in urls:
            urls.append(url)
    return urls


def _seconds(value: str, unit: str) -> float:
    return float(value) * (60 if unit.lower().startswith("m") else 1)


def _count(text: str) -> int | None:
    match = COUNT_RE.search(text)
    if not match:
        return None
    raw = match.group(1).lower()
    value = NUMBER_WORDS.get(raw) or (int(raw) if raw.isdigit() else None)
    return max(1, min(30, value)) if value else None


def _platforms(lower: str) -> list[str]:
    platforms = []
    if re.search(r"\b(shorts?|youtube|yt)\b", lower):
        platforms.append("youtube")
    if re.search(r"\b(reels?|instagram|insta|ig)\b", lower):
        platforms.append("instagram")
    if re.search(r"\b(both|all platforms|everywhere|both platforms)\b", lower):
        platforms = ["youtube", "instagram"]
    return platforms


def _aspects(lower: str) -> list[str]:
    aspects = []
    if re.search(r"\b(9:16|vertical|portrait|shorts?|reels?|tiktok)\b", lower):
        aspects.append("9:16")
    if re.search(r"\b(1:1|square)\b", lower):
        aspects.append("1:1")
    if re.search(r"\b(16:9|landscape|horizontal|widescreen)\b", lower):
        aspects.append("16:9")
    return aspects


def _durations(text: str) -> tuple[float | None, float | None]:
    if m := DURATION_RANGE_RE.search(text):
        return _seconds(m.group(1), m.group(3)), _seconds(m.group(2), m.group(3))
    low = high = None
    if m := DURATION_MAX_RE.search(text):
        high = _seconds(m.group(1), m.group(2))
    if m := DURATION_MIN_RE.search(text):
        low = _seconds(m.group(1), m.group(2))
    return low, high


def parse(text: str, *, has_attachments: bool = False) -> ParsedRequest:
    text = (text or "").strip()
    urls = extract_urls(text)
    # Keyword detection ignores the links themselves (a youtube.com link says
    # where the source is, not where the clips should be published).
    lower = URL_RE.sub(" ", text).lower()
    base = {"text": text, "urls": urls}

    # --- global controls ---------------------------------------------------
    if re.search(r"\b(emergency stop|stop everything|kill switch|stop all (jobs|automation|publishing))\b", lower):
        return ParsedRequest("control", control_action="emergency_stop", **base)
    # Publishing only (processing continues): "let's hold off posting for now", "pause publishing".
    if re.search(r"\b(hold off( on)?|stop|pause|halt|no more)\s+(posting|publishing|uploading)\b"
                 r"|\b(don'?t|do not)\s+(post|publish|upload)\s+anything\s+(for now|yet|until)\b", lower):
        return ParsedRequest("control", control_action="hold_publishing", **base)
    if re.search(r"\b(resume|restart|start|continue)\s+(posting|publishing|uploading)\b|\b(posting|publishing) again\b",
                 lower):
        return ParsedRequest("control", control_action="resume_publishing", **base)
    if re.search(r"\b(pause|halt|freeze)\s+(all|everything|automation|jobs|work)\b", lower):
        return ParsedRequest("control", control_action="pause", **base)
    if re.search(r"\b(resume|unpause|continue)\s+(all|everything|automation|jobs|work)\b", lower):
        return ParsedRequest("control", control_action="resume", **base)

    clipish = bool(re.search(r"\b(clip|short|reel|highlight|cut|edit|analy[sz]e|process|watch|make|create|turn|find|best|moments?|repurpose)", lower))
    # "this upload" / "the uploaded video" name the file sent, they don't ask to publish it.
    verbs = re.sub(r"\b(this|the|that|my|an?|your)\s+(upload|uploaded( \w+)?)\b|\buploaded\b", " ", lower)
    publish = bool(re.search(r"\b(publish|post|upload|share it|go live with|schedule)\b", verbs)) and not re.search(r"\b(don'?t|do not|never|without)\s+(publish|post|upload)", lower)
    low, high = _durations(text)
    focus = None
    if m := FOCUS_RE.search(text):
        candidate = m.group(1).strip()
        if not re.search(r"(youtube|instagram|http|www\.)", candidate, re.IGNORECASE) and len(candidate.split()) <= 8:
            focus = candidate
    caption_style = None
    for style, pattern in (("kinetic", r"kinetic|animated captions"), ("karaoke_fill", r"karaoke"), ("boxed", r"boxed|box(ed)? captions"),
                           ("clean_lower", r"clean|minimal|lower[- ]third"), ("bold_pop", r"bold|pop|big captions")):
        if re.search(pattern, lower) and re.search(r"caption|subtitle", lower):
            caption_style = style
            break
    burn = False if re.search(r"\b(no|without)\s+(burned[- ]in\s+)?(captions|subtitles)\b", lower) else None
    languages = re.findall(r"\b(english|spanish|french|german|portuguese|arabic|hindi|urdu|italian|japanese)\s+(?:subtitles|captions)\b", lower)

    folder = folder_name(URL_RE.sub(" ", text))
    if folder and not urls and not has_attachments and (clipish or publish or re.search(r"\b(watch|process|all videos)\b", lower)):
        return ParsedRequest("process_folder", folder=folder, count=_count(text), platforms=_platforms(lower), aspects=_aspects(lower),
                             min_seconds=low, max_seconds=high, publish=publish, focus=focus, caption_style=caption_style,
                             burn_captions=burn, languages=languages, **base)

    if urls or has_attachments:
        return ParsedRequest(
            "process_source", count=_count(text), platforms=_platforms(lower), aspects=_aspects(lower),
            min_seconds=low, max_seconds=high, publish=publish,
            live_hint=bool(re.search(r"\b(live|livestream|live stream|stream)\b", lower)) or any("/live/" in u for u in urls),
            focus=focus, caption_style=caption_style, burn_captions=burn, languages=languages,
            confidence=0.95 if clipish or not text.replace(" ".join(urls), "").strip() else 0.75, **base,
        )

    # --- memory ---------------------------------------------------------------
    if re.search(r"\b(forget|delete (the |that |this )?(memory|preference)|remove (that|the) (memory|preference))\b", lower):
        return ParsedRequest("forget", **base)
    if re.search(r"^(please )?(remember|note|keep in mind)\b|\b(from now on|going forward|in (the )?future)\b|^(i prefer|i like|i want my|i don'?t like|always|never|my (brand|channel|audience|niche|style))\b", lower):
        return ParsedRequest("remember", **base)

    # --- changing or judging existing clips ("make clip 2 shorter", "better title for clip 1") --
    if wants_edit(text):
        return ParsedRequest("edit", clip_refs=clip_refs(text), caption_style=caption_style, burn_captions=burn, **base)

    # --- publishing / approvals -----------------------------------------------
    refs = [int(n) for n in re.findall(r"\b(?:clip|short|reel|#)\s*(\d{1,2})\b", lower)]
    if re.search(r"^(approve|approved|yes,? (publish|post)|go ahead|looks good,? publish|ship it)\b", lower):
        return ParsedRequest("approve", clip_refs=refs, **base)
    if publish and re.search(r"\b(clips?|shorts?|reels?|it|them|these|those|videos?|all|best|top|#\d)\b", lower):
        return ParsedRequest("publish", platforms=_platforms(lower), clip_refs=refs, **base)

    # --- information ------------------------------------------------------------
    if re.search(r"\b(status|progress|how('?s| is) it going|what are you (doing|working on)|done yet|still working|eta)\b", lower):
        return ParsedRequest("status", **base)
    if re.search(r"\b(cost|costs|spend|spent|budget|how much (did|have|has)|api usage|bill)\b", lower):
        return ParsedRequest("costs", **base)
    if re.search(r"\b(analytics|performance|perform(ed|ing)?|how did .* do|views|stats|insights|engagement|retention)\b", lower):
        return ParsedRequest("analytics", platforms=_platforms(lower), **base)
    if re.search(r"\b(what (have )?you learn(ed|t)|lessons|what works|strateg(y|ies)|experiments?|learning)\b", lower):
        return ParsedRequest("learning", **base)
    if re.search(r"\b(connect|link|add|set ?up)\b.*\b(youtube|instagram|openai|gemini|claude|anthropic|ollama|api key|google drive|provider|model)\b", lower):
        return ParsedRequest("connect", **base)
    action = re.search(r"\b(make|create|cut|generate|turn|produce|extract|find|give me|edit|repurpose)\b", lower)
    # "Give me a tip for writing a Shorts title" asks for advice, not for production.
    advice = re.search(r"\b(tips?|advice|ideas?|suggestions?|examples?|best practices?|explain|why|how (do|can|should|to|would))\b",
                       lower)
    if action and not advice and re.search(r"\b(clips?|shorts?|reels?|videos?|highlights?)\b", lower):
        return ParsedRequest("need_source", count=_count(text), platforms=_platforms(lower), **base)
    if re.search(r"^(hi|hello|hey|yo|hiya|good (morning|afternoon|evening))\b", lower) and len(lower) < 40:
        return ParsedRequest("greeting", **base)
    if re.search(r"\b(help|what can you do|how do(es)? (this|it) work|getting started)\b", lower):
        return ParsedRequest("help", **base)
    if text.endswith("?") or re.match(r"^(what|why|how|when|which|who|should|is|are|can|does|do)\b", lower) \
            or re.match(r"^(please )?(give me|tell me|explain|suggest|recommend|list|describe)\b", lower):
        return ParsedRequest("question", confidence=0.7, **base)
    return ParsedRequest("chat", confidence=0.5, **base)


_MEMORY_COMMAND = re.compile(r"^\s*(please\s+)?(remember|note(\s+down)?|keep in mind|don'?t forget)(\s+that)?\s*[:,-]?\s*",
                             re.IGNORECASE)


def memory_text(text: str) -> str:
    """The fact itself, without the command: "Remember that my audience…" -> "My audience…"."""
    cleaned = _MEMORY_COMMAND.sub("", text or "").strip()
    return (cleaned[:1].upper() + cleaned[1:]) if cleaned else (text or "").strip()


def extract_preferences(text: str) -> dict:
    """Structured settings implied by a preference statement (for a confirm-to-apply card)."""
    lower = URL_RE.sub(" ", text or "").lower()
    prefs: dict = {}
    low, high = _durations(text or "")
    if high:
        prefs["max_seconds"] = high
    if low:
        prefs["min_seconds"] = low
    count = _count(text or "")
    if count:
        prefs["default_count"] = count
    for style, pattern in (("kinetic", r"kinetic"), ("karaoke_fill", r"karaoke"), ("boxed", r"boxed"), ("clean_lower", r"clean|minimal|lower[- ]third"),
                           ("bold_pop", r"\bbold\b|\bpop\b|big captions")):
        if re.search(pattern, lower) and re.search(r"caption|subtitle", lower):
            prefs["caption_style"] = style
            break
    aspects = _aspects(lower)
    if aspects and re.search(r"\b(always|default|prefer|from now on)\b", lower):
        prefs["aspects"] = aspects
    return prefs
