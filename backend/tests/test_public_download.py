"""Public video downloads with yt-dlp.

YouTube and Instagram are unreachable from the build sandbox, so the real
platform downloads are BLOCKED here (see docs/TEST_RESULTS.md). These tests run
the same code path — yt-dlp, size limits, storage, the media job, honest error
states and workflow routing — against a local HTTP server, using yt-dlp's
generic extractor in place of the platform extractors.
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

from clipforge.db.base import session_scope
from clipforge.db.models import Job, MediaAsset, Project, Source
from clipforge.ingestion import public_download as pd
from clipforge.ingestion import resolver as rs
from clipforge.jobs import queue


@pytest.fixture
def media_server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", str(root / "clip.mp4")], check=True, timeout=120)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", root
    server.shutdown()
    server.server_close()


@pytest.fixture
def generic_extractor(monkeypatch):
    """Stand in for the YouTube/Instagram extractors with yt-dlp's generic one (local test server)."""
    monkeypatch.setattr(pd, "ALLOWED_EXTRACTORS", ["generic"])


# --------------------------------------------------------------------- unit
@pytest.mark.parametrize("message,state", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", rs.SOURCE_PRIVATE),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", rs.LOGIN_REQUIRED),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video", rs.PERMISSION_REQUIRED),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", rs.LOGIN_REQUIRED),
    ("ERROR: [instagram] Abc: Requested content is not available, rate-limit reached or login required", rs.LOGIN_REQUIRED),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", rs.ACCESS_DENIED),
    ("ERROR: [youtube] abc: This video is DRM protected", rs.ACCESS_DENIED),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", rs.SOURCE_UNAVAILABLE),
    ("ERROR: Unsupported URL: http://127.0.0.1/x", rs.SOURCE_UNAVAILABLE),
])
def test_errors_map_to_honest_access_states(message, state):
    error = pd.explain(message)
    assert error.state == state and error.retryable is False
    assert "cookies" not in str(error).lower()  # never suggests signing in or exporting browser cookies


def test_temporary_problems_are_retried():
    assert pd.explain("ERROR: HTTP Error 429: Too Many Requests").retryable
    assert pd.explain("ERROR: Unable to download webpage: <urlopen error timed out>").retryable


def test_refusals_from_metadata():
    hour = 3600
    assert pd.refusal({"live_status": "is_live"}, 6 * hour).state == rs.METADATA_ONLY
    assert pd.refusal({"live_status": "post_live"}, 6 * hour).retryable
    assert pd.refusal({"availability": "subscriber_only"}, 6 * hour).state == rs.PERMISSION_REQUIRED
    assert pd.refusal({"availability": "needs_auth"}, 6 * hour).state == rs.LOGIN_REQUIRED
    assert pd.refusal({"duration": 8 * hour}, 6 * hour).state == rs.ACCESS_DENIED
    assert pd.refusal({"live_status": "was_live", "availability": "public", "duration": 600}, 6 * hour) is None


def test_downloader_reports_what_it_can_do():
    info = pd.available()
    assert info["ok"] and info["js_runtime"] == "deno" and info["youtube_challenge_solver"] and info["ffmpeg"]


# ---------------------------------------------------------- download path (local)
def test_download_stores_the_video(media_server, generic_extractor, tmp_path):
    base, root = media_server
    seen: list[float] = []
    details: list[dict] = []
    result = pd.download(f"{base}/clip.mp4", tmp_path / "out", max_bytes=50 * 1024 * 1024, max_seconds=3600,
                         on_progress=lambda f, _label, detail: (seen.append(f), details.append(detail)))
    size = (root / "clip.mp4").stat().st_size
    assert result.path.exists() and result.path.stat().st_size == size
    assert result.info["extractor_key"] == "Generic" and seen and max(seen) == pytest.approx(1.0)
    # Bytes, size and the number of files are reported for the progress display.
    assert details[-1]["downloaded"] == size and details[-1]["total"] == size
    assert details[-1]["parts"] == 1 and seen == sorted(seen)


def test_download_size_limit(media_server, generic_extractor, tmp_path):
    base, _root = media_server
    with pytest.raises(pd.DownloadError) as err:
        pd.download(f"{base}/clip.mp4", tmp_path / "out", max_bytes=2048, max_seconds=3600)
    assert err.value.state == rs.ACCESS_DENIED and "limit" in str(err.value)
    assert not list((tmp_path / "out").glob("download.mp4"))


def test_only_platform_extractors_are_loaded(media_server, tmp_path):
    """With the real allow-list, an arbitrary URL can never be fetched through yt-dlp."""
    base, _root = media_server
    with pytest.raises(pd.DownloadError) as err:
        pd.download(f"{base}/clip.mp4", tmp_path / "out", max_bytes=50 * 1024 * 1024, max_seconds=3600)
    assert err.value.state == rs.SOURCE_UNAVAILABLE
    assert not (tmp_path / "out" / "download.mp4").exists()


