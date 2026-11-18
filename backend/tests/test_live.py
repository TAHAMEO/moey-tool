"""Live processing with a REAL authenticated SRT feed pushed by FFmpeg.

Covers: waiting for the live source, authenticated ingest (wrong passphrase
rejected), real-time per-segment transcription, candidate highlights during
the stream, reconnection after an interruption, end-of-stream detection,
replay processing into rendered clips, and bounded retention.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from clipforge.config import get_settings
from clipforge.db.base import session_scope
from clipforge.db.models import Clip, Job, LiveSegment, LiveSession, Project, Render
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker
from clipforge.media import transcription

pytestmark = pytest.mark.slow
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_env(monkeypatch, tmp_path):
    if not transcription.ENGINES["vosk"].availability()[0]:
        pytest.skip("BLOCKED: no local ASR model installed")
    monkeypatch.setenv("CLIPFORGE_LIVE_SRT_PORT", str(_free_udp_port()))
    monkeypatch.setenv("CLIPFORGE_LIVE_SRT_PORT_COUNT", "1")
    monkeypatch.setenv("CLIPFORGE_LIVE_PUBLIC_HOST", "127.0.0.1")
    monkeypatch.setenv("CLIPFORGE_LIVE_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("CLIPFORGE_LIVE_RECONNECT_GRACE_S", "20")
    monkeypatch.setenv("CLIPFORGE_LIVE_SEGMENT_SECONDS", "4")
    get_settings.cache_clear()
    sys.path.insert(0, str(FIXTURES))
    from make_test_media import build

    info = build(tmp_path / "talk.mp4")
    yield info
    get_settings.cache_clear()


def _push(url: str, path: str, start: float, duration: float) -> int:
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-ss", str(start), "-t", str(duration),
                           "-i", path, "-vf", "scale=960:540", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                           "-c:a", "aac", "-f", "mpegts", url], timeout=duration + 60).returncode


def test_live_stream_end_to_end(owner_client, live_env):
    created = owner_client.post("/api/live/sessions", json={"retention_minutes": 60})
    assert created.status_code == 200, created.text
    session = created.json()
    publish_url = session["publish_url"]
    assert publish_url.startswith("srt://127.0.0.1:") and "passphrase=" in publish_url
    with session_scope() as db:
        project = Project(source_id=uuid.UUID(session["source_id"]), title="Live test",
                          request={"count": 1, "min_seconds": 8, "max_seconds": 30, "aspects": ["9:16"], "platforms": ["youtube"]})
        db.add(project)
        db.flush()
        job_id = queue.enqueue(db, "workflow.video", {"project_id": str(project.id), "source_id": session["source_id"]},
                               project_id=project.id).id
    import clipforge.handlers  # noqa: F401

    # Two workers so the long-running ingest and the processing run concurrently.
    workers = [Worker(["default"], worker_id="live-default"), Worker(["default", "media"], worker_id="live-media")]
    threads = [threading.Thread(target=w.run, daemon=True) for w in workers]
    for t in threads:
        t.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            with session_scope() as db:
                if db.get(Job, job_id).status_label == "Waiting for live source":
                    break
            time.sleep(0.5)
        assert db.get(Job, job_id).status_label == "Waiting for live source"
        wrong = publish_url.replace("passphrase=", "passphrase=WRONG")
        assert _push(wrong, live_env["path"], 0, 3) != 0  # unauthenticated feed refused
        assert _push(publish_url, live_env["path"], 0, 24) == 0  # first connection
        time.sleep(3)  # connection drop
        # Reconnect the way real encoders do: retry until the ingest accepts again.
        for _attempt in range(6):
            if _push(publish_url, live_env["path"], 24, 26) == 0:
                break
            time.sleep(2)
        else:
            raise AssertionError("encoder could not reconnect")
        deadline = time.time() + 600
        while time.time() < deadline:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job.status in ("completed", "failed"):
                    break
            time.sleep(2)
    finally:
        for w in workers:
            w.stop()
        for t in threads:
            t.join(timeout=60)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "completed", job.error
        live_session = db.get(LiveSession, uuid.UUID(session["id"]))
        assert live_session.status == "ended" and live_session.connections == 2
        segments = db.execute(select(LiveSegment).where(LiveSegment.session_id == live_session.id)).scalars().all()
        assert len(segments) >= 10 and sum(len(s.words) for s in segments) > 60  # transcribed while streaming
        assert live_session.stats["candidates"], "no live highlight candidates recorded"
        assert abs(live_session.stats["stream_seconds"] - 50) < 6
        clips = db.execute(select(Clip).where(Clip.project_id == job.project_id)).scalars().all()
        assert clips and all(db.execute(select(Render).where(Render.clip_id == c.id)).scalar_one().quality_report["passed"]
                             for c in clips)


def test_retention_deletes_old_unkept_segments(tmp_path):
    from clipforge.handlers.live_jobs import _enforce_retention

    with session_scope() as db:
        live_session = LiveSession(stream_key_hash=uuid.uuid4().hex, status="live", retention_minutes=1)
        db.add(live_session)
        db.flush()
        for idx in range(30):
            path = tmp_path / f"seg_{idx:06d}.ts"
            path.write_bytes(b"x")
            db.add(LiveSegment(session_id=live_session.id, idx=idx, path=str(path), stream_offset_s=idx * 10.0, duration_s=10.0,
                               status="transcribed", keep=(idx == 3)))
        session_id = live_session.id
    deleted = _enforce_retention(session_id, retention_s=60)
    with session_scope() as db:
        rows = {s.idx: s for s in db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id)).scalars()}
    assert deleted == 22  # 23 segments end before 300-60=240s; the kept highlight (idx 3) survives
    assert rows[3].status == "transcribed" and Path(rows[3].path).exists()
    assert rows[0].status == "deleted" and not Path(rows[0].path).exists()
    assert rows[29].status == "transcribed"
