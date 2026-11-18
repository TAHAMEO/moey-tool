"""Local text analysis for clip scoring and metadata (no model required)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

STOPWORDS = {"a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves", "just", "also", "really", "get", "got", "going", "gonna", "go", "thing", "things", "lot", "like", "okay", "ok", "yeah", "um", "uh"}
FILLERS = {"um", "uh", "erm", "hmm", "uhm", "like", "basically", "actually", "literally", "okay", "ok", "yeah", "so",
           "right", "anyway", "kinda", "sorta"}
HOOK_OPENERS = re.compile(
    r"^(here'?s|here is|this is (why|how|the)|the (biggest|best|worst|real|secret|truth|reason|mistake|problem)|"
    r"why|how|what if|stop|never|nobody|everyone|most people|you('re| are)? (probably|doing|making)|"
    r"if you|imagine|the one|number one|my (best|top|number)|i (was|made|lost|found|doubled|quit)|let me tell you)",
    re.IGNORECASE,
)
HOOK_WORDS = {"secret", "mistake", "biggest", "best", "worst", "never", "always", "nobody", "everyone", "truth", "why",
              "how", "stop", "instantly", "simple", "proven", "surprising", "shocking", "free", "doubled", "tripled",
              "lost", "won", "failed", "hack", "rule", "tip", "warning", "number", "one", "only", "first", "last"}
EMOTION = {
    "amazing": 0.9, "incredible": 0.9, "love": 0.8, "hate": 0.8, "terrible": 0.8, "awful": 0.8, "crazy": 0.8,
    "insane": 0.9, "shocking": 0.9, "huge": 0.6, "massive": 0.7, "worst": 0.8, "best": 0.6, "fear": 0.7, "afraid": 0.6,
    "angry": 0.7, "excited": 0.7, "happy": 0.5, "sad": 0.6, "painful": 0.7, "beautiful": 0.6, "brutal": 0.8,
    "devastating": 0.9, "unbelievable": 0.9, "wow": 0.8, "lost": 0.5, "won": 0.5, "failed": 0.6, "power": 0.5,
    "dream": 0.5, "struggle": 0.6, "proud": 0.6, "scared": 0.7, "risk": 0.5, "never": 0.4, "worth": 0.4,
}
INFO_MARKERS = re.compile(
    r"\b(because|the reason|which means|that means|how to|step|steps|first|second|third|tip|lesson|example|for example|"
    r"rule|strategy|framework|mistake|percent|%|times|data|study|research|results?|so that|instead)\b", re.IGNORECASE)
CONCLUSION_MARKERS = re.compile(r"\b(that'?s (why|the|how)|so the|the key|remember|bottom line|in short|that is the|which is why|the lesson)\b", re.IGNORECASE)
#: Sentence openers that point back to something said earlier.
DANGLING_START = {"it", "this", "that", "they", "he", "she", "these", "those", "which", "also", "then", "because",
                  "plus", "as", "or", "them", "there"}
#: Discourse markers: neutral openers ("So here is my tip") — skipped before judging the hook.
DISCOURSE = {"so", "and", "but", "now", "okay", "ok", "well", "right", "alright", "anyway", "look", "listen"}
#: True fillers at the start of a clip read as hesitation.
START_FILLERS = {"um", "uh", "erm", "hmm", "uhm", "like", "basically", "literally", "yeah", "kinda", "sorta", "dude"}
CTA_RE = re.compile(r"\b(subscribe|like and subscribe|hit the bell|link in (the )?(description|bio)|sponsor(ed)? by|"
                    r"use code|check out my|follow me|comment below|smash that)\b", re.IGNORECASE)
TRANSITION_RE = re.compile(r"\b(now let('?s| us) (talk|move|look)|moving on|next up|let('?s| us) move on|another thing|"
                           r"the next (thing|topic|point)|on to|let('?s| us) talk about)\b", re.IGNORECASE)
CALLBACK_RE = re.compile(r"\b(remember that|like i said|as i said|as i mentioned|that one|the same thing)\b", re.IGNORECASE)
NUMBER_RE = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|percent)\b", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass
class Sentence:
    first: int
    last: int
    start: float
    end: float
    text: str


def tokens(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def strip_discourse(text: str) -> str:
    """Drop leading discourse markers: "So, here is my tip" -> "here is my tip"."""
    words = text.strip().split()
    while words and re.sub(r"[^a-z']", "", words[0].lower()) in DISCOURSE:
        words = words[1:]
    return " ".join(words)


def content_words(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 2 and not t.isdigit()]


def sentences(words: list[dict]) -> list[Sentence]:
    out: list[Sentence] = []
    first = 0
    for i, word in enumerate(words):
        is_last = i == len(words) - 1
        if re.search(r"[.!?…]['\"]?$", word["text"]) or is_last:
            span = words[first:i + 1]
            out.append(Sentence(first, i, span[0]["start"], span[-1]["end"], " ".join(w["text"] for w in span)))
            first = i + 1
    return out


def idf_table(docs: list[str]) -> dict[str, float]:
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(content_words(doc)))
    n = max(1, len(docs))
    return {term: math.log((1 + n) / (1 + count)) + 1.0 for term, count in df.items()}


def keywords(text: str, top_k: int = 8) -> list[str]:
    """RAKE-style key phrases: split on stopwords/punctuation, score by degree/frequency."""
    phrases: list[list[str]] = []
    for chunk in re.split(r"[.,!?;:()\n\"]", text.lower()):
        current: list[str] = []
        for token in WORD_RE.findall(chunk):
            if token in STOPWORDS or token in FILLERS or len(token) < 3 or token.isdigit():
                if current:
                    phrases.append(current)
                current = []
            else:
                current.append(token)
        if current:
            phrases.append(current)
    freq: Counter[str] = Counter()
    degree: Counter[str] = Counter()
    for phrase in phrases:
        for word in phrase:
            freq[word] += 1
            degree[word] += len(phrase) - 1
    scores: dict[str, float] = {}
    for phrase in phrases:
        if len(phrase) > 4:
            continue
        key = " ".join(phrase)
        scores[key] = max(scores.get(key, 0.0), sum((degree[w] + freq[w]) / freq[w] for w in phrase))
    cta_words = {"subscribe", "follow", "comment", "notification", "bell", "sponsor", "description", "channel", "video"}
    scores = {k: v for k, v in scores.items() if not (set(k.split()) & cta_words)}
    ranked = sorted(scores.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    result: list[str] = []
    for phrase, _ in ranked:
        if not any(phrase in existing or existing in phrase for existing in result):
            result.append(phrase)
        if len(result) >= top_k:
            break
    return result


def title_case(text: str) -> str:
    small = {"a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "by", "of", "in", "vs", "with"}
    words = text.split()
    return " ".join(w if (i and w.lower() in small) else (w[:1].upper() + w[1:]) for i, w in enumerate(words))
