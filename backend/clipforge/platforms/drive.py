"""Google Drive (read-only): an authorized media library for the owner's master files.

Owners often keep the original files of their own YouTube videos in Drive
(uploads or Google Takeout exports). With ``drive.readonly`` access CLIPFORGE
can fetch those masters automatically when the owner pastes the YouTube link,
so the normal workflow stays "link in" without manual downloads.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from ..config import get_settings
from .http import PlatformError, google_error, request

FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,videoMediaMetadata,capabilities(canDownload)"


def _api() -> str:
    return get_settings().google_api_base.rstrip("/") + "/drive/v3"


def file_info(token: str, file_id: str) -> dict:
    response = request("google_drive", "GET", f"{_api()}/files/{file_id}",
                       params={"fields": FIELDS, "supportsAllDrives": "true"},
                       headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise google_error(response)
    return response.json()


def search_videos(token: str, name_hint: str, *, limit: int = 25) -> list[dict]:
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", name_hint)][:4]
    clauses = ["mimeType contains 'video/'", "trashed = false"] + [f"name contains '{w}'" for w in words]
    response = request("google_drive", "GET", f"{_api()}/files", headers={"Authorization": f"Bearer {token}"}, params={
        "q": " and ".join(clauses), "pageSize": str(limit), "fields": f"files({FIELDS})",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "modifiedTime desc"})
    if response.status_code != 200:
        raise google_error(response)
    return response.json().get("files", [])


def download(token: str, file_id: str, dest: Path, *, max_bytes: int) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as client:
        with client.stream("GET", f"{_api()}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                           headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status_code != 200:
                response.read()
                raise google_error(response)
            with open(tmp, "wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise PlatformError("google_drive", "File exceeds the configured download limit", reason="too_large")
                    digest.update(chunk)
                    handle.write(chunk)
    tmp.replace(dest)
    return {"size": size, "sha256": digest.hexdigest()}
