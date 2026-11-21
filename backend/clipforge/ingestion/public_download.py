"""Download publicly viewable YouTube and Instagram videos with yt-dlp.

Used when the owner has it switched on (Settings → Sources; on by default) and
only for videos anyone can watch without signing in. The owner is responsible
for having the rights to reuse what they download — CLIPFORGE says so on every
project that uses it.

Hard limits, whatever the platform does:

* yt-dlp never receives cookies, passwords, netrc entries or browser sessions,
  so private, members-only, age-restricted, paid and "sign in to confirm"
  videos are reported as such — never worked around;
* no faked location (``geo_bypass`` off), no TLS/browser impersonation,
  no proof-of-origin token generators, no DRM handling;
* only the YouTube and Instagram extractors are loaded, so a link can never
  make yt-dlp fetch anything else;
* size and duration limits apply before and during the download; live and
  upcoming streams are refused (their replays are fine once processed).

Runs in the media worker, which holds no secrets: yt-dlp parses untrusted pages
and, for YouTube, runs the player's challenge script in Deno, which yt-dlp
starts without file, network or environment permissions.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import resolver as r

log = logging.getLogger(__name__)

#: Extractor names yt-dlp may load (full, case-insensitive match). Tests narrow
#: this to the generic extractor to exercise the pipeline against a local server.
ALLOWED_EXTRACTORS = ["youtube", "instagram"]

RIGHTS_NOTE = "Use it only if you own the video or have permission to reuse it."

# Message patterns (yt-dlp/platform wording) → honest access state. First match wins.
_ERRORS: list[tuple[str, str, str, bool]] = [
    (r"private video|video is private|this account is private", r.SOURCE_PRIVATE,
     "This video is private. CLIPFORGE only downloads videos anyone can watch.", False),
    (r"members[- ]only|join this channel|channel'?s members|available to this channel", r.PERMISSION_REQUIRED,
     "This video is for channel members only. CLIPFORGE doesn't sign in to get around that.", False),
    (r"confirm your age|age[- ]restricted|inappropriate for some users|age-gate", r.LOGIN_REQUIRED,
     "YouTube only shows this video to signed-in adults. CLIPFORGE doesn't sign in to YouTube to get around that.", False),
    (r"not a bot|confirm you.?re not|unusual traffic|captcha", r.LOGIN_REQUIRED,
     ("YouTube asked for a sign-in to confirm this isn't a bot. CLIPFORGE doesn't sign in or work around that check; "
      "it usually doesn't happen on a home internet connection. You can upload the file instead."), False),
    (r"requires payment|purchase|rent this|paid content|premium", r.PERMISSION_REQUIRED,
     "This video has to be bought, rented or watched with a paid membership.", False),
    (r"\bdrm\b|widevine|playready|fairplay", r.ACCESS_DENIED, "This video is DRM-protected.", False),
    (r"not available in your country|geo.?restrict|blocked (it )?in your country|not made this video available",
     r.ACCESS_DENIED, "The uploader made this video unavailable in this server's country.", False),
    (r"login required|log in|logged-in|login_required|rate-limit reached or login|checkpoint required|sessionid",
     r.LOGIN_REQUIRED, ("Instagram only shows this post to signed-in users. CLIPFORGE doesn't sign in to see other "
                        "accounts' posts — connect your own professional account if it's your Reel, or upload the file."),
     False),
    (r"this live event will begin|premieres in|is_upcoming|scheduled to start", r.METADATA_ONLY,
     "The stream or premiere hasn't started yet.", False),
    (r"429|too many requests|rate.?limit", r.SOURCE_UNAVAILABLE,
     "The platform is limiting downloads right now; I'll try again later.", True),
    ((r"video unavailable|has been removed|no longer available|terminated|does not exist|not found|404|"
      r"unable to extract|no video formats|there is no video|unsupported url"), r.SOURCE_UNAVAILABLE,
     "The video couldn't be found — it may have been removed, or the link is wrong.", False),
    (r"timed? ?out|temporar|connection|network is unreachable|ssl|eof occurred|reset by peer|503|502|500", r.SOURCE_UNAVAILABLE,
     "The platform couldn't be reached; I'll try again later.", True),
]


class DownloadError(RuntimeError):
    def __init__(self, message: str, *, state: str, retryable: bool = False, detail: str = ""):
        super().__init__(message)
        self.state = state
        self.retryable = retryable
        self.detail = detail


def explain(raw: str) -> DownloadError:
    """Map a yt-dlp error message to an honest access state and plain wording."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", raw or "")
    lowered = text.lower()
    for pattern, state, message, retryable in _ERRORS:
        if re.search(pattern, lowered):
            return DownloadError(message, state=state, retryable=retryable, detail=text[:500])
    return DownloadError("The video couldn't be downloaded.", state=r.SOURCE_UNAVAILABLE, retryable=False, detail=text[:500])


@dataclass
class Downloaded:
    path: Path
    info: dict[str, Any] = field(default_factory=dict)


def deno_path() -> str | None:
    try:
        import deno

        return deno.find_deno_bin()
    except (ImportError, FileNotFoundError, OSError):  # fall back to one on PATH
        return shutil.which("deno")


