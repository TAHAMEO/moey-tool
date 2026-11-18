"""Live jobs: authorised SRT ingest and incremental live processing."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select

from ..config import get_settings
from ..db.base import session_scope, utcnow
from ..db.models import Job, LiveSegment, LiveSession, MediaAsset, Project, Source
from ..jobs import queue
from ..jobs.registry import JobContext, PermanentError, handler
from ..media import highlights, live
from ..security.crypto import read_secret
from ..storage import get_storage, sha256_file

log = logging.getLogger(__name__)


def live_dir(session_id: uuid.UUID) -> Path:
    return get_settings().data_dir / "live" / str(session_id)


def _resume_waiting_workflows(db, source_id: uuid.UUID | None) -> int:
    if source_id is None:
        return 0
    resumed = 0
    for job in db.execute(select(Job).join(Project, Project.id == Job.project_id)
                          .where(Job.type == "workflow.video", Job.status == "waiting", Project.source_id == source_id)).scalars():
        if (job.checkpoint or {}).get("waiting", {}).get("type") == "live_wait":
            queue.resume_with(db, job.id, {"live": True}, label="Live source connected")
            resumed += 1
    return resumed


@handler("live.ingest", queue="default", needs_secrets=True)
def live_ingest(ctx: JobContext) -> dict:
    session_id = uuid.UUID(ctx.payload["session_id"])
    settings = get_settings()
    with session_scope() as db:
        session = db.get(LiveSession, session_id)
        if session is None or session.status in ("ended", "stopped"):
            return {"skipped": True}
        passphrase = read_secret(db, session.secret_id)
        port, start_index = session.port, session.next_segment_idx
        stream_seconds = float((session.stats or {}).get("stream_seconds", 0.0))
    state = {"offset": stream_seconds, "last_check": 0.0, "stop": False}

    def on_segment(idx: int, path: Path, duration: float) -> None:
        with session_scope() as db:
            if db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id, LiveSegment.idx == idx)).first():
                return
            db.add(LiveSegment(session_id=session_id, idx=idx, path=str(path), stream_offset_s=round(state["offset"], 3),
                               duration_s=round(duration, 3)))
            state["offset"] += duration
            row = db.get(LiveSession, session_id)
            row.next_segment_idx = idx + 1
            row.stats = {**(row.stats or {}), "stream_seconds": round(state["offset"], 3)}
        ctx.progress(None, message=f"Recorded live segment {idx} ({duration:.1f}s)", stage="live")

    def on_state(new_state: str) -> None:
        with session_scope() as db:
            row = db.get(LiveSession, session_id)
            if row.status in ("stopping", "stopped"):
                return
            row.status = new_state
            if new_state == "live":
                row.connections += 1
                row.started_at = row.started_at or utcnow()
                resumed = _resume_waiting_workflows(db, row.source_id)
                if resumed:
                    log.info("Live session %s connected; resumed %d workflow(s)", session_id, resumed)
        label = {"waiting": "Waiting for live source", "live": "Receiving live stream", "reconnecting": "Reconnecting"}[new_state]
        ctx.progress(None, label=label, stage="live", message=label)

    def should_stop() -> bool:
        if ctx.cancelled():
            return True
        now = time.monotonic()
        if now - state["last_check"] > 2:
            state["last_check"] = now
            with session_scope() as db:
                state["stop"] = db.get(LiveSession, session_id).status in ("stopping", "stopped")
        return state["stop"]

    ingest = live.LiveIngest(port, passphrase, live_dir(session_id), segment_s=settings.live_segment_seconds,
                             on_segment=on_segment, on_state=on_state)
    grace = float(ctx.payload.get("grace_s", settings.live_reconnect_grace_s))
    try:
        next_index = ingest.run(start_index, stop=should_stop, grace_s=grace,
                                max_session_s=settings.live_max_session_hours * 3600)
    except RuntimeError as exc:
        with session_scope() as db:
            db.get(LiveSession, session_id).status = "failed"
        raise PermanentError(str(exc), code="live_listener") from exc
    with session_scope() as db:
        row = db.get(LiveSession, session_id)
        row.next_segment_idx = next_index
        row.status = "stopped" if row.status in ("stopping", "stopped") else "ended"
        row.ended_at = utcnow()
        segments = db.execute(select(func.count()).select_from(LiveSegment).where(LiveSegment.session_id == session_id)).scalar_one()
    return {"_label": "Live stream ended", "segments": segments, "connections": ingest.connections}


@handler("live.process", queue="media")
def live_process(ctx: JobContext) -> dict:
    """Transcribe segments as they arrive, track candidate highlights, enforce retention."""
    session_id = uuid.UUID(ctx.payload["live_session_id"])
    source_id = uuid.UUID(ctx.payload["source_id"])
    work = live_dir(session_id) / "work"
    processed_since_scan = 0
    candidates: list[dict] = []
    while True:
        ctx.check()
        with session_scope() as db:
            session = db.get(LiveSession, session_id)
            status = session.status
            retention_s = session.retention_minutes * 60
            pending = db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id,
                                                           LiveSegment.status == "recorded").order_by(LiveSegment.idx)).scalars().all()
            pending = [(s.id, s.idx, s.path, s.stream_offset_s) for s in pending]
        for seg_id, idx, path, offset in pending:
            ctx.check()
            result = live.process_segment(Path(path), work, offset)
            with session_scope() as db:
                seg = db.get(LiveSegment, seg_id)
                seg.words, seg.energy, seg.status = result["words"], result["energy"], "transcribed"
                db.get(LiveSession, session_id).processed_until_idx = idx
            processed_since_scan += 1
            ctx.progress(None, label="Processing live stream", stage="live",
                         message=f"Segment {idx}: {len(result['words'])} words")
        if processed_since_scan >= 3 or (status in ("ended", "stopped", "failed") and processed_since_scan):
            candidates = _rolling_candidates(session_id)
            processed_since_scan = 0
        _enforce_retention(session_id, retention_s)
        if status in ("ended", "stopped", "failed") and not pending:
            break
        time.sleep(1.0)
    with session_scope() as db:
        kept = db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id, LiveSegment.status != "deleted")
                          .order_by(LiveSegment.idx)).scalars().all()
        paths = [Path(s.path) for s in kept if Path(s.path).exists()]
    if not paths:
        raise PermanentError("The live stream produced no recording to process.", code="live_empty")
    replay = live_dir(session_id) / "replay.mp4"
    live.build_replay(paths, replay)
    sha = sha256_file(replay)
    key = f"sources/{source_id}/live_replay.mp4"
    size = get_storage().put_file(replay, key, move=True)
    with session_scope() as db:
        asset = MediaAsset(kind="source", storage_key=key, mime="video/mp4", size_bytes=size, sha256=sha, source_id=source_id,
                           info={"origin": {"method": "live_ingest", "session_id": str(session_id)}, "segments": len(paths)})
        db.add(asset)
        db.flush()
        source = db.get(Source, source_id)
        source.media_asset_id, source.content_sha256, source.media_status = asset.id, sha, "available"
        session = db.get(LiveSession, session_id)
        session.stats = {**(session.stats or {}), "candidates": candidates, "replay_segments": len(paths)}
    return {"media_asset_id": str(asset.id), "segments": len(paths), "candidates": candidates}


def _rolling_candidates(session_id: uuid.UUID, window_s: float = 600.0) -> list[dict]:
    with session_scope() as db:
        segments = db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id,
                                                        LiveSegment.status == "transcribed").order_by(LiveSegment.idx)).scalars().all()
        if not segments:
            return []
        latest = segments[-1].stream_offset_s + segments[-1].duration_s
        words = [w for s in segments if s.stream_offset_s + s.duration_s >= latest - window_s for w in s.words]
        levels = [s.energy if s.energy is not None else -40.0 for s in segments for _ in range(int(s.duration_s * 2))]
    if len(words) < 20:
        return []
    from ..media.transcription import restore_punctuation

    words = restore_punctuation(sorted(words, key=lambda w: w["start"]))
    chosen, _ = highlights.select(words, highlights.SourceSignals(duration=latest, levels_db=levels, has_video=False),
                                  highlights.SelectionRequest(count=3, min_s=10, max_s=59, llm_rerank=False))
    found = [{"start": round(c.start, 2), "end": round(c.end, 2), "score": c.total, "hook": c.hook[:120]} for c in chosen]
    with session_scope() as db:
        for seg in db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id)).scalars():
            seg_end = seg.stream_offset_s + seg.duration_s
            if any(seg.stream_offset_s < c["end"] and seg_end > c["start"] for c in found):
                seg.keep = True
        session = db.get(LiveSession, session_id)
        session.stats = {**(session.stats or {}), "candidates": found}
    return found


def _enforce_retention(session_id: uuid.UUID, retention_s: float) -> int:
    deleted = 0
    with session_scope() as db:
        segments = db.execute(select(LiveSegment).where(LiveSegment.session_id == session_id).order_by(LiveSegment.idx)).scalars().all()
        if not segments:
            return 0
        latest = segments[-1].stream_offset_s + segments[-1].duration_s
        for seg in segments:
            if seg.status == "transcribed" and not seg.keep and seg.stream_offset_s + seg.duration_s < latest - retention_s:
                Path(seg.path).unlink(missing_ok=True)
                seg.status = "deleted"
                deleted += 1
    return deleted
