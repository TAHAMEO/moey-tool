"""End-to-end video pipeline with REAL media, REAL local ASR (Vosk), REAL FFmpeg.

Chat link -> Brain -> LangGraph workflow -> resolver -> SSRF-safe download ->
mezzanine -> chunked transcription ∥ analysis -> highlight selection ->
face-aware rendering with burned captions -> QC -> subtitles -> thumbnails ->
platform packages. Requires the Vosk model in var/models (see docs/INSTALL.md).
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from clipforge.db.base import session_scope
from clipforge.db.models import (
    Clip,
    Job,
    MediaAsset,
    OutputPackage,
    Project,
    Render,
    Source,
    Transcript,
    TranscriptChunk,
)
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker
from clipforge.media import transcription
from clipforge.storage import get_storage

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _vosk_available() -> bool:
    return transcription.ENGINES["vosk"].availability()[0]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not _vosk_available():
        pytest.skip("BLOCKED: no local ASR model installed (models/vosk)")
    directory = tmp_path_factory.mktemp("media")
    import sys

    sys.path.insert(0, str(FIXTURES))
    from make_test_media import build

    info = build(directory / "talk.mp4")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    info["url"] = f"http://127.0.0.1:{httpd.server_address[1]}/talk.mp4"
    yield info
    httpd.shutdown()


def _run_until_done(job_id, timeout=900):
    import clipforge.handlers  # noqa: F401

    worker = Worker(["default", "media"], worker_id=f"e2e-{uuid.uuid4().hex[:4]}")
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job.status in ("completed", "failed", "cancelled") or (job.status == "waiting" and
                                                                         (job.checkpoint or {}).get("waiting", {}).get("type") == "approval"):
                    return
            time.sleep(1)
        raise AssertionError("pipeline did not finish in time")
    finally:
        worker.stop()
        thread.join(timeout=60)


def _wer(ref: str, hyp: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0], d[0, :] = range(len(r) + 1), range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


def test_link_to_rendered_clips(owner_client, media):
    conv = owner_client.post("/api/chat/conversations", json={}).json()["id"]
    reply = owner_client.post(f"/api/chat/conversations/{conv}/messages",
                              json={"text": f"Make 2 Shorts and Reels under 30 seconds from this video {media['url']}"}).json()
    job_id = uuid.UUID(reply["meta"]["cards"][0]["job_id"])
    _run_until_done(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "completed", job.error
        project = db.get(Project, job.project_id)
        assert project.status == "ready" and project.analysis_level == "full_media" and project.title == "Talk"
        source = db.get(Source, project.source_id)
        assert source.access_status == "FULL_MEDIA_ACCESS" and source.access_detail["method"] == "direct_url"
        transcript = db.execute(select(Transcript).where(Transcript.source_id == source.id)).scalar_one()
        assert transcript.engine == "vosk" and transcript.word_count > 150 and transcript.status == "completed"
        clips = db.execute(select(Clip).where(Clip.project_id == project.id).order_by(Clip.rank)).scalars().all()
        assert len(clips) == 2
        script = media["script"]
        for clip in clips:
            assert clip.status == "rendered" and clip.end_s - clip.start_s <= 31
            assert set(clip.scores) == {"hook", "clarity", "emotion", "novelty", "information", "completeness",
                                        "visual", "audio", "relevance", "platform"}
            render = db.execute(select(Render).where(Render.clip_id == clip.id)).scalar_one()
            assert render.aspect == "9:16" and render.quality_report["passed"], render.quality_report
            assert render.subtitle_report["passed"]
            video = get_storage().local_path(db.get(MediaAsset, render.video_asset_id).storage_key)
            probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(video)],
                                              check=False, capture_output=True, text=True).stdout)
            codecs = {s["codec_type"]: (s["codec_name"], s.get("width"), s.get("height")) for s in probe["streams"]}
            assert codecs["video"] == ("h264", 1080, 1920) and codecs["audio"][0] == "aac"
            srt = get_storage().local_path(db.get(MediaAsset, render.srt_asset_id).storage_key).read_text()
            vtt = get_storage().local_path(db.get(MediaAsset, render.vtt_asset_id).storage_key).read_text()
            assert "-->" in srt and vtt.startswith("WEBVTT")
            # Caption accuracy against the known script for the clip's time range.
            reference = " ".join(s["text"] for s in script if s["end"] > clip.start_s + 0.3 and s["start"] < clip.end_s - 0.3
                                 and not re.match(r"^(Um,|Let me just)", s["text"]))
            hypothesis = " ".join(w["text"] for w in render.edit_decisions["words"])
            assert _wer(reference, hypothesis) < 0.25, (reference, hypothesis)
            packages = {p.platform: p for p in db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip.id)).scalars()}
            assert set(packages) == {"youtube", "instagram"}
            assert packages["youtube"].title and "#shorts" in packages["youtube"].title.lower()
            assert packages["youtube"].subtitle_asset_id == render.srt_asset_id
            assert packages["instagram"].caption and packages["instagram"].hashtags
        top = clips[0]
        assert "biggest mistake" in top.transcript_text.lower() or "compete on price" in top.transcript_text.lower()


def test_duplicate_link_reuses_expensive_work(owner_client, media):
    conv = owner_client.post("/api/chat/conversations", json={}).json()["id"]
    first = owner_client.post(f"/api/chat/conversations/{conv}/messages", json={"text": f"one short from {media['url']}"}).json()
    _run_until_done(uuid.UUID(first["meta"]["cards"][0]["job_id"]))
    second = owner_client.post(f"/api/chat/conversations/{conv}/messages", json={"text": f"one short from {media['url']}"}).json()
    job_id = uuid.UUID(second["meta"]["cards"][0]["job_id"])
    started = time.time()
    _run_until_done(job_id)
    with session_scope() as db:
        children = {j.type: j for j in db.execute(select(Job).where(Job.parent_id == job_id)).scalars()}
        assert children["media.prepare"].result["reused"] is True
        assert children["media.transcribe"].result["reused"] is True
        assert children["media.analyze"].result == {"scenes": "reused", "visual": "reused", "audio_events": "reused"}
        assert db.get(Job, job_id).status == "completed"
    assert time.time() - started < 120


def test_transcription_resumes_failed_chunk(monkeypatch, media, tmp_path):
    from clipforge.media import ffmpeg_tools

    wav = tmp_path / "asr.wav"
    ffmpeg_tools.extract_asr_audio(Path(media["path"]), wav, media["duration"])
    with session_scope() as db:
        source = Source(platform="upload", source_type="file", external_id=uuid.uuid4().hex)
        db.add(source)
        db.flush()
        source_id = source.id
    get_storage().put_file(wav, f"sources/{source_id}/asr.wav")
    monkeypatch.setattr(transcription, "CHUNK_S", 25.0)
    monkeypatch.setattr(transcription, "SEARCH_S", 5.0)
    engine = transcription.ENGINES["vosk"]
    calls: list[float] = []
    real = engine.transcribe
    state = {"fail": True}

    def flaky(wav_path, start, end, language):
        calls.append(start)
        if start > 20 and state["fail"]:
            state["fail"] = False
            raise RuntimeError("simulated worker crash mid-chunk")
        return real(wav_path, start, end, language)

    monkeypatch.setattr(engine, "transcribe", flaky)
    with session_scope() as db:
        job_id = queue.enqueue(db, "media.transcribe", {"source_id": str(source_id)}, queue="media").id
    import clipforge.handlers  # noqa: F401

    Worker(["media"], worker_id="chunk-test").run(max_jobs=1)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "queued" and job.status_label == "Retrying"  # transient failure -> retry
        chunks = db.execute(select(TranscriptChunk).order_by(TranscriptChunk.idx)).scalars().all()
        assert [c.status for c in chunks][:2] == ["done", "failed"] and len(chunks) >= 3
        job.run_after = job.created_at
    first_pass = list(calls)
    Worker(["media"], worker_id="chunk-test-2").run(max_jobs=1)
    with session_scope() as db:
        assert db.get(Job, job_id).status == "completed"
        assert all(c.status == "done" for c in db.execute(select(TranscriptChunk)).scalars())
    resumed = calls[len(first_pass):]
    assert 0.0 not in resumed  # the finished first chunk was not transcribed again
