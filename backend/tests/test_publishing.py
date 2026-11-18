"""Publishing (CONTRACT tests against a local fake of the documented APIs).

Real YouTube/Instagram publishing requires the owner's OAuth credentials and
platform approval, and the platforms are unreachable from the build sandbox —
those acceptance tests are reported as BLOCKED in docs/TEST_RESULTS.md.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest
from sqlalchemy import func, select

from clipforge.db.base import session_scope
from clipforge.db.models import (
    CaptionTrack,
    Clip,
    Job,
    MediaAsset,
    OutputPackage,
    PlatformQuotaUsage,
    Project,
    Publication,
    Render,
    Source,
)
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker
from clipforge.services import app_settings
from clipforge.storage import get_storage
from tests.conftest import connect

pytestmark = pytest.mark.contract

SRT = "1\n00:00:00,000 --> 00:00:01,500\nNever compete on price.\n"


def _make_package(tmp_path, platform: str, *, seconds: float = 2.0, captions_burned: bool = True, noise: bool = False) -> uuid.UUID:
    video = tmp_path / f"clip-{uuid.uuid4().hex[:6]}.mp4"
    source = "testsrc2=size=1080x1920:rate=30" if not noise else "nullsrc=size=1080x1920:rate=30,geq=random(1)*255:128:128"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source, "-f", "lavfi", "-i",
                    "sine=frequency=500", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(video)], check=True)
    thumb = tmp_path / f"thumb-{uuid.uuid4().hex[:6]}.jpg"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(thumb)], check=True)
    srt = tmp_path / f"subs-{uuid.uuid4().hex[:6]}.srt"
    srt.write_text(SRT)
    storage = get_storage()
    with session_scope() as db:
        source_row = Source(platform="upload", source_type="file", external_id=uuid.uuid4().hex)
        db.add(source_row)
        db.flush()
        project = Project(source_id=source_row.id, title="Pricing talk", request={})
        db.add(project)
        db.flush()
        assets = {}
        for kind, path, mime in (("clip", video, "video/mp4"), ("thumbnail", thumb, "image/jpeg"), ("subtitle", srt, "application/x-subrip")):
            key = f"projects/{project.id}/{path.name}"
            storage.put_file(path, key)
            asset = MediaAsset(kind=kind, storage_key=key, mime=mime, size_bytes=path.stat().st_size, project_id=project.id,
                               info={"frame_time": 0.5} if kind == "thumbnail" else {})
            db.add(asset)
            db.flush()
            assets[kind] = asset.id
        clip = Clip(project_id=project.id, source_id=source_row.id, rank=1, start_s=0, end_s=seconds, title="Never compete on price",
                    hook="Never compete on price.", transcript_text="Never compete on price.", status="rendered")
        db.add(clip)
        db.flush()
        render = Render(clip_id=clip.id, aspect="9:16", status="done", video_asset_id=assets["clip"],
                        srt_asset_id=assets["subtitle"], thumbnail_asset_id=assets["thumbnail"],
                        captions_burned=captions_burned, quality_report={"passed": True},
                        edit_decisions={"kept_intervals": [[0, seconds]]})
        db.add(render)
        db.flush()
        package = OutputPackage(clip_id=clip.id, render_id=render.id, platform=platform, title="Never compete on price #shorts",
                                description="Compete on value.\n\n#shorts", tags=["pricing", "value"],
                                caption="Never compete on price.\n\n#Pricing #reels", hashtags=["#Pricing", "#reels"],
                                subtitle_asset_id=assets["subtitle"], thumbnail_asset_id=assets["thumbnail"],
                                cover_asset_id=assets["thumbnail"], privacy="private", status="ready")
        db.add(package)
        db.flush()
        return package.id


def _publish(package_id: uuid.UUID) -> Job:
    import clipforge.handlers  # noqa: F401

    with session_scope() as db:
        job_id = queue.enqueue(db, "workflow.publish", {"package_id": str(package_id)}).id
    Worker(["default"], worker_id=f"pub-{uuid.uuid4().hex[:4]}").run(max_jobs=1)
    with session_scope() as db:
        job = db.get(Job, job_id)
        db.expunge(job)
        return job


def test_youtube_publish_captions_thumbnail_and_analytics_schedule(platforms, tmp_path):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    job = _publish(package_id)
    assert job.status == "completed" and job.status_label == "Published", job.error
    with session_scope() as db:
        publication = db.execute(select(Publication)).scalar_one()
        assert publication.status == "published" and publication.external_id.startswith("VID")
        assert publication.external_url == f"https://www.youtube.com/shorts/{publication.external_id}"
        assert publication.verification["upload_status"] == "processed"
        assert publication.verification["thumbnail_set"] is True
        track = db.execute(select(CaptionTrack)).scalar_one()
        assert track.status == "serving" and track.language == "en" and track.verified_at is not None
        offsets = {j.payload["offset_hours"] for j in db.execute(select(Job).where(Job.type == "analytics.collect")).scalars()}
        assert offsets == {1, 24, 72, 168, 672}
        quota = db.execute(select(PlatformQuotaUsage)).scalar_one()
        assert quota.units >= 1600 + 400 + 50
    upload_meta = next(iter(platforms.uploads.values()))["meta"]
    assert upload_meta["snippet"]["title"] == "Never compete on price #shorts"
    assert upload_meta["status"]["privacyStatus"] == "private"
    assert platforms.inserted_captions[0]["videoId"] == publication.external_id


def test_duplicate_publication_is_prevented(platforms, tmp_path):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    assert _publish(package_id).status_label == "Published"
    second = _publish(package_id)
    assert second.status == "completed" and second.status_label == "Already published" and second.result["duplicate"]
    assert len(platforms.uploads) == 1  # no second upload session was ever created
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Publication)).scalar_one() == 1


def test_interrupted_upload_resumes_without_duplicate(platforms, tmp_path, monkeypatch):
    from clipforge.platforms import youtube

    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube", seconds=3.0, noise=True)
    platforms.fail_next_chunk = True  # the server drops a chunk half-way and answers 503
    job = _publish(package_id)
    assert job.status == "completed", job.error
    assert len(platforms.uploads) == 1 and len(platforms.uploaded_videos) == 1
    status_queries = [r for r in platforms.requests if r["method"] == "PUT" and r["headers"].get("Content-Range", "").startswith("bytes */")]
    assert status_queries, "client must ask the server how many bytes it has before resuming"
    upload = next(iter(platforms.uploads.values()))
    assert upload["received"] == upload["total"]


def test_quota_exhaustion_reschedules_instead_of_failing(platforms, tmp_path):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    platforms.quota_exceeded = True
    job = _publish(package_id)
    assert job.status == "completed" and job.status_label == "Scheduled" and job.result["scheduled_at"]
    with session_scope() as db:
        later = db.execute(select(Job).where(Job.type == "workflow.publish", Job.status == "queued")).scalar_one()
        assert later.run_after > job.finished_at
        assert db.get(OutputPackage, package_id).status == "scheduled"


def test_policies_block_publishing(platforms, tmp_path):
    package_id = _make_package(tmp_path, "youtube")
    not_connected = _publish(package_id)
    assert not_connected.status == "failed" and "Connect your YouTube channel" in not_connected.error["message"]
    connect("youtube", "UC_OWNER")
    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"policy": "draft_only"})
    draft = _publish(package_id)
    assert draft.status == "failed" and "draft-only" in draft.error["message"]
    assert platforms.uploads == {}


def test_instagram_reel_resumable_publish(platforms, tmp_path, monkeypatch):
    from clipforge.publishing import service

    monkeypatch.setattr(service, "IG_POLL_INTERVAL_S", 0.1)
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS", "username": "owner_reels"})
    package_id = _make_package(tmp_path, "instagram")
    job = _publish(package_id)
    assert job.status == "completed" and job.status_label == "Published", job.error
    container = next(iter(platforms.containers.values()))
    assert container["params"]["media_type"] == "REELS" and container["params"]["upload_type"] == "resumable"
    assert container["params"]["caption"].startswith("Never compete on price.") and "#Pricing" in container["params"]["caption"]
    assert container["params"]["thumb_offset"] == "500"
    rupload = [r for r in platforms.requests if r["path"].startswith("/rupload/")][0]
    assert rupload["headers"]["Authorization"].startswith("OAuth ") and int(rupload["headers"]["file_size"]) == rupload["size"]
    with session_scope() as db:
        publication = db.execute(select(Publication)).scalar_one()
        assert publication.status == "published" and publication.external_url.startswith("https://www.instagram.com/reel/")


def test_instagram_requires_burned_in_captions(platforms, tmp_path):
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    package_id = _make_package(tmp_path, "instagram", captions_burned=False)
    job = _publish(package_id)
    assert job.status == "failed" and "burned-in captions" in job.error["message"]
    assert platforms.containers == {}
