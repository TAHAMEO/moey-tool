"""Smart Source Access Resolver (Part 31.3).

For every submitted link this determines what can *genuinely and lawfully* be
accessed — metadata, a transcript, the media itself, or an authorised live
feed — and returns one structured state with the evidence behind it and the
simplest authorised alternatives. Access methods, in priority order:

1. official platform APIs                  (YouTube Data API, Instagram Graph API)
2. OAuth-connected owner access            (own YouTube captions, own Instagram media)
3. official exports / permitted downloads  (owner's master files)
4. authorised media storage integrations   (Google Drive, owner media library)
5. direct media URLs supplied by the owner (SSRF-hardened, rights attested)
6. authorised live ingest feeds            (owner pushes RTMP/SRT to CLIPFORGE)

Never used: third-party downloaders, scraping, DRM/paywall/login circumvention.
YouTube's API offers no general video download and Instagram's API only exposes
the connected account's own media — the resolver says so plainly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import LiveSession, SocialAccount, Source
from ..platforms import drive, google, instagram, youtube
from ..platforms.http import PlatformError
from ..security import ssrf

log = logging.getLogger(__name__)

FULL_MEDIA_ACCESS = "FULL_MEDIA_ACCESS"
TRANSCRIPT_ONLY = "TRANSCRIPT_ONLY"
METADATA_ONLY = "METADATA_ONLY"
AUTHORIZED_LIVE_FEED = "AUTHORIZED_LIVE_FEED"
REPLAY_ACCESSIBLE = "REPLAY_ACCESSIBLE"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
PLATFORM_APPROVAL_REQUIRED = "PLATFORM_APPROVAL_REQUIRED"
SOURCE_PRIVATE = "SOURCE_PRIVATE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
ACCESS_DENIED = "ACCESS_DENIED"

STATES = (FULL_MEDIA_ACCESS, TRANSCRIPT_ONLY, METADATA_ONLY, AUTHORIZED_LIVE_FEED, REPLAY_ACCESSIBLE, LOGIN_REQUIRED,
          PERMISSION_REQUIRED, PLATFORM_APPROVAL_REQUIRED, SOURCE_PRIVATE, SOURCE_UNAVAILABLE, UNSUPPORTED_SOURCE,
          ACCESS_DENIED)
PROCESSABLE = (FULL_MEDIA_ACCESS, REPLAY_ACCESSIBLE, AUTHORIZED_LIVE_FEED)
INSTAGRAM_CDN = re.compile(r"(^|\.)(cdninstagram\.com|fbcdn\.net)$")

NO_DOWNLOAD_NOTE = ("YouTube's official API does not provide video downloads, and CLIPFORGE does not use third-party "
                    "downloaders.")


@dataclass
class AccessResult:
    state: str
    platform: str
    source_type: str
    method: str | None = None
    capabilities: dict[str, bool] = field(default_factory=lambda: {"metadata": False, "transcript": False,
                                                                   "media": False, "live": False})
    metadata: dict[str, Any] = field(default_factory=dict)
    live_status: str | None = None
    owned: bool | None = None
    reasons: list[str] = field(default_factory=list)
    alternatives: list[dict[str, str]] = field(default_factory=list)
    acquire: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, str]] = field(default_factory=list)
    retryable: bool = False

    def check(self, name: str, result: str) -> None:
        self.checks.append({"check": name, "result": result})

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def analysis_level(self) -> str:
        if self.capabilities.get("media"):
            return "full_media"
        if self.capabilities.get("transcript"):
            return "transcript_only"
        if self.capabilities.get("metadata"):
            return "metadata_only"
        return "none"


ALT = {
    "connect_youtube": {"action": "connect_youtube", "label": "Connect your YouTube channel (if this is your video)", "href": "/settings#connections"},
    "connect_instagram": {"action": "connect_instagram", "label": "Connect your Instagram professional account (if this is your Reel)", "href": "/settings#connections"},
    "connect_drive": {"action": "connect_drive", "label": "Connect Google Drive so I can fetch your original files automatically", "href": "/settings#connections"},
    "upload": {"action": "upload", "label": "Upload the original file (you keep the rights)", "href": "/"},
    "direct_link": {"action": "direct_link", "label": "Paste a direct link to the original file you have rights to", "href": "/"},
    "drive_link": {"action": "drive_link", "label": "Share the original from Google Drive and paste its link", "href": "/"},
    "live_ingest": {"action": "live_ingest", "label": "Send your stream to CLIPFORGE from OBS/your encoder (live ingest)", "href": "/settings#live"},
    "wait_replay": {"action": "wait_replay", "label": "I can process the replay after the stream ends, once the recording file is available", "href": "/"},
    "permission": {"action": "permission", "label": "Ask the creator for permission and their original file", "href": "/"},
}


def _account(db: Session, platform: str) -> SocialAccount | None:
    return db.execute(select(SocialAccount).where(SocialAccount.platform == platform,
                                                  SocialAccount.status == "connected")).scalars().first()


# ------------------------------------------------------------------- YouTube
def _youtube_auth(db: Session, res: AccessResult) -> tuple[youtube.Auth | None, SocialAccount | None]:
    account = _account(db, "youtube")
    if account is not None:
        try:
            return youtube.Auth(access_token=google.access_token(db, account)), account
        except PlatformError as exc:
            res.check("youtube_oauth", f"connected account unusable: {exc}")
    key = get_settings().youtube_api_key
    if key is not None:
        return youtube.Auth(api_key=key.get_secret_value()), None
    return None, None


def _match_master(db: Session, title: str, duration_s: float, res: AccessResult) -> dict | None:
    """Find the owner's original file for one of their YouTube videos."""
    library = get_settings().library_dir / "masters"
    if library.exists():
        best = None
        for path in library.rglob("*"):
            if path.is_file() and path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
                score = SequenceMatcher(None, path.stem.lower(), title.lower()).ratio()
                if score > 0.6 and (best is None or score > best[0]):
                    best = (score, path)
        if best:
            res.check("media_library", f"matched local master {best[1].name} ({best[0]:.0%} title match)")
            return {"library_path": str(best[1])}
    account = _account(db, "google_drive")
    if account is None:
        res.check("google_drive", "not connected")
        return None
    try:
        token = google.access_token(db, account)
        for item in drive.search_videos(token, title):
            millis = float(((item.get("videoMediaMetadata") or {}).get("durationMillis")) or 0)
            similar = SequenceMatcher(None, item.get("name", "").lower(), title.lower()).ratio()
            if duration_s and millis and abs(millis / 1000 - duration_s) <= 2.0 and similar >= 0.45:
                res.check("google_drive", f"matched '{item['name']}' (duration within 2s)")
                return {"drive_file_id": item["id"], "drive_account_id": str(account.id)}
        res.check("google_drive", "no matching original file found")
    except PlatformError as exc:
        res.check("google_drive", f"search failed: {exc}")
    return None


