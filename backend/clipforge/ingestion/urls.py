"""Platform detection and URL normalisation (no network access).

Normalised URLs and stable external ids make duplicate submissions of the same
source — in any of its URL forms — map to one ``Source`` row, so transcripts and
analyses are reused instead of recomputed (acceptance test 37).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
IG_CODE = re.compile(r"^[A-Za-z0-9_-]{5,40}$")
DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
MEDIA_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".ts")
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
            "www.youtube-nocookie.com"}
IG_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}


@dataclass
class DetectedSource:
    original_url: str
    platform: str  # youtube | instagram | gdrive | direct | unknown
    source_type: str  # video | short | livestream | reel | post | tv | file | stream | unsupported
    external_id: str | None
    normalized_url: str | None
    valid: bool
    error: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _invalid(url: str, platform: str, error: str, hint: str | None = None) -> DetectedSource:
    return DetectedSource(url, platform, "unsupported", None, None, False, error, hint)


def _youtube(url: str, host: str, path: str, query: dict[str, list[str]]) -> DetectedSource:
    parts = [p for p in path.split("/") if p]
    video_id, kind = None, "video"
    if host == "youtu.be" and parts:
        video_id = parts[0]
    elif parts[:1] == ["watch"]:
        video_id = (query.get("v") or [None])[0]
    elif parts and parts[0] in ("shorts", "live", "embed", "v", "e") and len(parts) > 1:
        video_id = parts[1]
        kind = {"shorts": "short", "live": "livestream"}.get(parts[0], "video")
    elif parts and (parts[0].startswith("@") or parts[0] in ("channel", "c", "user", "playlist", "results", "feed")):
        return _invalid(url, "youtube", "This is a channel, playlist or page link, not a single video.",
                        "Paste the link of the specific video or livestream you want me to work on.")
    if not video_id or not YT_ID.match(video_id):
        return _invalid(url, "youtube", "This YouTube link does not contain a valid video id.",
                        "Copy the link from the video's Share button and paste it here.")
    return DetectedSource(url, "youtube", kind, video_id, f"https://www.youtube.com/watch?v={video_id}", True)


def _instagram(url: str, path: str) -> DetectedSource:
    parts = [p for p in path.split("/") if p]
    if parts and parts[0] == "stories":
        return _invalid(url, "instagram", "Instagram Stories are temporary and not available through the official API.",
                        "Share the Reel or video post link instead.")
    # Optional leading username: /{user}/reel/{code}/
    if len(parts) >= 3 and parts[1] in ("reel", "reels", "p", "tv"):
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] in ("reel", "reels", "p", "tv") and IG_CODE.match(parts[1]):
        kind = {"reel": "reel", "reels": "reel", "p": "post", "tv": "tv"}[parts[0]]
        segment = "reel" if kind == "reel" else ("tv" if kind == "tv" else "p")
        return DetectedSource(url, "instagram", kind, parts[1], f"https://www.instagram.com/{segment}/{parts[1]}/", True)
    return _invalid(url, "instagram", "This Instagram link is not a Reel or video post link.",
                    "Open the Reel or post, tap Share → Copy link, and paste it here.")


def _drive(url: str, host: str, path: str, query: dict[str, list[str]]) -> DetectedSource:
    file_id = None
    match = re.search(r"/(?:file/d|uc|open)/([A-Za-z0-9_-]+)", path)
    if match and match.group(1) not in ("uc", "open"):
        file_id = match.group(1)
    file_id = file_id or (query.get("id") or [None])[0]
    if not file_id or not DRIVE_ID.match(file_id):
        return _invalid(url, "gdrive", "This Google Drive link does not identify a single file.")
    return DetectedSource(url, "gdrive", "file", file_id, f"https://drive.google.com/file/d/{file_id}/view", True)


def detect(url: str) -> DetectedSource:
    raw = (url or "").strip()
    if not raw:
        return _invalid(raw, "unknown", "No link provided.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return _invalid(raw, "unknown", "That link could not be read.")
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme in ("rtmp", "rtmps", "srt"):
        return _invalid(raw, "live_ingest", "Stream ingest addresses are configured in Connections → Live, not pasted as sources.")
    if scheme not in ("http", "https") or not host:
        return _invalid(raw, "unknown", "Only web links (http/https) are supported.")
    query = parse_qs(parts.query)
    if host in YT_HOSTS or host == "youtu.be":
        return _youtube(raw, host, parts.path, query)
    if host in IG_HOSTS or host == "instagr.am":
        return _instagram(raw, parts.path)
    if host in ("drive.google.com", "docs.google.com"):
        return _drive(raw, host, parts.path, query)
    if host.endswith(("tiktok.com", "facebook.com", "fb.watch", "twitter.com", "x.com", "vimeo.com", "twitch.tv")):
        return _invalid(raw, "unknown", f"{host} links are not supported yet.",
                        "Supported: YouTube videos and livestreams, Instagram Reels and video posts, Google Drive files, and direct media file links you have rights to.")
    path_lower = parts.path.lower()
    normalized = urlunsplit((scheme, parts.netloc.lower(), parts.path, parts.query, ""))
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    if path_lower.endswith(".m3u8"):
        return DetectedSource(raw, "direct", "stream", digest, normalized, True)
    if path_lower.endswith(MEDIA_EXT):
        return DetectedSource(raw, "direct", "file", digest, normalized, True)
    return DetectedSource(raw, "direct", "unknown", digest, normalized, True,
                          hint="I'll check whether this link points to a media file you can share with me.")