def available() -> dict[str, Any]:
    """What the downloader can do here (for diagnostics)."""
    try:
        import yt_dlp

        version = yt_dlp.version.__version__
    except ImportError:
        return {"ok": False, "reason": "yt-dlp is not installed"}
    try:
        import yt_dlp_ejs  # noqa: F401

        solver = True
    except ImportError:
        solver = False
    runtime = deno_path()
    return {"ok": True, "yt_dlp": version, "youtube_challenge_solver": solver and bool(runtime),
            "js_runtime": "deno" if runtime else None, "ffmpeg": bool(shutil.which("ffmpeg"))}


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            self.messages.append(msg)
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        self.messages.append(msg)
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)
        log.info("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        log.info("yt-dlp error: %s", msg)


KEEP = ("id", "title", "uploader", "uploader_id", "channel", "channel_id", "duration", "upload_date", "license",
        "availability", "webpage_url", "extractor_key", "width", "height", "fps", "vcodec", "acodec", "live_status",
        "was_live", "age_limit", "description")


def refusal(info: dict, max_seconds: float) -> DownloadError | None:
    """Why a video must not be downloaded, judged from its metadata before any media is fetched."""
    live = info.get("live_status")
    if live == "is_live":
        return DownloadError("The stream is live right now — I can process its replay after it ends, or connect your "
                             "encoder for live clipping.", state=r.METADATA_ONLY)
    if live == "is_upcoming":
        return DownloadError("The stream or premiere hasn't started yet.", state=r.METADATA_ONLY)
    if live == "post_live":
        return DownloadError("The stream just ended and the platform is still processing its replay. I'll try again later.",
                             state=r.SOURCE_UNAVAILABLE, retryable=True)
    availability = info.get("availability")
    if availability == "premium_only":
        return DownloadError("This video needs a paid membership.", state=r.PERMISSION_REQUIRED)
    if availability == "subscriber_only":
        return DownloadError("This video is for channel members only.", state=r.PERMISSION_REQUIRED)
    if availability in ("needs_auth", "private"):
        return DownloadError("This video needs a signed-in account, which CLIPFORGE doesn't use.",
                             state=r.SOURCE_PRIVATE if availability == "private" else r.LOGIN_REQUIRED)
    if info.get("duration") and float(info["duration"]) > max_seconds:
        return DownloadError(f"The video is {float(info['duration']) / 3600:.1f} hours long; the limit is "
                             f"{max_seconds / 3600:.1f} hours.", state=r.ACCESS_DENIED)
    return None


def download(url: str, out_dir: Path, *, max_bytes: int, max_seconds: float, cache_dir: Path | None = None,
             on_progress: Callable[[float, str], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> Downloaded:
    """Download one public video to ``out_dir`` (mp4 when possible). Raises :class:`DownloadError`."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    rejected: list[DownloadError] = []
    logger = _Log()

    def gate(info: dict, *, incomplete: bool) -> str | None:
        problem = refusal(info, max_seconds)
        if problem is None:
            return None
        if not incomplete:
            rejected.append(problem)
        return str(problem)

    def hook(status: dict) -> None:
        if cancelled and cancelled():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")
        if status.get("status") == "downloading" and on_progress:
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            if total and total > max_bytes * 1.05:
                raise yt_dlp.utils.DownloadError(f"File is larger than the {max_bytes // (1024 * 1024)} MB limit")
            on_progress(min(1.0, done / total) if total else 0.0, "Downloading")

    runtime = deno_path()
    options: dict[str, Any] = {
        # best video up to 1080p on its short side + best audio, preferring MP4/M4A (no re-encode to merge)
        "format": "bv*+ba/b",
        "format_sort": ["res:1080", "ext:mp4:m4a"],
        "merge_output_format": "mp4",
        "outtmpl": {"default": "download.%(ext)s"},
        "paths": {"home": str(out_dir), "temp": str(out_dir)},
        "noplaylist": True,
        "allowed_extractors": list(ALLOWED_EXTRACTORS),
        # never sign in, never pretend to be elsewhere, never fetch helper code from the internet
        "cookiefile": None,
        "usenetrc": False,
        "geo_bypass": False,
        "remote_components": [],
        "js_runtimes": {"deno": {"path": runtime}} if runtime else {"deno": {}},
        "max_filesize": max_bytes,
        "match_filter": gate,
        "progress_hooks": [hook],
        "logger": logger,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 5,
        "extractor_retries": 2,
        "concurrent_fragment_downloads": 4,
        "writethumbnail": False,
        "writesubtitles": False,
        "overwrites": True,
        "cachedir": str(cache_dir) if cache_dir else False,
        "ffmpeg_location": shutil.which("ffmpeg"),
        "warn_when_outdated": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadCancelled as exc:
        raise DownloadError("Download cancelled.", state=r.SOURCE_UNAVAILABLE, retryable=False) from exc
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        message = str(exc)
        if "larger than" in message.lower() and "limit" in message.lower():
            raise DownloadError(f"The video file is larger than the {max_bytes // (1024 * 1024)} MB download limit.",
                                state=r.ACCESS_DENIED) from exc
        raise explain(message) from exc
    if rejected:
        raise rejected[0]
    if info is None:
        raise explain(" ".join(logger.warnings) or "no video returned")
    files = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
    path = next((f for f in files if f.exists()), None)
    if path is None:
        candidates = sorted(out_dir.glob("download.*"), key=lambda p: p.stat().st_size, reverse=True)
        path = next((c for c in candidates if not c.name.endswith((".part", ".ytdl", ".json"))), None)
    if path is None:
        said = " ".join(logger.warnings + logger.messages).lower()
        if "max-filesize" in said or "larger than" in said:
            raise DownloadError(f"The video file is larger than the {max_bytes // (1024 * 1024)} MB download limit.",
                                state=r.ACCESS_DENIED)
        raise explain(" ".join(logger.warnings) or "the download produced no file")
    if path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        raise DownloadError(f"The video file is larger than the {max_bytes // (1024 * 1024)} MB download limit.",
                            state=r.ACCESS_DENIED)
    kept = {k: info.get(k) for k in KEEP if info.get(k) is not None}
    if kept.get("description"):
        kept["description"] = str(kept["description"])[:2000]
    return Downloaded(path=path, info=kept)
