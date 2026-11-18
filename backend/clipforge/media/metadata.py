"""Titles, descriptions, tags, captions and hashtags per platform (Part 16).

Local generation is always available (key phrases + extractive summary).
When a model is connected and budget allows, it drafts platform-specific copy
from the clip transcript (fenced as untrusted data); the result is validated
against platform limits and falls back to the local version if invalid.
Copy must describe what the clip actually contains — no invented claims.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field

from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.hub import TaskSpec, hub
from ..security.untrusted import GUARD_INSTRUCTION, fence
from . import textutils as tu

log = logging.getLogger(__name__)

YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX = 5000
YT_TAGS_MAX_CHARS = 450
IG_CAPTION_MAX = 2200
IG_HASHTAGS_MAX = 30


@dataclass
class PlatformCopy:
    youtube_title: str
    youtube_description: str
    youtube_tags: list[str]
    instagram_caption: str
    instagram_hashtags: list[str]
    thumbnail_text: str
    method: str = "local_template"
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _hashtag(phrase: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", phrase)
    return "#" + "".join(w[:1].upper() + w[1:] for w in words)[:40] if words else ""


def one_line(text: str) -> str:
    """First meaningful line of model output, without markdown, labels or wrapping quotes."""
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(#+\s*|[-*]\s+|\*\*)?(youtube\s+)?(title|caption)\s*:\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line.replace("**", "")).strip().strip('"“”\'').strip()
        if line:
            return line
    return ""


def youtube_safe(text: str) -> str:
    """The YouTube API rejects titles and descriptions containing < or >."""
    return (text or "").replace("<", "‹").replace(">", "›")


def _clean_sentence(text: str) -> str:
    text = tu.strip_discourse(text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:1].upper() + text[1:] if text else text


def local_copy(clip_text: str, hook: str, *, channel: str = "", source_title: str | None = None,
               source_url: str | None = None, owned_source: bool = True, llm_title: str | None = None) -> PlatformCopy:
    keywords = tu.keywords(clip_text, top_k=8)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clip_text) if len(s.split()) >= 4]
    hook_clean = _clean_sentence(hook).rstrip(".")
    if llm_title:
        title = llm_title.strip().rstrip(".")
    elif 12 <= len(hook_clean) <= 70:
        title = hook_clean
    else:
        lead = tu.title_case(keywords[0]) if keywords else "Watch This"
        fragment = " ".join(hook_clean.split()[:8])
        title = f"{lead}: {fragment}" if fragment and fragment.lower() not in lead.lower() else lead
    title = youtube_safe(one_line(title))
    title = title[:YT_TITLE_MAX - 9].rstrip() + " #shorts" if "#shorts" not in title.lower() else title[:YT_TITLE_MAX]
    summary = " ".join(_clean_sentence(s) for s in sents[1:3]) or hook_clean
    tags = []
    for kw in keywords:
        if sum(len(t) + 1 for t in tags) + len(kw) > YT_TAGS_MAX_CHARS:
            break
        tags.append(kw)
    if channel:
        tags.append(channel)
    hashtags = [h for h in (_hashtag(k) for k in keywords[:5]) if len(h) > 2]
    credit = ""
    if not owned_source and source_url:
        credit = f"\n\nSource: {source_title or 'original video'} — {source_url}"
    yt_description = f"{hook_clean}.\n\n{summary}{credit}\n\n#shorts " + " ".join(hashtags[:3])
    ig_caption = f"{hook_clean}.\n\n{summary}" + (f"\n\nFollow {channel} for more." if channel else "\n\nFollow for more.")
    ig_tags = list(dict.fromkeys(hashtags + ["#reels"]))[:IG_HASHTAGS_MAX]
    thumb = " ".join(hook_clean.split()[:7])
    return PlatformCopy(
        youtube_title=title, youtube_description=youtube_safe(yt_description)[:YT_DESCRIPTION_MAX], youtube_tags=tags,
        instagram_caption=(ig_caption + "\n\n" + " ".join(ig_tags))[:IG_CAPTION_MAX], instagram_hashtags=ig_tags,
        thumbnail_text=thumb, keywords=keywords,
    )


class LLMCopy(BaseModel):
    youtube_title: str = Field(max_length=95)
    youtube_description: str = Field(max_length=1500)
    youtube_tags: list[str] = Field(default_factory=list, max_length=15)
    instagram_caption: str = Field(max_length=1800)
    instagram_hashtags: list[str] = Field(default_factory=list, max_length=12)
    thumbnail_text: str = Field(max_length=60)


def generate(clip_text: str, hook: str, *, channel: str = "", source_title: str | None = None,
             source_url: str | None = None, owned_source: bool = True, llm_title: str | None = None,
             preferences: list[str] | None = None, job_id=None, project_id=None, allow_llm: bool = True) -> PlatformCopy:
    base = local_copy(clip_text, hook, channel=channel, source_title=source_title, source_url=source_url,
                      owned_source=owned_source, llm_title=llm_title)
    if not allow_llm:
        return base
    prefs = "\n".join(f"- {p}" for p in (preferences or [])[:6]) or "- none recorded"
    messages = [
        ChatMessage("system", "You write platform-native copy for short vertical videos. YouTube Shorts: title under 70 "
                              "characters, description with 2-3 hashtags including #shorts, up to 10 tags. Instagram "
                              "Reels: an engaging caption (hook line first) and 3-8 relevant hashtags. Describe only what "
                              "the clip actually says; no false promises or invented facts. Owner preferences:\n" + prefs
                              + "\nReturn JSON only. " + GUARD_INSTRUCTION),
        ChatMessage("user", fence("clip_transcript", clip_text, 4000)),
    ]
    try:
        parsed, result = hub.complete_json(
            TaskSpec("metadata", quality=2, temperature=0.5, max_tokens=900, cache_ttl_s=30 * 24 * 3600,
                     job_id=job_id, project_id=project_id, max_providers=2), messages, LLMCopy)
    except NoProviderAvailable:
        return base
    title = youtube_safe(one_line(parsed.youtube_title)) or base.youtube_title
    if "#shorts" not in title.lower() and len(title) <= YT_TITLE_MAX - 8:
        title += " #shorts"
    hashtags = [h if h.startswith("#") else "#" + h for h in (re.sub(r"\s+", "", t) for t in parsed.instagram_hashtags) if len(h) > 1]
    credit = f"\n\nSource: {source_title or 'original video'} — {source_url}" if (not owned_source and source_url) else ""
    return PlatformCopy(
        youtube_title=title[:YT_TITLE_MAX],
        youtube_description=youtube_safe(parsed.youtube_description.strip() + credit)[:YT_DESCRIPTION_MAX],
        youtube_tags=[t[:60] for t in parsed.youtube_tags][:15] or base.youtube_tags,
        instagram_caption=(parsed.instagram_caption.strip() + "\n\n" + " ".join(hashtags))[:IG_CAPTION_MAX],
        instagram_hashtags=hashtags[:IG_HASHTAGS_MAX] or base.instagram_hashtags,
        thumbnail_text=one_line(parsed.thumbnail_text) or base.thumbnail_text,
        method=f"llm:{result.model}",
        keywords=base.keywords,
    )