def _resolve_youtube(db: Session, source: Source) -> AccessResult:
    res = AccessResult(state=METADATA_ONLY, platform="youtube", source_type=source.source_type)
    auth, account = _youtube_auth(db, res)
    item = None
    if auth is not None:
        try:
            item = youtube.video(auth, source.external_id)
            google.record_quota(db, "videos.list")
            res.check("youtube_data_api", "video found" if item else "no video returned")
        except PlatformError as exc:
            res.check("youtube_data_api", str(exc)[:200])
            res.retryable = exc.retryable
    if item is None and auth is None:
        res.check("youtube_data_api", "no API key or connected channel (metadata via oEmbed only)")
    if item is None:
        try:
            data = youtube.oembed(source.normalized_url)
            if data:
                res.metadata.update({"title": data.get("title"), "author": data.get("author_name"),
                                     "thumbnail_url": data.get("thumbnail_url")})
                res.capabilities["metadata"] = True
                res.check("youtube_oembed", "public metadata available")
            else:
                res.check("youtube_oembed", "not available (private, removed or embedding disabled)")
        except PlatformError as exc:
            res.check("youtube_oembed", f"unreachable: {exc}")
            res.retryable = True
    else:
        snippet = item.get("snippet", {})
        res.capabilities["metadata"] = True
        res.metadata.update({
            "title": snippet.get("title"), "author": snippet.get("channelTitle"), "channel_id": snippet.get("channelId"),
            "published_at": snippet.get("publishedAt"), "description": (snippet.get("description") or "")[:2000],
            "duration_s": youtube.parse_duration((item.get("contentDetails") or {}).get("duration", "")),
            "privacy": (item.get("status") or {}).get("privacyStatus"),
            "thumbnail_url": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url"),
            "has_captions": (item.get("contentDetails") or {}).get("caption") == "true",
        })
        res.live_status = youtube.live_status(item)
        if res.live_status != "none":
            res.metadata["live"] = item.get("liveStreamingDetails")

    if not res.capabilities["metadata"]:
        if res.retryable:
            res.state = SOURCE_UNAVAILABLE
            res.reasons.append("YouTube could not be reached to check this video. I'll retry automatically.")
        else:
            res.state = SOURCE_PRIVATE if auth is None else SOURCE_UNAVAILABLE
            res.reasons.append("This video is private, removed, or not visible to the connected account.")
            res.alternatives += [ALT["connect_youtube"], ALT["upload"]]
        return res

    if account is not None and res.metadata.get("channel_id"):
        try:
            channel = youtube.my_channel(youtube.Auth(access_token=google.access_token(db, account)))
            res.owned = bool(channel and channel.get("id") == res.metadata["channel_id"])
            res.check("ownership", "video belongs to the connected channel" if res.owned else "video belongs to another channel")
        except PlatformError as exc:
            res.check("ownership", f"could not verify: {exc}")

    # --- livestreams -----------------------------------------------------------
    if res.live_status in ("scheduled", "live"):
        session = db.execute(select(LiveSession).where(LiveSession.status.in_(("live", "reconnecting", "waiting")),
                                                       LiveSession.source_id == source.id)).scalars().first()
        if session is not None and session.status in ("live", "reconnecting"):
            res.state = AUTHORIZED_LIVE_FEED
            res.method = "live_ingest"
            res.capabilities.update(media=True, live=True)
            res.acquire = {"live_session_id": str(session.id)}
            res.reasons.append("Your encoder is sending this stream to CLIPFORGE, so I can process it live.")
            return res
        res.state = METADATA_ONLY
        res.reasons.append(
            "The stream is scheduled — waiting for the live source." if res.live_status == "scheduled"
            else "The stream is live on YouTube, but YouTube does not provide a live media feed to other apps.")
        res.reasons.append("I'll start live clipping as soon as an authorized feed (your encoder → CLIPFORGE) is connected.")
        res.alternatives += [ALT["live_ingest"], ALT["wait_replay"]]
        res.acquire = {"await_live": True}
        return res

    # --- owner's media (original file) -----------------------------------------
    if res.owned:
        master = _match_master(db, res.metadata.get("title") or "", res.metadata.get("duration_s") or 0.0, res)
        if master:
            res.state = REPLAY_ACCESSIBLE if res.live_status == "ended" else FULL_MEDIA_ACCESS
            res.method = "owner_media_library"
            res.capabilities["media"] = True
            res.acquire = master
            res.reasons.append("Found your original file in your connected media library.")
            return res
        try:
            tracks = youtube.captions_list(youtube.Auth(access_token=google.access_token(db, account)), source.external_id)
            google.record_quota(db, "captions.list")
            if tracks:
                preferred = sorted(tracks, key=lambda t: ((t["snippet"].get("trackKind") or "").lower() == "asr",
                                                          t["snippet"].get("language") != "en"))[0]
                res.state = TRANSCRIPT_ONLY
                res.method = "youtube_owner_captions"
                res.capabilities["transcript"] = True
                res.acquire = {"caption_id": preferred["id"], "language": preferred["snippet"].get("language")}
                res.reasons.append("This is your video, so I can use its caption track. Without the original file I can "
                                   "find the best moments from the transcript, but I can't analyse visuals or render clips.")
                res.alternatives += [ALT["connect_drive"], ALT["upload"]]
                return res
            res.check("owner_captions", "no caption tracks on this video")
        except PlatformError as exc:
            res.check("owner_captions", f"unavailable: {exc}")
        res.state = METADATA_ONLY
        res.reasons.append("This is your video, but I couldn't find its original file. " + NO_DOWNLOAD_NOTE)
        res.alternatives += [ALT["connect_drive"], ALT["upload"], ALT["direct_link"]]
        return res

    res.state = METADATA_ONLY
    res.reasons.append(NO_DOWNLOAD_NOTE + " I can read this video's public details, but not its media.")
    if account is None:
        res.alternatives.append(ALT["connect_youtube"])
    res.alternatives += [ALT["upload"], ALT["drive_link"], ALT["permission"]]
    return res


