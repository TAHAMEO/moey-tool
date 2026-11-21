"""YouTube Data API v3 + YouTube Analytics API v2 (official REST endpoints).

Implements the documented resumable upload protocol: a session URI is created
once and persisted, chunks are multiples of 256 KiB, ``308 Resume Incomplete``
reports progress, and after any interruption the session is *queried* before
sending more bytes — so a crash never causes a duplicate upload.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_settings
from .http import PlatformError, google_error, request

log = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB


def _api() -> str:
    return get_settings().google_api_base.rstrip("/") + "/youtube/v3"


def _upload_api() -> str:
    return get_settings().google_api_base.rstrip("/") + "/upload/youtube/v3"


@dataclass
class Auth:
    access_token: str | None = None
    api_key: str | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}

    def params(self) -> dict[str, str]:
        return {} if self.access_token else ({"key": self.api_key} if self.api_key else {})


def _get(auth: Auth, path: str, params: dict[str, Any]) -> dict:
    response = request("youtube", "GET", f"{_api()}/{path}", params={**params, **auth.params()}, headers=auth.headers())
    if response.status_code != 200:
        raise google_error(response)
    return response.json()


# ------------------------------------------------------------------ reading
def video(auth: Auth, video_id: str) -> dict | None:
    data = _get(auth, "videos", {"part": "snippet,contentDetails,status,liveStreamingDetails,statistics", "id": video_id})
    items = data.get("items", [])
    return items[0] if items else None


def my_channel(auth: Auth) -> dict | None:
    data = _get(auth, "channels", {"part": "snippet,contentDetails", "mine": "true"})
    items = data.get("items", [])
    return items[0] if items else None


def live_status(item: dict) -> str:
    """scheduled | live | ended | none — from liveBroadcastContent/liveStreamingDetails."""
    content = (item.get("snippet") or {}).get("liveBroadcastContent", "none")
    details = item.get("liveStreamingDetails") or {}
    if content == "upcoming":
        return "scheduled"
    if content == "live" or (details.get("actualStartTime") and not details.get("actualEndTime")):
        return "live"
    if details.get("actualEndTime"):
        return "ended"
    return "none"


def captions_list(auth: Auth, video_id: str) -> list[dict]:
    return _get(auth, "captions", {"part": "snippet", "videoId": video_id}).get("items", [])


def captions_download(auth: Auth, caption_id: str, fmt: str = "srt") -> str:
    response = request("youtube", "GET", f"{_api()}/captions/{caption_id}", params={"tfmt": fmt}, headers=auth.headers())
    if response.status_code != 200:
        raise google_error(response)
    return response.text


def parse_duration(iso: str) -> float:
    """ISO-8601 duration (PT#H#M#S) -> seconds."""
    import re

    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", iso or "")
    if not match:
        return 0.0
    d, h, m, s = (float(x) if x else 0.0 for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def oembed(url: str) -> dict | None:
    """Public oEmbed endpoint: title/author without an API key."""
    response = request("youtube", "GET", get_settings().youtube_oembed_url, params={"url": url, "format": "json"},
                       max_attempts=2, timeout=15)
    if response.status_code == 200:
        return response.json()
    if response.status_code in (401, 403, 404):
        return None
    raise google_error(response)


# --------------------------------------------------------------- uploading
@dataclass
class UploadProgress:
    session_uri: str
    total: int
    confirmed: int = 0
    video_id: str | None = None
    resource: dict | None = None


def start_upload(auth: Auth, file_size: int, snippet: dict, status: dict) -> str:
    response = request(
        "youtube", "POST", f"{_upload_api()}/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={**auth.headers(), "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(file_size), "X-Upload-Content-Type": "video/mp4"},
        content=json.dumps({"snippet": snippet, "status": status}).encode(),
    )
    if response.status_code != 200 or not response.headers.get("location"):
        raise google_error(response)
    return response.headers["location"]


def _range_end(response) -> int:
    value = response.headers.get("range")
    return int(value.split("-")[-1]) + 1 if value else 0


def query_upload(auth: Auth, progress: UploadProgress) -> UploadProgress:
    response = request("youtube", "PUT", progress.session_uri,
                       headers={**auth.headers(), "Content-Length": "0", "Content-Range": f"bytes */{progress.total}"},
                       retry_on={500, 502, 503, 504})
    if response.status_code in (200, 201):
        progress.resource = response.json()
        progress.video_id = progress.resource.get("id")
        progress.confirmed = progress.total
    elif response.status_code == 308:
        progress.confirmed = _range_end(response)
    elif response.status_code == 404:
        raise PlatformError("youtube", "Upload session expired", status=404, reason="session_expired")
    else:
        raise google_error(response)
    return progress


def upload_chunks(auth: Auth, progress: UploadProgress, path: Path, *,
                  on_progress: Callable[[UploadProgress], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> UploadProgress:
    """Send the remaining bytes; safe to call again after any interruption."""
    with open(path, "rb") as handle:
        while progress.video_id is None:
            if cancelled and cancelled():
                raise PlatformError("youtube", "Upload cancelled", reason="cancelled")
            start = progress.confirmed
            handle.seek(start)
            chunk = handle.read(CHUNK)
            end = start + len(chunk) - 1
            response = request(
                "youtube", "PUT", progress.session_uri, max_attempts=1, timeout=300, retry_on=set(),
                headers={**auth.headers(), "Content-Length": str(len(chunk)),
                         "Content-Range": f"bytes {start}-{end}/{progress.total}"},
                content=chunk,
            )
            if response.status_code in (200, 201):
                progress.resource = response.json()
                progress.video_id = progress.resource.get("id")
                progress.confirmed = progress.total
            elif response.status_code == 308:
                progress.confirmed = _range_end(response)
            elif response.status_code in (500, 502, 503, 504, 429):
                # Interrupted: ask the server how much it really has.
                query_upload(auth, progress)
            elif response.status_code == 404:
                raise PlatformError("youtube", "Upload session expired", status=404, reason="session_expired")
            else:
                raise google_error(response)
            if on_progress:
                on_progress(progress)
    return progress


def video_status(auth: Auth, video_id: str) -> dict | None:
    data = _get(auth, "videos", {"part": "status,processingDetails,snippet", "id": video_id})
    items = data.get("items", [])
    return items[0] if items else None


#: ``status`` properties a videos.update request may send. Anything left out of an
#: update is reset by YouTube, so updates always start from the video's current values.
WRITABLE_STATUS = ("privacyStatus", "publishAt", "embeddable", "license", "publicStatsViewable",
                   "selfDeclaredMadeForKids", "containsSyntheticMedia")


def update_status(auth: Auth, video_id: str, current: dict, changes: dict) -> dict:
    """Change privacy/schedule while keeping every other status property as it is."""
    body = {k: v for k, v in (current or {}).items() if k in WRITABLE_STATUS}
    body.update({k: v for k, v in changes.items() if v is not None})
    for key in [k for k, v in changes.items() if v is None]:
        body.pop(key, None)  # e.g. publishAt=None removes the schedule
    response = request("youtube", "PUT", f"{_api()}/videos", params={"part": "status"},
                       headers={**auth.headers(), "Content-Type": "application/json"},
                       content=json.dumps({"id": video_id, "status": body}).encode())
    if response.status_code != 200:
        raise google_error(response)
    return response.json()


WRITABLE_SNIPPET = ("title", "description", "tags", "categoryId", "defaultLanguage", "defaultAudioLanguage")


def update_snippet(auth: Auth, video_id: str, current: dict, changes: dict) -> dict:
    """Change title/description/tags; other snippet properties keep their current values."""
    body = {k: v for k, v in (current or {}).items() if k in WRITABLE_SNIPPET}
    body.update(changes)
    response = request("youtube", "PUT", f"{_api()}/videos", params={"part": "snippet"},
                       headers={**auth.headers(), "Content-Type": "application/json"},
                       content=json.dumps({"id": video_id, "snippet": body}).encode())
    if response.status_code != 200:
        raise google_error(response)
    return response.json()


def insert_caption(auth: Auth, video_id: str, language: str, name: str, srt_text: str) -> dict:
    boundary = f"clipforge-{uuid.uuid4().hex}"
    metadata = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n{srt_text}\r\n--{boundary}--\r\n").encode()
    response = request("youtube", "POST", f"{_upload_api()}/captions",
                       params={"uploadType": "multipart", "part": "snippet"},
                       headers={**auth.headers(), "Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
    if response.status_code not in (200, 201):
        raise google_error(response)
    return response.json()


def set_thumbnail(auth: Auth, video_id: str, image: Path) -> dict:
    response = request("youtube", "POST", f"{_upload_api()}/thumbnails/set",
                       params={"videoId": video_id, "uploadType": "media"},
                       headers={**auth.headers(), "Content-Type": "image/jpeg"}, content=image.read_bytes())
    if response.status_code not in (200, 201):
        raise google_error(response)
    return response.json()


def add_to_playlist(auth: Auth, playlist_id: str, video_id: str) -> dict:
    response = request("youtube", "POST", f"{_api()}/playlistItems", params={"part": "snippet"},
                       headers={**auth.headers(), "Content-Type": "application/json"},
                       content=json.dumps({"snippet": {"playlistId": playlist_id,
                                                       "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).encode())
    if response.status_code not in (200, 201):
        raise google_error(response)
    return response.json()


def statistics(auth: Auth, video_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        data = _get(auth, "videos", {"part": "statistics,contentDetails,status", "id": ",".join(video_ids[i:i + 50])})
        for item in data.get("items", []):
            out[item["id"]] = item
    return out


def analytics_report(auth: Auth, video_id: str, start_date: str, end_date: str, metrics: list[str]) -> dict:
    response = request("youtube", "GET", f"{get_settings().youtube_analytics_base.rstrip('/')}/reports", headers=auth.headers(), params={
        "ids": "channel==MINE", "startDate": start_date, "endDate": end_date, "metrics": ",".join(metrics),
        "dimensions": "video", "filters": f"video=={video_id}",
    })
    if response.status_code != 200:
        raise google_error(response)
    return response.json()