def test_downloader_never_signs_in_or_fakes_location(monkeypatch, tmp_path):
    captured: dict = {}

    class Recorder:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            raise pd.DownloadError("stop", state=rs.SOURCE_UNAVAILABLE)

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Recorder)
    with pytest.raises(pd.DownloadError):
        pd.download("https://www.youtube.com/watch?v=aaaaaaaaaaa", tmp_path, max_bytes=1, max_seconds=1)
    assert captured["cookiefile"] is None and captured["usenetrc"] is False and "cookiesfrombrowser" not in captured
    assert "username" not in captured and "password" not in captured
    assert captured["geo_bypass"] is False and captured["remote_components"] == []
    assert captured["allowed_extractors"] == ["youtube", "instagram"] and captured["noplaylist"] is True
    assert captured["js_runtimes"]["deno"]["path"]


# ------------------------------------------------------------- media job + workflow
def _source(url: str, platform: str = "youtube") -> uuid.UUID:
    with session_scope() as db:
        source = Source(platform=platform, source_type="video", external_id=uuid.uuid4().hex[:11], original_url=url,
                        normalized_url=url)
        db.add(source)
        db.flush()
        return source.id


def test_download_job_stores_media_in_the_media_worker(media_server, generic_extractor):
    import clipforge.handlers  # noqa: F401
    from clipforge.jobs.worker import Worker

    base, _root = media_server
    source_id = _source(f"{base}/clip.mp4")
    with session_scope() as db:
        job_id = queue.enqueue(db, "source.download", {"source_id": str(source_id), "url": f"{base}/clip.mp4"},
                               queue="media").id
    Worker(["media"], worker_id="dl-test").run(max_jobs=1, idle_exit_s=3)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "completed", job.error
        source = db.get(Source, source_id)
        asset = db.get(MediaAsset, source.media_asset_id)
        assert source.media_status == "available" and asset.sha256 == source.content_sha256
        assert asset.info["origin"]["method"] == "public_download" and asset.info["origin"]["tool"] == "yt-dlp"
        assert source.source_metadata["download"]["extractor_key"] == "Generic"
    # A second run reuses the stored file instead of downloading again.
    with session_scope() as db:
        again = queue.enqueue(db, "source.download", {"source_id": str(source_id), "url": f"{base}/clip.mp4"},
                              queue="media").id
    Worker(["media"], worker_id="dl-test2").run(max_jobs=1, idle_exit_s=3)
    with session_scope() as db:
        assert db.get(Job, again).result["reused"] is True


@pytest.mark.contract
def test_sign_in_wall_becomes_an_honest_access_state(platforms, monkeypatch):
    """The resolver hopes a public Reel can be downloaded; Instagram wants a sign-in → the project says so."""
    import clipforge.handlers  # noqa: F401
    from clipforge.jobs.worker import Worker
    from tests.test_resolver import make_source

    def refuse(*_args, **_kwargs):
        raise pd.explain("ERROR: [Instagram] Pub123456: Requested content is not available, rate-limit reached or login required")

    monkeypatch.setattr(pd, "download", refuse)
    source_id = make_source("https://www.instagram.com/reel/Pub123456/")
    with session_scope() as db:
        project = Project(source_id=source_id, title="t", request={"count": 2, "aspects": ["9:16"], "platforms": ["instagram"]})
        db.add(project)
        db.flush()
        job_id = queue.enqueue(db, "workflow.video", {"project_id": str(project.id), "source_id": str(source_id)},
                               project_id=project.id).id
        project_id = project.id
    Worker(["default", "media"], worker_id="wf-dl").run(idle_exit_s=4)
    with session_scope() as db:
        job, project, source = db.get(Job, job_id), db.get(Project, project_id), db.get(Source, source_id)
        assert job.status == "completed" and job.status_label == "Access required", job.error
        assert project.status == "access_required"
        assert source.access_status == rs.LOGIN_REQUIRED
        assert any("signed-in users" in reason for reason in project.limitations)
        assert {a["action"] for a in source.access_detail["alternatives"]} >= {"connect_instagram", "upload"}
        downloads = db.execute(select(Job).where(Job.type == "source.download")).scalars().all()
        assert len(downloads) == 1 and downloads[0].attempts == 1  # a sign-in wall is not retried


def test_cache_and_work_paths_stay_inside_the_data_directory(media_server, generic_extractor, tmp_path):
    base, _root = media_server
    cache = tmp_path / "cache"
    result = pd.download(f"{base}/clip.mp4", tmp_path / "out", max_bytes=50 * 1024 * 1024, max_seconds=3600, cache_dir=cache)
    assert Path(result.path).parent == tmp_path / "out"