# ----------------------------------------------------------------- Instagram
def _resolve_instagram(db: Session, source: Source) -> AccessResult:
    res = AccessResult(state=LOGIN_REQUIRED, platform="instagram", source_type=source.source_type)
    account = _account(db, "instagram")
    if account is not None:
        try:
            token = instagram.access_token(db, account)
            media = instagram.find_own_media(token, account.external_account_id, source.external_id)
            if media is not None:
                res.owned = True
                res.capabilities["metadata"] = True
                res.metadata.update({"caption": (media.get("caption") or "")[:2000], "permalink": media.get("permalink"),
                                     "timestamp": media.get("timestamp"), "media_type": media.get("media_type"),
                                     "product_type": media.get("media_product_type"), "author": media.get("username"),
                                     "thumbnail_url": media.get("thumbnail_url")})
                if media.get("media_type") not in ("VIDEO", "REELS") and media.get("media_product_type") != "REELS":
                    res.state = UNSUPPORTED_SOURCE
                    res.reasons.append("This post is not a video.")
                    return res
                if not media.get("media_url"):
                    res.state = ACCESS_DENIED
                    res.reasons.append("Instagram did not provide the media file for this post (it may contain copyrighted audio).")
                    res.alternatives.append(ALT["upload"])
                    return res
                res.state = FULL_MEDIA_ACCESS
                res.method = "instagram_own_media"
                res.capabilities["media"] = True
                res.acquire = {"media_url": media["media_url"], "media_id": media.get("id")}
                res.reasons.append("This Reel belongs to your connected Instagram account, so I can access its video.")
                return res
            res.owned = False
            res.check("instagram_own_media", "not found among the connected account's media")
        except PlatformError as exc:
            res.check("instagram_own_media", f"lookup failed: {exc}")
            res.retryable = exc.retryable
    else:
        res.check("instagram_account", "not connected")
    try:
        meta = instagram.oembed(source.normalized_url)
    except PlatformError as exc:
        meta = None
        res.check("instagram_oembed", f"failed: {exc}")
    if meta:
        res.capabilities["metadata"] = True
        res.metadata.update({"author": meta.get("author_name"), "thumbnail_url": meta.get("thumbnail_url")})
        res.check("instagram_oembed", "public metadata available")
    elif get_settings().meta_client_token is None:
        res.check("instagram_oembed", "Meta app client token not configured (oEmbed Read feature needed)")
    if account is None:
        res.state = LOGIN_REQUIRED if not meta else METADATA_ONLY
        res.reasons.append("Instagram only lets apps access media from the creator's own connected professional account.")
        res.alternatives += [ALT["connect_instagram"], ALT["upload"]]
    else:
        res.state = METADATA_ONLY if meta else PERMISSION_REQUIRED
        res.reasons.append("This Reel isn't from your connected account, and Instagram's API doesn't allow access to other "
                           "accounts' media.")
        res.alternatives += [ALT["permission"], ALT["upload"]]
    return res


