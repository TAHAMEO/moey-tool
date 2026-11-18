"""Acquire source content through the method the resolver authorised.

Runs in the ``default`` worker (it may need OAuth tokens). Downloaded media is
untrusted: it is stored as-is and only ever opened by the isolated media
worker with hardened FFmpeg flags. Content is deduplicated by SHA-256.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import utcnow
from ..db.models import MediaAsset, SocialAccount, Source, Transcript
from ..platforms import drive, google, instagram, youtube
from ..platforms.http import PlatformError
from ..security import ssrf
from ..storage import get_storage, sha256_file
from . import resolver as r

log = logging.getLogger(__name__)


class AcquisitionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, state: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.state = state


def _store_source_media(db: Session, source: Source, path: Path, *, sha: str, mime: str, origin: dict) -> MediaAsset:
    existing = db.execute(select(MediaAsset).where(MediaAsset.sha256 == sha, MediaAsset.kind.in_(("source", "upload")))).scalars().first()
    if existing is not None:
        path.unlink(missing_ok=True)
        asset = existing
    else:
        key = f"sources/{source.id}/original{path.suffix or '.bin'}"
        size = get_storage().put_file(path, key, move=True)
        asset = MediaAsset(kind="source", storage_key=key, mime=mime, size_bytes=size, sha256=sha, source_id=source.id,
                           info={"origin": origin, "validated": False})
        db.add(asset)
        db.flush()
    source.media_asset_id = asset.id
    source.content_sha256 = sha
    source.media_status = "available"
    return asset


def _fresh_instagram_url(db: Session, media_id: str) -> str:
    account = db.execute(select(SocialAccount).where(SocialAccount.platform == "instagram",
                                                     SocialAccount.status == "connected")).scalars().first()
    if account is None:
        raise AcquisitionError("Instagram is no longer connected.", state=r.LOGIN_REQUIRED)
    try:
        url = instagram.fresh_media_url(instagram.access_token(db, account), media_id)
    except PlatformError as exc:
        raise AcquisitionError(f"Could not renew access to the Instagram media: {exc}", retryable=exc.retryable) from exc
    if not url:
        raise AcquisitionError("Instagram no longer provides the media file for this post.", state=r.ACCESS_DENIED)
    return url


def acquire(db: Session, source: Source, access: dict) -> dict:
    """Returns {'media_asset_id'|'transcript_id': ..., 'method': ...}."""
    method = access.get("method")
    params = access.get("acquire") or {}
    settings = get_settings()
    tmp_dir = settings.work_dir / "acquire" / str(source.id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_download_mb * 1024 * 1024

    if method == "upload":
        asset = db.get(MediaAsset, uuid.UUID(params["asset_id"]))
        if asset is None:
            raise AcquisitionError("The uploaded file is missing.")
        source.media_asset_id = asset.id
        source.content_sha256 = asset.sha256
        source.media_status = "available"
        return {"media_asset_id": str(asset.id), "method": method}

    if method == "direct_url":
        dest = tmp_dir / ("download" + (Path(params["url"].split("?")[0]).suffix[:6] or ".bin"))
        try:
            result = ssrf.safe_download(params["url"], dest, max_bytes=max_bytes)
        except ssrf.UnsafeURLError as exc:
            raise AcquisitionError(f"Link blocked for safety: {exc}", state=r.ACCESS_DENIED) from exc
        except ssrf.FetchError as exc:
            retry = exc.code in ("timeout",) or (exc.status or 0) >= 500
            state = {"access_denied": r.ACCESS_DENIED, "not_found": r.SOURCE_UNAVAILABLE}.get(exc.code)
            message = str(exc)
            if exc.code == "access_denied":
                message += " — if it was a temporary (signed) link it may have expired; paste a fresh link."
            raise AcquisitionError(message, retryable=retry, state=state) from exc
        asset = _store_source_media(db, source, result.path, sha=result.sha256, mime=result.content_type,
                                    origin={"method": method, "url": result.final_url, "redirects": result.redirects})
        return {"media_asset_id": str(asset.id), "method": method, "bytes": result.size}

    if method in ("google_drive", "owner_media_library") and params.get("drive_file_id"):
        account = db.get(SocialAccount, uuid.UUID(params["drive_account_id"]))
        if account is None:
            raise AcquisitionError("Google Drive is no longer connected.", state=r.LOGIN_REQUIRED)
        dest = tmp_dir / "drive_download.bin"
        try:
            info = drive.download(google.access_token(db, account), params["drive_file_id"], dest, max_bytes=max_bytes)
        except PlatformError as exc:
            raise AcquisitionError(str(exc), retryable=exc.retryable) from exc
        asset = _store_source_media(db, source, dest, sha=info["sha256"], mime="application/octet-stream",
                                    origin={"method": method, "drive_file_id": params["drive_file_id"]})
        return {"media_asset_id": str(asset.id), "method": method, "bytes": info["size"]}

    if method == "owner_media_library" and params.get("library_path"):
        library = (settings.library_dir / "masters").resolve()
        path = Path(params["library_path"]).resolve()
        if not str(path).startswith(str(library) + "/"):
            raise AcquisitionError("Library path is outside the media library.", state=r.ACCESS_DENIED)
        dest = tmp_dir / ("library" + path.suffix)
        shutil.copy2(path, dest)
        asset = _store_source_media(db, source, dest, sha=sha256_file(dest), mime="application/octet-stream",
                                    origin={"method": method, "library_file": path.name})
        return {"media_asset_id": str(asset.id), "method": method}

    if method == "instagram_own_media":
        dest = tmp_dir / "instagram.mp4"
        url = params["media_url"]
        for attempt in (1, 2):
            try:
                result = ssrf.safe_download(url, dest, max_bytes=max_bytes, allowed_redirect_hosts=None)
                break
            except ssrf.UnsafeURLError as exc:
                raise AcquisitionError(f"Instagram media link refused: {exc}", state=r.ACCESS_DENIED) from exc
            except ssrf.FetchError as exc:
                # Instagram's media URLs are signed and expire (e.g. when a retry runs hours later):
                # ask the API once for a fresh link to the same media instead of retrying a dead one.
                if attempt == 1 and exc.code in ("access_denied", "not_found") and params.get("media_id"):
                    url = _fresh_instagram_url(db, params["media_id"])
                    continue
                retry = exc.code == "timeout" or (exc.status or 0) >= 500
                raise AcquisitionError(f"Instagram media download failed: {exc}", retryable=retry) from exc
        host = ssrf.validate_url(result.final_url).host
        if not r.INSTAGRAM_CDN.search(host):
            dest.unlink(missing_ok=True)
            raise AcquisitionError(f"Unexpected media host {host}; refused.", state=r.ACCESS_DENIED)
        asset = _store_source_media(db, source, result.path, sha=result.sha256, mime=result.content_type,
                                    origin={"method": method, "media_id": params.get("media_id")})
        return {"media_asset_id": str(asset.id), "method": method, "bytes": result.size}

    if method == "youtube_owner_captions":
        account = db.execute(select(SocialAccount).where(SocialAccount.platform == "youtube",
                                                         SocialAccount.status == "connected")).scalars().first()
        if account is None:
            raise AcquisitionError("YouTube is no longer connected.", state=r.LOGIN_REQUIRED)
        try:
            text = youtube.captions_download(youtube.Auth(access_token=google.access_token(db, account)), params["caption_id"])
            google.record_quota(db, "captions.download")
        except PlatformError as exc:
            raise AcquisitionError(str(exc), retryable=exc.retryable) from exc
        from ..media.subtitles import parse_srt

        words = parse_srt(text)
        key = f"transcripts/{source.id}/youtube_captions.json"
        tmp = tmp_dir / "captions.json"
        tmp.write_text(json.dumps({"words": words}))
        get_storage().put_file(tmp, key, move=True)
        transcript = Transcript(source_id=source.id, origin="platform_captions", engine="youtube_captions",
                                model=params.get("language") or "", language=params.get("language"), status="completed",
                                word_level=False, storage_key=key, word_count=len(words),
                                text_preview=" ".join(w["text"] for w in words[:80]),
                                duration_s=words[-1]["end"] if words else 0.0)
        db.add(transcript)
        db.flush()
        source.transcript_status = "available"
        return {"transcript_id": str(transcript.id), "method": method, "words": len(words)}

    raise AcquisitionError(f"No acquisition method for access state {access.get('state')}")


def mark_resolved(source: Source, access: r.AccessResult) -> None:
    source.access_status = access.state
    source.access_detail = access.to_dict()
    source.live_status = access.live_status
    if access.metadata:
        source.source_metadata = {**(source.source_metadata or {}), **{k: v for k, v in access.metadata.items() if v is not None}}
    source.last_resolved_at = utcnow()
