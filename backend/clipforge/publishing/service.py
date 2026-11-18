"""Publishing to YouTube and Instagram (Parts 14–16).

Rules enforced here:

* **No duplicates.** A publication's idempotency key is the platform, the
  account and the SHA-256 of the exact rendered file. A second request for the
  same key returns the existing publication instead of uploading again.
* **Crash-safe.** Every irreversible step is persisted before the next one:
  the YouTube resumable session URI before any bytes are sent, the returned
  video id before captions/thumbnail, the Instagram container id before
  polling and the media id before verification. A retry resumes from there.
* **Honest status.** ``published`` is set only after the platform confirms it
  (YouTube upload/processing status, Instagram permalink lookup).
* **Policy and limits.** Emergency stop, disabled integrations, per-day caps
  and platform quotas are checked before any upload.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from ..db.base import session_scope, utcnow
from ..db.models import (
    CaptionTrack,
    Clip,
    MediaAsset,
    OutputPackage,
    Project,
    Publication,
    Render,
    SocialAccount,
)
from ..platforms import google, instagram, youtube
from ..platforms.http import PlatformError
from ..services import app_settings, control
from ..storage import get_storage, sha256_file

log = logging.getLogger(__name__)


class PublishError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool = False, reschedule_at: datetime | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.reschedule_at = reschedule_at


@dataclass
class Prepared:
    package_id: uuid.UUID
    platform: str
    account_id: uuid.UUID
    video_path: Path
    video_sha: str
    publication_id: uuid.UUID
    duplicate: bool


def idempotency_key(platform: str, account_id: uuid.UUID, video_sha: str) -> str:
    return hashlib.sha256(f"{platform}:{account_id}:{video_sha}".encode()).hexdigest()


def _tomorrow() -> datetime:
    now = datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=15, second=0, microsecond=0)


def prepare(package_id: uuid.UUID) -> Prepared:
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        if package is None:
            raise PublishError("The publishing package no longer exists.", code="missing")
        if not control.integration_enabled(db, f"publish:{package.platform}"):
            raise PublishError("Publishing is blocked (emergency stop or integration disabled in Settings).", code="blocked")
        publishing = app_settings.get_section(db, "publishing")
        if publishing.policy == "draft_only":
            raise PublishError("Publishing policy is draft-only.", code="draft_only")
        account = db.execute(select(SocialAccount).where(SocialAccount.platform == package.platform,
                                                         SocialAccount.status == "connected")).scalars().first()
        if account is None:
            name = "YouTube channel" if package.platform == "youtube" else "Instagram professional account"
            raise PublishError(f"Connect your {name} in Settings → Connections before publishing.", code="not_connected")
        render = db.get(Render, package.render_id)
        if render is not None and render.status == "expired":
            raise PublishError("This clip's files were removed by storage clean-up. Re-render it (My Videos → Adjust) "
                               "before publishing.", code="render_expired")
        if render is None or render.status != "done" or not render.quality_report.get("passed"):
            raise PublishError("This clip has not passed its quality checks, so it won't be published.", code="qc_failed")
        if package.platform == "instagram" and not render.captions_burned:
            raise PublishError("Instagram Reels need burned-in captions; re-render this clip with captions on.",
                               code="captions_required")
        asset = db.get(MediaAsset, render.video_asset_id)
        path = get_storage().local_path(asset.storage_key)
        if not asset.sha256:
            asset.sha256 = sha256_file(path)
        key = idempotency_key(package.platform, account.id, asset.sha256)
        publication = db.execute(select(Publication).where(Publication.idempotency_key == key)).scalar_one_or_none()
        if publication is not None and publication.status == "published":
            return Prepared(package.id, package.platform, account.id, path, asset.sha256, publication.id, duplicate=True)
        limit = publishing.max_per_day_youtube if package.platform == "youtube" else publishing.max_per_day_instagram
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today = db.execute(select(func.count()).select_from(Publication).where(
            Publication.platform == package.platform, Publication.status == "published",
            Publication.published_at >= day_start)).scalar_one()
        if today >= limit:
            raise PublishError(f"Daily {package.platform} publishing limit ({limit}) reached; scheduled for tomorrow.",
                               code="daily_limit", reschedule_at=_tomorrow())
        if package.platform == "youtube" and google.quota_remaining(db) < google.QUOTA_COST["videos.insert"] + 500:
            raise PublishError("YouTube API quota for today is nearly used up; scheduled for after the quota resets.",
                               code="quota", reschedule_at=_tomorrow())
        if publication is None:
            publication = Publication(package_id=package.id, account_id=account.id, platform=package.platform,
                                      idempotency_key=key, status="pending",
                                      request_snapshot={"title": package.title, "caption": package.caption[:500],
                                                        "privacy": package.privacy, "video_sha256": asset.sha256})
            db.add(publication)
            db.flush()
        publication.attempts += 1
        return Prepared(package.id, package.platform, account.id, path, asset.sha256, publication.id, duplicate=False)


def _update(publication_id: uuid.UUID, **fields: Any) -> None:
    with session_scope() as db:
        publication = db.get(Publication, publication_id)
        for key, value in fields.items():
            setattr(publication, key, value)


# ------------------------------------------------------------------- YouTube
def publish_youtube(prep: Prepared, *, on_progress=None, cancelled=None, verify_timeout_s: float = 300) -> dict:
    with session_scope() as db:
        package = db.get(OutputPackage, prep.package_id)
        account = db.get(SocialAccount, prep.account_id)
        publishing = app_settings.get_section(db, "publishing")
        clips_cfg = app_settings.get_section(db, "clips")
        publication = db.get(Publication, prep.publication_id)
        state = dict(publication.upload_state or {})
        token = google.access_token(db, account)
        snippet = {"title": package.title[:100] or "Untitled", "description": package.description[:5000],
                   "tags": package.tags[:30], "categoryId": publishing.youtube_category_id,
                   "defaultLanguage": clips_cfg.languages[0], "defaultAudioLanguage": clips_cfg.languages[0]}
        status: dict[str, Any] = {"privacyStatus": package.privacy or publishing.default_privacy,
                                  "selfDeclaredMadeForKids": publishing.made_for_kids, "embeddable": True}
        if publication.scheduled_at and publication.scheduled_at > utcnow():
            status.update(privacyStatus="private", publishAt=publication.scheduled_at.isoformat().replace("+00:00", "Z"))
        srt_key = db.get(MediaAsset, package.subtitle_asset_id).storage_key if package.subtitle_asset_id else None
        thumb_key = db.get(MediaAsset, package.thumbnail_asset_id).storage_key if package.thumbnail_asset_id else None
        language = clips_cfg.languages[0]
        captions_enabled = publishing.upload_youtube_captions
        playlist = publishing.youtube_playlist_id
    auth = youtube.Auth(access_token=token)
    video_id = state.get("video_id")
    if not video_id:
        size = prep.video_path.stat().st_size
        progress = None
        if state.get("session_uri"):
            progress = youtube.UploadProgress(state["session_uri"], size)
            try:
                youtube.query_upload(auth, progress)
            except PlatformError as exc:
                if exc.reason != "session_expired":
                    raise
                progress = None  # expired before any video was created: start over safely
        if progress is None:
            session_uri = youtube.start_upload(auth, size, snippet, status)
            with session_scope() as db:
                google.record_quota(db, "videos.insert")
            _update(prep.publication_id, status="uploading", upload_state={"session_uri": session_uri, "confirmed": 0})
            progress = youtube.UploadProgress(session_uri, size)

        def _saved(p: youtube.UploadProgress) -> None:
            _update(prep.publication_id, upload_state={"session_uri": p.session_uri, "confirmed": p.confirmed,
                                                       "video_id": p.video_id})
            if on_progress:
                on_progress(p.confirmed / max(1, p.total))

        youtube.upload_chunks(auth, progress, prep.video_path, on_progress=_saved, cancelled=cancelled)
        video_id = progress.video_id
        _update(prep.publication_id, status="processing", external_id=video_id,
                external_url=f"https://www.youtube.com/shorts/{video_id}",
                upload_state={"session_uri": progress.session_uri, "confirmed": progress.total, "video_id": video_id})
    verification: dict[str, Any] = {"video_id": video_id}
    # Captions: one track per language, re-uploaded only if the content changed.
    if captions_enabled and srt_key:
        srt_text = get_storage().local_path(srt_key).read_text(encoding="utf-8")
        digest = hashlib.sha256(srt_text.encode()).hexdigest()
        with session_scope() as db:
            track = db.execute(select(CaptionTrack).where(CaptionTrack.publication_id == prep.publication_id,
                                                          CaptionTrack.language == language)).scalar_one_or_none()
            needs_upload = track is None or track.content_sha256 != digest or track.status != "serving"
        if needs_upload:
            try:
                inserted = youtube.insert_caption(auth, video_id, language, f"{language.upper()} (CLIPFORGE)", srt_text)
                with session_scope() as db:
                    google.record_quota(db, "captions.insert")
                    if track is None:
                        db.add(CaptionTrack(publication_id=prep.publication_id, language=language,
                                            external_id=inserted["id"], content_sha256=digest, status="uploaded"))
                    else:
                        row = db.get(CaptionTrack, track.id)
                        row.external_id, row.content_sha256, row.status = inserted["id"], digest, "uploaded"
            except PlatformError as exc:
                verification["captions_error"] = str(exc)[:300]
        try:
            tracks = youtube.captions_list(auth, video_id)
            with session_scope() as db:
                google.record_quota(db, "captions.list")
                for row in db.execute(select(CaptionTrack).where(CaptionTrack.publication_id == prep.publication_id)).scalars():
                    match = next((t for t in tracks if t.get("id") == row.external_id), None)
                    if match is not None:
                        row.status = (match.get("snippet") or {}).get("status", "serving")
                        row.verified_at = utcnow()
                verification["captions"] = [{"id": t.get("id"), "language": (t.get("snippet") or {}).get("language"),
                                             "status": (t.get("snippet") or {}).get("status")} for t in tracks]
        except PlatformError as exc:
            verification["captions_verify_error"] = str(exc)[:300]
    if thumb_key and not verification.get("thumbnail_set"):
        try:
            youtube.set_thumbnail(auth, video_id, get_storage().local_path(thumb_key))
            with session_scope() as db:
                google.record_quota(db, "thumbnails.set")
            verification["thumbnail_set"] = True
        except PlatformError as exc:  # custom thumbnails need a verified channel
            verification["thumbnail_error"] = str(exc)[:300]
    if playlist:
        try:
            youtube.add_to_playlist(auth, playlist, video_id)
            verification["playlist"] = playlist
        except PlatformError as exc:
            verification["playlist_error"] = str(exc)[:300]
    # Only a confirmed upload/processing status counts as published.
    import time

    deadline = time.monotonic() + verify_timeout_s
    item = None
    while True:
        item = youtube.video_status(auth, video_id)
        upload_status = ((item or {}).get("status") or {}).get("uploadStatus")
        processing = ((item or {}).get("processingDetails") or {}).get("processingStatus")
        if upload_status in ("failed", "rejected", "deleted"):
            reason = (item.get("status") or {}).get("rejectionReason") or (item.get("status") or {}).get("failureReason")
            _update(prep.publication_id, status="failed", last_error=f"YouTube {upload_status}: {reason}", verification=verification)
            raise PublishError(f"YouTube marked the upload as {upload_status} ({reason}).", code="youtube_rejected")
        if upload_status in ("processed", "uploaded") and processing in (None, "succeeded"):
            break
        if time.monotonic() > deadline:
            verification["note"] = "Still processing on YouTube; verification will be retried."
            _update(prep.publication_id, status="processing", verification=verification)
            raise PublishError("Uploaded; waiting for YouTube processing to finish.", code="processing", retryable=True)
        time.sleep(10)
    verification.update({"upload_status": upload_status, "processing": processing,
                         "privacy": ((item or {}).get("status") or {}).get("privacyStatus")})
    _finish(prep, external_id=video_id, url=f"https://www.youtube.com/shorts/{video_id}", verification=verification)
    return {"platform": "youtube", "video_id": video_id, "url": f"https://www.youtube.com/shorts/{video_id}",
            "verification": verification}


# ------------------------------------------------------------------ Instagram
IG_POLL_INTERVAL_S = 10.0


def publish_instagram(prep: Prepared, *, cancelled=None, poll_interval_s: float | None = None) -> dict:
    poll_interval_s = IG_POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s
    with session_scope() as db:
        package = db.get(OutputPackage, prep.package_id)
        account = db.get(SocialAccount, prep.account_id)
        publishing = app_settings.get_section(db, "publishing")
        publication = db.get(Publication, prep.publication_id)
        state = dict(publication.upload_state or {})
        if (account.meta or {}).get("account_type") not in (None, "BUSINESS", "MEDIA_CREATOR"):
            raise PublishError("Instagram publishing needs a Business or Creator (professional) account.", code="account_type")
        token = instagram.access_token(db, account)
        render = db.get(Render, package.render_id)
        video_asset = db.get(MediaAsset, render.video_asset_id)
        thumb = db.get(MediaAsset, render.thumbnail_asset_id) if render.thumbnail_asset_id else None
        thumb_offset = int(float((thumb.info or {}).get("frame_time", 0)) * 1000) if thumb else None
        public_url = get_storage().public_url(video_asset.storage_key)
        ig_user = account.external_account_id
    caption = (package.caption or "")[:2200]
    media_id = state.get("media_id")
    if not media_id:
        container_id = state.get("container_id")
        if not container_id:
            try:
                limit = instagram.publishing_limit(token, ig_user)
                if limit and limit.get("quota_usage", 0) >= (limit.get("config") or {}).get("quota_total", 50):
                    raise PublishError("Instagram's 24-hour publishing limit is reached; scheduled for tomorrow.",
                                       code="ig_limit", reschedule_at=_tomorrow())
            except PlatformError as exc:
                log.warning("Could not read Instagram publishing limit: %s", exc)
            container = instagram.create_reel_container(token, ig_user, caption=caption,
                                                        share_to_feed=publishing.instagram_share_to_feed,
                                                        thumb_offset_ms=thumb_offset, video_url=public_url)
            container_id = container["id"]
            state = {"container_id": container_id, "upload_uri": container.get("uri"), "method": "video_url" if public_url else "resumable"}
            _update(prep.publication_id, status="uploading", upload_state=state)
        if state.get("method") == "resumable" and not state.get("uploaded"):
            instagram.upload_resumable(token, state["upload_uri"], prep.video_path)
            state["uploaded"] = True
            _update(prep.publication_id, upload_state=state)
        instagram.wait_for_container(token, container_id, interval_s=poll_interval_s, cancelled=cancelled)
        media_id = instagram.publish_container(token, ig_user, container_id)
        state["media_id"] = media_id
        _update(prep.publication_id, status="processing", external_id=media_id, upload_state=state)
    info = instagram.media_info(token, media_id)
    if not info.get("permalink"):
        raise PublishError("Instagram has not confirmed the Reel yet.", code="processing", retryable=True)
    verification = {"media_id": media_id, "permalink": info["permalink"], "media_product_type": info.get("media_product_type"),
                    "timestamp": info.get("timestamp")}
    _finish(prep, external_id=media_id, url=info["permalink"], verification=verification)
    return {"platform": "instagram", "media_id": media_id, "url": info["permalink"], "verification": verification}


def _finish(prep: Prepared, *, external_id: str, url: str, verification: dict) -> None:
    with session_scope() as db:
        publication = db.get(Publication, prep.publication_id)
        publication.status = "published"
        publication.external_id = external_id
        publication.external_url = url
        publication.published_at = publication.published_at or utcnow()
        publication.verification = verification
        publication.last_error = None
        package = db.get(OutputPackage, prep.package_id)
        package.status = "published"
        clip = db.get(Clip, package.clip_id)
        project = db.get(Project, clip.project_id)
        offsets = app_settings.get_section(db, "analytics").collection_offsets_hours
        from ..jobs import queue

        for hours in offsets:
            queue.enqueue(db, "analytics.collect", {"publication_id": str(publication.id), "offset_hours": hours},
                          run_after=publication.published_at + timedelta(hours=hours), project_id=project.id,
                          dedupe_key=f"analytics:{publication.id}:{hours}", priority=180, max_attempts=4)
        log.info("Published %s %s -> %s", prep.platform, prep.package_id, url)


def publish(package_id: uuid.UUID, *, on_progress=None, cancelled=None) -> dict:
    prep = prepare(package_id)
    if prep.duplicate:
        with session_scope() as db:
            publication = db.get(Publication, prep.publication_id)
            return {"platform": prep.platform, "duplicate": True, "url": publication.external_url,
                    "external_id": publication.external_id}
    try:
        if prep.platform == "youtube":
            return publish_youtube(prep, on_progress=on_progress, cancelled=cancelled)
        return publish_instagram(prep, cancelled=cancelled)
    except PlatformError as exc:
        _update(prep.publication_id, last_error=str(exc)[:1000])
        if exc.reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise PublishError("YouTube API quota exceeded; scheduled for after the daily reset.", code="quota",
                               reschedule_at=_tomorrow()) from exc
        if exc.reason in ("reauth", "token") or exc.status in (401,):
            raise PublishError(f"{prep.platform.title()} access expired; reconnect the account.", code="reauth") from exc
        raise PublishError(str(exc), code="platform", retryable=exc.retryable) from exc
