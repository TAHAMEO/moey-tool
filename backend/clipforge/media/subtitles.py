"""Subtitle generation and validation (Part 13).

Separate SRT/VTT files are always produced, even when captions are burned in:
YouTube receives a real caption track; Instagram Reels get burned-in captions.

Validation checks timing (monotonic, non-overlapping, minimum/maximum display
time), readability (characters per second, line length, line count), language
consistency, spelling, and flags words that need a human look — low-confidence
recognitions and sensitive words that ASR may have produced in error.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

MAX_LINE_CHARS = 42
MAX_LINES = 2
MAX_CPS = 20.0
MIN_DURATION = 0.7
MAX_DURATION = 7.0
GAP = 0.04
LOW_CONFIDENCE = 0.6

# Words that are rarely intended in business/creator content but are common
# ASR confusions — always surfaced for review, never silently published.
SENSITIVE = {"rape", "rapes", "raped", "rapist", "kill", "killed", "suicide", "nazi", "terrorist", "porn", "sex", "shit",
             "fuck", "fucking", "bitch", "damn", "hell", "drugs", "cocaine", "slave", "retard", "retarded", "whore", "bomb"}


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[dict] = field(default_factory=list)


@dataclass
class SubtitleReport:
    cues: int
    issues: list[dict]
    review_words: list[dict]
    language: str | None
    language_consistent: bool
    misspelled: list[str]
    max_cps: float
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _wrap(text: str, width: int = MAX_LINE_CHARS) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    if len(lines) == 2:  # balance two-line captions for readability
        joined = " ".join(lines)
        best = min(range(1, len(joined.split())), key=lambda k: abs(len(" ".join(joined.split()[:k])) - len(joined) / 2),
                   default=None)
        if best:
            parts = joined.split()
            a, b = " ".join(parts[:best]), " ".join(parts[best:])
            if len(a) <= width and len(b) <= width:
                lines = [a, b]
    return lines


def build_cues(words: list[dict], *, max_chars: int = MAX_LINE_CHARS * MAX_LINES, max_duration: float = MAX_DURATION) -> list[Cue]:
    """Group words into readable cues, breaking at sentence ends and pauses."""
    cues: list[Cue] = []
    current: list[dict] = []

    def flush():
        if current:
            cues.append(Cue(current[0]["start"], current[-1]["end"], " ".join(w["text"] for w in current), list(current)))
            current.clear()

    for i, word in enumerate(words):
        if current:
            text_len = len(" ".join(w["text"] for w in current)) + 1 + len(word["text"])
            gap = word["start"] - current[-1]["end"]
            if text_len > max_chars or word["end"] - current[0]["start"] > max_duration or gap > 0.8:
                flush()
        current.append(word)
        if re.search(r"[.!?…]$", word["text"]) and len(" ".join(w["text"] for w in current)) > 18:
            flush()
    flush()
    # Enforce minimum display time and non-overlap without changing order.
    for i, cue in enumerate(cues):
        if cue.end - cue.start < MIN_DURATION:
            limit = cues[i + 1].start - GAP if i + 1 < len(cues) else cue.start + MIN_DURATION
            cue.end = max(cue.end, min(cue.start + MIN_DURATION, limit))
        if i + 1 < len(cues) and cue.end > cues[i + 1].start - GAP:
            cue.end = max(cue.start + 0.2, cues[i + 1].start - GAP)
    return cues


def _ts(seconds: float, sep: str) -> str:
    ms = round(max(0.0, seconds) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for n, cue in enumerate(cues, 1):
        blocks.append(f"{n}\n{_ts(cue.start, ',')} --> {_ts(cue.end, ',')}\n" + "\n".join(_wrap(cue.text)))
    return "\n\n".join(blocks) + "\n"


def to_vtt(cues: list[Cue]) -> str:
    blocks = ["WEBVTT", ""]
    for cue in cues:
        blocks.append(f"{_ts(cue.start, '.')} --> {_ts(cue.end, '.')} line:85%\n" + "\n".join(_wrap(cue.text)))
        blocks.append("")
    return "\n".join(blocks)


_LANG_STOPWORDS = {
    "en": {"the", "and", "is", "to", "of", "you", "that", "it", "in", "for", "this", "with"},
    "es": {"el", "la", "de", "que", "y", "en", "los", "es", "por", "las", "un", "para"},
    "fr": {"le", "la", "de", "et", "les", "des", "est", "que", "en", "un", "une", "pour"},
    "de": {"der", "die", "und", "das", "ist", "nicht", "ich", "zu", "den", "mit", "ein", "es"},
    "pt": {"o", "a", "de", "que", "e", "do", "da", "em", "um", "para", "não", "os"},
}


def detect_language(text: str) -> str | None:
    tokens = re.findall(r"[a-zà-ÿ']+", text.lower())
    if len(tokens) < 8:
        return None
    scores = {lang: sum(1 for t in tokens if t in words) / len(tokens) for lang, words in _LANG_STOPWORDS.items()}
    lang, score = max(scores.items(), key=lambda kv: kv[1])
    return lang if score >= 0.05 else None


@lru_cache(maxsize=4)
def _spell(language: str):
    try:
        from spellchecker import SpellChecker

        return SpellChecker(language=language)
    except (ImportError, ValueError, OSError):  # unsupported language or missing dictionary
        return None


def validate(cues: list[Cue], *, expected_language: str = "en", duration: float | None = None) -> SubtitleReport:
    issues: list[dict] = []
    review: list[dict] = []
    max_cps = 0.0
    previous_end = -1.0
    all_text = " ".join(c.text for c in cues)
    for n, cue in enumerate(cues, 1):
        length = cue.end - cue.start
        cps = len(cue.text) / max(0.01, length)
        max_cps = max(max_cps, cps)
        if cue.start < previous_end - 1e-3:
            issues.append({"cue": n, "type": "overlap", "detail": f"starts {previous_end - cue.start:.2f}s before previous cue ends"})
        if cue.end <= cue.start:
            issues.append({"cue": n, "type": "timing", "detail": "end is not after start"})
        if length > MAX_DURATION + 0.01:
            issues.append({"cue": n, "type": "too_long", "detail": f"shown for {length:.1f}s"})
        if cps > MAX_CPS * 1.25:
            issues.append({"cue": n, "type": "reading_speed", "detail": f"{cps:.0f} characters/second"})
        lines = _wrap(cue.text)
        if len(lines) > MAX_LINES:
            issues.append({"cue": n, "type": "line_count", "detail": f"{len(lines)} lines"})
        if duration is not None and cue.end > duration + 0.5:
            issues.append({"cue": n, "type": "beyond_media", "detail": "cue extends past the end of the video"})
        for word in cue.words:
            token = re.sub(r"[^a-z']", "", word["text"].lower())
            if token in SENSITIVE:
                review.append({"cue": n, "word": word["text"], "reason": "sensitive word — confirm it was actually said",
                               "time": round(word["start"], 2)})
            elif word.get("conf", 1.0) < LOW_CONFIDENCE:
                review.append({"cue": n, "word": word["text"], "reason": f"low recognition confidence ({word['conf']:.2f})",
                               "time": round(word["start"], 2)})
        previous_end = cue.end
    language = detect_language(all_text)
    consistent = language in (None, expected_language)
    if not consistent:
        issues.append({"cue": None, "type": "language", "detail": f"text looks like '{language}', expected '{expected_language}'"})
    misspelled: list[str] = []
    checker = _spell(expected_language)
    if checker is not None:
        tokens = [t for t in re.findall(r"[A-Za-z']+", all_text) if len(t) > 2 and not t[0].isupper()]
        misspelled = sorted(checker.unknown([t.lower() for t in tokens]))[:30]
    blocking = [i for i in issues if i["type"] in ("overlap", "timing", "beyond_media", "language")]
    return SubtitleReport(cues=len(cues), issues=issues, review_words=review, language=language,
                          language_consistent=consistent, misspelled=misspelled, max_cps=round(max_cps, 1),
                          passed=not blocking)


def write_files(cues: list[Cue], directory: Path, stem: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    srt, vtt = directory / f"{stem}.srt", directory / f"{stem}.vtt"
    srt.write_text(to_srt(cues), encoding="utf-8")
    vtt.write_text(to_vtt(cues), encoding="utf-8")
    return srt, vtt


def parse_srt(text: str) -> list[dict]:
    """Platform caption tracks (SRT) -> pseudo word list (one entry per cue word)."""
    words: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        timing = next((l for l in lines if "-->" in l), None)
        if not timing:
            continue
        start_s, end_s = [_parse_ts(part.strip().split(" ")[0]) for part in timing.split("-->")]
        body = " ".join(l for l in lines[lines.index(timing) + 1:]).strip()
        tokens = re.sub(r"<[^>]+>", "", body).split()
        if not tokens:
            continue
        step = (end_s - start_s) / len(tokens)
        for i, token in enumerate(tokens):
            words.append({"text": token, "start": round(start_s + i * step, 3), "end": round(start_s + (i + 1) * step, 3),
                          "conf": 1.0, "approximate": True})
    return words


def _parse_ts(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)