# ------------------------------------------------------------- Google Drive
def _resolve_drive(db: Session, source: Source) -> AccessResult:
    res = AccessResult(state=LOGIN_REQUIRED, platform="gdrive", source_type="file")
    account = _account(db, "google_drive")
    if account is None:
        res.reasons.append("Connect Google Drive (read-only) so I can open files you share with me.")
        res.alternatives += [ALT["connect_drive"], ALT["upload"]]
        return res
    try:
        info = drive.file_info(google.access_token(db, account), source.external_id)
    except PlatformError as exc:
        if exc.status == 404:
            res.state = SOURCE_UNAVAILABLE
            res.reasons.append("The connected Google account can't see this file (wrong account or not shared).")
        elif exc.status == 403:
            res.state = ACCESS_DENIED
            res.reasons.append("Google Drive denied access to this file.")
        else:
            res.state = SOURCE_UNAVAILABLE
            res.retryable = exc.retryable
            res.reasons.append(f"Google Drive could not be reached: {exc}")
        return res
    res.capabilities["metadata"] = True
    res.metadata.update({"title": info.get("name"), "size": int(info.get("size") or 0), "mime": info.get("mimeType"),
                         "duration_s": float(((info.get("videoMediaMetadata") or {}).get("durationMillis")) or 0) / 1000})
    if not (info.get("mimeType", "").startswith(("video/", "audio/"))):
        res.state = UNSUPPORTED_SOURCE
        res.reasons.append("This Drive file isn't a video or audio file.")
        return res
    if info.get("capabilities", {}).get("canDownload") is False:
        res.state = ACCESS_DENIED
        res.reasons.append("The file's owner has disabled downloading.")
        return res
    res.state = FULL_MEDIA_ACCESS
    res.method = "google_drive"
    res.capabilities["media"] = True
    res.acquire = {"drive_file_id": source.external_id, "drive_account_id": str(account.id)}
    res.reasons.append("Your connected Google Drive gives me access to this file.")
    return res


# --------------------------------------------------------------- direct URLs
def _resolve_direct(db: Session, source: Source) -> AccessResult:
    res = AccessResult(state=UNSUPPORTED_SOURCE, platform="direct", source_type=source.source_type)
    url = source.normalized_url
    if source.source_type == "stream":
        res.state = PERMISSION_REQUIRED
        res.reasons.append("Live HLS pulls are only enabled for stream sources you register in Settings → Live.")
        res.alternatives.append(ALT["live_ingest"])
        return res
    try:
        head = ssrf.safe_head(url)
    except ssrf.UnsafeURLError as exc:
        res.state = ACCESS_DENIED
        res.reasons.append(f"Link blocked for safety: {exc}")
        return res
    except Exception as exc:  # network errors are temporary
        res.state = SOURCE_UNAVAILABLE
        res.retryable = True
        res.reasons.append(f"The link could not be reached ({exc.__class__.__name__}).")
        return res
    status = head["status"]
    if status in (301, 302, 303, 307, 308) and head.get("location"):
        res.check("redirect", "link redirects; the download will follow and re-check every hop")
        status = 200
        head["content_type"] = head["content_type"] or "application/octet-stream"
    if status in (401, 403):
        res.state = ACCESS_DENIED
        res.reasons.append("The server requires a login or refused access. I don't bypass logins or paywalls.")
        res.alternatives += [ALT["upload"], ALT["direct_link"]]
        return res
    if status in (404, 410):
        res.state = SOURCE_UNAVAILABLE
        res.reasons.append("Nothing was found at this link.")
        return res
    if status >= 400:
        res.state = SOURCE_UNAVAILABLE
        res.retryable = status >= 500
        res.reasons.append(f"The server answered with an error ({status}).")
        return res
    content_type = head.get("content_type") or ""
    if content_type and not content_type.startswith(ssrf.MEDIA_CONTENT_TYPES):
        res.state = UNSUPPORTED_SOURCE
        res.reasons.append("This link opens a web page, not a media file. Paste a direct link to the video file.")
        res.alternatives += [ALT["upload"]]
        return res
    res.state = FULL_MEDIA_ACCESS
    res.method = "direct_url"
    res.capabilities.update(metadata=True, media=True)
    res.metadata.update({"content_type": content_type, "size": int(head["content_length"]) if (head.get("content_length") or "").isdigit() else None,
                         "host": head["host"]})
    res.acquire = {"url": url}
    res.reasons.append("Direct media link you provided (you confirm you have the rights to use it).")
    return res


def resolve(db: Session, source: Source) -> AccessResult:
    if source.platform == "upload":
        res = AccessResult(state=FULL_MEDIA_ACCESS, platform="upload", source_type="file", method="upload",
                           capabilities={"metadata": True, "transcript": False, "media": True, "live": False})
        res.reasons.append("You uploaded this file.")
        res.acquire = {"asset_id": str(source.media_asset_id)}
        return res
    resolver = {"youtube": _resolve_youtube, "instagram": _resolve_instagram, "gdrive": _resolve_drive,
                "direct": _resolve_direct}.get(source.platform)
    if resolver is None:
        return AccessResult(state=UNSUPPORTED_SOURCE, platform=source.platform, source_type=source.source_type,
                            reasons=["This kind of link isn't supported."])
    return resolver(db, source)
