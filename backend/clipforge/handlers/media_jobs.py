"""Specialist media jobs.

``source.acquire`` runs on the ``default`` queue (it may need OAuth tokens).
Everything that opens untrusted media runs on the ``media`` queue, whose
workers have no master key. Every job reuses earlier results for the same
source (content-addressed) instead of recomputing them.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db.base import session_scope
from ..db.models import (
    Analysis,
    Clip,
    LibraryItem,
    MediaAsset,
    Project,
    Render,
    Source,
    Transcript,
    TranscriptChunk,
)
from ..ingestion import acquire as acq
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..media import analysis, ffmpeg_tools, render, subtitles, thumbnails, transcription
from ..services import app_settings
from ..storage import get_storage

log = logging.getLogger(__name__)


def _local(key: str) -> Path:
    return get_storage().local_path(key)


def _writable(key: str) -> Path:
    storage = get_storage()
    return storage.path_for_write(key) if hasattr(storage, "path_for_write") else storage.cache.path_for_write(key)


def _commit(key: str, path: Path) -> int:
    return get_storage().put_file(path, key)


def _asset(db, *, kind: str, key: str, source_id=None, project_id=None, mime: str, path: Path, **info) -> MediaAsset:
    asset = MediaAsset(kind=kind, storage_key=key, source_id=source_id, project_id=project_id, mime=mime,
                       size_bytes=path.stat().st_size, info=info.pop("info", {}), **info)
    db.add(asset)
    db.flush()
    return asset


# ------------------------------------------------------------------- acquire
@handler("source.acquire", queue="default", needs_secrets=True)
def acquire_job(ctx: JobContext) -> dict:
    ctx.progress(0.1, label="Fetching available source data", stage="acquire")
    with session_scope() as db:
        source = db.get(Source, uuid.UUID(ctx.payload["source_id"]))
        try:
            result = acq.acquire(db, source, ctx.payload["access"])
        except acq.AcquisitionError as exc:
            if exc.retryable:
                raise RetryableError(str(exc)) from exc
            raise PermanentError(str(exc), code="acquire", details={"state": exc.state}) from exc
    return result


# ------------------------------------------------------------------- prepare
@handler("media.prepare", queue="media")
def prepare_job(ctx: JobContext) -> dict:
    source_id = uuid.UUID(ctx.payload["source_id"])
    with session_scope() as db:
        source = db.get(Source, source_id)
        existing = {a.kind: a for a in db.execute(select(MediaAsset).where(
            MediaAsset.source_id == source_id, MediaAsset.kind.in_(("mezzanine", "proxy", "asr_audio")))).scalars()}
        if "mezzanine" in existing and "asr_audio" in existing:
            mez = existing["mezzanine"]
            ctx.event("Reusing prepared media from an earlier run", stage="prepare")
            return {"mezzanine": mez.storage_key, "proxy": existing.get("proxy").storage_key if existing.get("proxy") else None,
                    "asr": existing["asr_audio"].storage_key, "duration": mez.duration_s, "has_video": bool(mez.width),
                    "has_audio": True, "width": mez.width, "height": mez.height, "reused": True}
        original = db.get(MediaAsset, source.media_asset_id)
        original_key = original.storage_key
        original_id = original.id
    ctx.progress(0.05, label="Checking media", stage="prepare")
    src = _local(original_key)
    try:
        info = ffmpeg_tools.probe(src)
        ffmpeg_tools.validate_source(info)
    except ffmpeg_tools.MediaError as exc:
        raise PermanentError(str(exc), code=exc.code, label="Source unavailable") from exc
    if not info.has_audio:
        raise PermanentError("The media has no audio track, so there is nothing to transcribe or clip from speech.",
                             code="no_audio")
    base = f"sources/{source_id}"
    mez_key = f"{base}/mezzanine.{'mp4' if info.has_video else 'm4a'}"
    mez_path = _writable(mez_key)
    ctx.progress(0.1, label="Preparing media", stage="prepare")
    mez_info = ffmpeg_tools.make_mezzanine(src, mez_path, info, cancelled=ctx.cancelled,
                                           on_progress=lambda f: ctx.progress(0.1 + 0.6 * f))
    _commit(mez_key, mez_path)
    proxy_key = None
    if mez_info.has_video:
        proxy_key = f"{base}/proxy.mp4"
        proxy_path = _writable(proxy_key)
        ffmpeg_tools.make_proxy(mez_path, proxy_path, mez_info.duration_s, cancelled=ctx.cancelled)
        _commit(proxy_key, proxy_path)
    asr_key = f"{base}/asr.wav"
    asr_path = _writable(asr_key)
    ffmpeg_tools.extract_asr_audio(mez_path, asr_path, mez_info.duration_s, cancelled=ctx.cancelled)
    _commit(asr_key, asr_path)
    profile = analysis.audio_profile(asr_path)
    with session_scope() as db:
        _asset(db, kind="mezzanine", key=mez_key, source_id=source_id, mime="video/mp4" if mez_info.has_video else "audio/mp4",
               path=mez_path, duration_s=mez_info.duration_s, width=mez_info.width, height=mez_info.height,
               info={"probe": mez_info.to_dict(), "original_probe": info.to_dict()})
        if proxy_key:
            _asset(db, kind="proxy", key=proxy_key, source_id=source_id, mime="video/mp4", path=_local(proxy_key))
        _asset(db, kind="asr_audio", key=asr_key, source_id=source_id, mime="audio/wav", path=asr_path,
               duration_s=mez_info.duration_s)
        db.add(Analysis(source_id=source_id, kind="audio_profile", version="1", data=profile))
        original = db.get(MediaAsset, original_id)
        original.info = {**(original.info or {}), "validated": True, "probe": info.to_dict()}
        original.duration_s = info.duration_s
    ctx.progress(1.0, message=f"Prepared {mez_info.duration_s:.0f}s of media", stage="prepare")
    return {"mezzanine": mez_key, "proxy": proxy_key, "asr": asr_key, "duration": mez_info.duration_s,
            "has_video": mez_info.has_video, "has_audio": True, "width": mez_info.width, "height": mez_info.height,
            "reused": False}


# ---------------------------------------------------------------- transcribe
@handler("media.transcribe", queue="media")
def transcribe_job(ctx: JobContext) -> dict:
    source_id = uuid.UUID(ctx.payload["source_id"])
    language = ctx.payload.get("language") or "en"
    with session_scope() as db:
        done = db.execute(select(Transcript).where(Transcript.source_id == source_id, Transcript.status == "completed",
                                                   Transcript.origin == "asr").order_by(Transcript.created_at.desc())).scalars().first()
        if done is not None:
            ctx.event(f"Reusing saved transcript ({done.engine}, {done.word_count} words)", stage="transcribe")
            return {"transcript_id": str(done.id), "engine": done.engine, "words": done.word_count, "reused": True}
    try:
        engine = transcription.select_engine(language)
    except transcription.TranscriptionUnavailable as exc:
        raise PermanentError(f"No speech-recognition engine is available: {exc}. Install a model (see docs/INSTALL.md).",
                             code="no_asr_engine") from exc
    asr_key = f"sources/{source_id}/asr.wav"
    wav = _local(asr_key)
    with session_scope() as db:
        transcript = db.execute(select(Transcript).where(Transcript.source_id == source_id, Transcript.engine == engine.name,
                                                         Transcript.status == "running")).scalars().first()
        if transcript is None:
            transcript = Transcript(source_id=source_id, origin="asr", engine=engine.name, model=engine.model_label,
                                    language=language, status="running", word_level=True)
            db.add(transcript)
            db.flush()
            for idx, (start, end) in enumerate(transcription.plan_chunks(wav)):
                db.add(TranscriptChunk(transcript_id=transcript.id, idx=idx, start_s=start, end_s=end))
        transcript_id = transcript.id
        chunks = db.execute(select(TranscriptChunk).where(TranscriptChunk.transcript_id == transcript_id)
                            .order_by(TranscriptChunk.idx)).scalars().all()
        plan = [(c.id, c.idx, c.start_s, c.end_s, c.status) for c in chunks]
    total = len(plan)
    ctx.progress(0.0, label="Transcribing", stage="transcribe",
                 message=f"Transcribing with {engine.name} ({total} chunk{'s' if total != 1 else ''})")
    for n, (chunk_id, idx, start, end, status) in enumerate(plan):
        if status == "done":
            continue
        ctx.check()
        try:
            words = engine.transcribe(wav, start, end, language)
        except Exception as exc:
            with session_scope() as db:
                chunk = db.get(TranscriptChunk, chunk_id)
                chunk.status, chunk.attempts, chunk.error = "failed", chunk.attempts + 1, str(exc)[:500]
            raise RetryableError(f"Chunk {idx} failed: {exc}") from exc
        with session_scope() as db:
            chunk = db.get(TranscriptChunk, chunk_id)
            chunk.words = [w.to_dict() for w in words]
            chunk.status = "done"
            chunk.attempts += 1
        ctx.progress((n + 1) / total, message=f"Chunk {idx + 1}/{total} transcribed ({len(words)} words)")
    with session_scope() as db:
        chunks = db.execute(select(TranscriptChunk).where(TranscriptChunk.transcript_id == transcript_id)
                            .order_by(TranscriptChunk.idx)).scalars().all()
        words = [w for c in chunks for w in c.words]
        # Chunks are cut in silences, but drop any duplicate word straddling a boundary.
        deduped: list[dict] = []
        for word in sorted(words, key=lambda w: w["start"]):
            if deduped and word["start"] < deduped[-1]["end"] - 0.05 and word["text"] == deduped[-1]["text"]:
                continue
            deduped.append(word)
        if not engine.punctuated:
            deduped = transcription.restore_punctuation(deduped)
        key = f"transcripts/{source_id}/{transcript_id}.json"
        path = _writable(key)
        path.write_text(json.dumps({"words": deduped, "engine": engine.name, "model": engine.model_label}))
        _commit(key, path)
        transcript = db.get(Transcript, transcript_id)
        transcript.status = "completed"
        transcript.storage_key = key
        transcript.word_count = len(deduped)
        transcript.text_preview = " ".join(w["text"] for w in deduped[:120])
        transcript.avg_confidence = round(sum(w.get("conf", 1) for w in deduped) / len(deduped), 3) if deduped else None
        transcript.duration_s = deduped[-1]["end"] if deduped else 0.0
        db.get(Source, source_id).transcript_status = "available"
    if not deduped:
        raise PermanentError("No speech was recognised in this media.", code="no_speech")
    return {"transcript_id": str(transcript_id), "engine": engine.name, "words": len(deduped), "reused": False}


# ------------------------------------------------------------------- analyze
@handler("media.analyze", queue="media")
def analyze_job(ctx: JobContext) -> dict:
    source_id = uuid.UUID(ctx.payload["source_id"])
    has_video = bool(ctx.payload.get("has_video", True))
    with session_scope() as db:
        done = {a.kind for a in db.execute(select(Analysis).where(Analysis.source_id == source_id)).scalars()}
    steps = [("audio_events", lambda: analysis.audio_events(_local(f"sources/{source_id}/asr.wav")))]
    if has_video:
        steps = [("scenes", lambda: analysis.detect_scenes(_local(f"sources/{source_id}/proxy.mp4"))),
                 ("visual", lambda: analysis.visual_features(_local(f"sources/{source_id}/proxy.mp4"))), *steps]
    summary = {}
    for n, (kind, fn) in enumerate(steps):
        ctx.check()
        if kind in done:
            summary[kind] = "reused"
            continue
        ctx.progress(n / len(steps), label="Analyzing", stage="analyze", message=f"Analyzing {kind.replace('_', ' ')}")
        try:
            data = fn()
        except Exception as exc:  # noqa: BLE001 - one failed analysis must not sink the project
            log.warning("Analysis %s failed: %s", kind, exc)
            data = {"error": str(exc)[:300]}
        with session_scope() as db:
            db.add(Analysis(source_id=source_id, kind=kind, version="1", data=data))
        summary[kind] = len(data) if isinstance(data, list) else "error"
    return summary


# -------------------------------------------------------------------- render
def _source_paths(source_id: uuid.UUID) -> dict:
    with session_scope() as db:
        assets = {a.kind: a for a in db.execute(select(MediaAsset).where(MediaAsset.source_id == source_id)).scalars()}
        mez = assets["mezzanine"]
        return {"mezzanine": _local(mez.storage_key), "has_video": bool(mez.width), "duration": mez.duration_s or 0.0}


def load_words(source_id: uuid.UUID) -> list[dict]:
    with session_scope() as db:
        transcript = db.execute(select(Transcript).where(Transcript.source_id == source_id, Transcript.status == "completed")
                                .order_by(Transcript.word_level.desc(), Transcript.created_at.desc())).scalars().first()
        if transcript is None:
            return []
        key = transcript.storage_key
    return json.loads(_local(key).read_text())["words"]


def load_analysis(source_id: uuid.UUID) -> dict:
    with session_scope() as db:
        return {a.kind: a.data for a in db.execute(select(Analysis).where(Analysis.source_id == source_id)).scalars()}


@handler("media.render", queue="media")
def render_job(ctx: JobContext) -> dict:
    clip_id = uuid.UUID(ctx.payload["clip_id"])
    options = ctx.payload.get("options", {})
    with session_scope() as db:
        clip = db.get(Clip, clip_id)
        project = db.get(Project, clip.project_id)
        source_id = clip.source_id
        project_id = project.id
        start, end, rank, hook = clip.start_s, clip.end_s, clip.rank, clip.hook
        clips_cfg = app_settings.get_section(db, "clips")
        brand = app_settings.get_section(db, "brand")
        aspects = project.request.get("aspects") or clips_cfg.aspects
        done = {r.aspect for r in db.execute(select(Render).where(Render.clip_id == clip_id, Render.status == "done")).scalars()}
        # Owner-licensed assets only (rights explicitly confirmed in the media library).
        library = {}
        if brand.watermark_library_item_id:
            item = db.get(LibraryItem, uuid.UUID(brand.watermark_library_item_id))
            if item is not None and item.rights_confirmed and item.kind == "logo" and item.asset_id:
                library["watermark_path"] = str(_local(db.get(MediaAsset, item.asset_id).storage_key))
        if clips_cfg.use_licensed_music:
            item = db.execute(select(LibraryItem).where(LibraryItem.kind == "music", LibraryItem.rights_confirmed.is_(True))
                              .order_by(LibraryItem.created_at.desc())).scalars().first()
            if item is not None and item.asset_id:
                library["music_path"] = str(_local(db.get(MediaAsset, item.asset_id).storage_key))
        options = {**library, **options}
    paths = _source_paths(source_id)
    words = load_words(source_id)
    analyses = load_analysis(source_id)
    silences = [tuple(s) for s in (analyses.get("audio_profile") or {}).get("silences", [])]
    scene_cuts = [s["start"] for s in (analyses.get("scenes") or []) if isinstance(s, dict) and s.get("start", 0) > 0]
    settings = get_settings()
    work = settings.work_dir / "render" / str(clip_id)
    results = []
    for n, aspect in enumerate(aspects):
        if aspect in done:
            continue
        ctx.check()
        ctx.progress(n / len(aspects), label="Rendering", stage="render", message=f"Rendering clip {rank} ({aspect})")
        plan = render.plan_edits(start, end, silences, words,
                                 remove_silences=options.get("remove_silences", clips_cfg.remove_silences),
                                 min_silence_s=clips_cfg.min_silence_to_cut_s, aspect=aspect,
                                 caption_style=options.get("caption_style") or project.request.get("caption_style") or clips_cfg.caption_style,
                                 burn_captions=options.get("burn_captions", project.request.get("burn_captions", clips_cfg.burn_captions)),
                                 punch_in=clips_cfg.punch_in_zoom, loudness_lufs=clips_cfg.loudness_lufs,
                                 music=options.get("music_path"), music_db=clips_cfg.music_volume_db,
                                 watermark=options.get("watermark_path"), watermark_position=brand.watermark_position,
                                 caption_accent=brand.accent_color)
        slug = f"clip{rank:02d}_{aspect.replace(':', 'x')}"
        base_key = f"projects/{project_id}/clips/{clip_id}"
        video_key = f"{base_key}/{slug}.mp4"
        out_path = _writable(video_key)
        # Thumbnail moment: best frame inside the clip, mapped onto the edited timeline.
        best_src_t = thumbnails.best_frame_time(paths["mezzanine"], start, end, scene_cuts=scene_cuts) if paths["has_video"] else start
        out_t = 0.0
        offset = 0.0
        for a, b in plan.keep:
            if a <= best_src_t <= b:
                out_t = offset + (best_src_t - a)
                break
            offset += b - a
        decisions = render.render(paths["mezzanine"], plan, words, out_path, work, has_video=paths["has_video"],
                                  has_audio=True, cancelled=ctx.cancelled,
                                  still_at=out_t, still_path=work / f"{slug}_frame.jpg")
        qc = render.quality_check(out_path, expected_duration=plan.output_duration, aspect=aspect,
                                  loudness_target=plan.loudness_lufs)
        _commit(video_key, out_path)
        cues = subtitles.build_cues(decisions["words"])
        sub_report = subtitles.validate(cues, expected_language=(project.request.get("languages") or ["en"])[0],
                                        duration=plan.output_duration)
        srt_path, vtt_path = subtitles.write_files(cues, work / "subs", slug)
        srt_key, vtt_key = f"{base_key}/{slug}.srt", f"{base_key}/{slug}.vtt"
        _commit(srt_key, srt_path)
        _commit(vtt_key, vtt_path)
        # Cover: the clean (caption-free) still written by the render pass, so the
        # hook text is never stacked on top of burned-in captions.
        frame = Path(decisions["still"]) if decisions.get("still") else \
            thumbnails.extract_frame(out_path, out_t, work / f"{slug}_frame.jpg")
        size = (1080, 1920) if aspect == "9:16" else (1080, 1080) if aspect == "1:1" else (1280, 720)
        thumb_key = f"{base_key}/{slug}_thumb.jpg"
        thumb_path = thumbnails.compose(frame, _writable(thumb_key), size=size, text=options.get("thumbnail_text") or hook,
                                        accent=brand.accent_color)
        _commit(thumb_key, thumb_path)
        landscape_key = None
        if paths["has_video"] and aspect != "16:9":
            src_frame = thumbnails.extract_frame(paths["mezzanine"], best_src_t, work / f"{slug}_src.jpg")
            landscape_key = f"{base_key}/{slug}_yt_thumb.jpg"
            thumbnails.compose(src_frame, _writable(landscape_key), size=(1280, 720),
                               text=options.get("thumbnail_text") or hook, accent=brand.accent_color)
            _commit(landscape_key, _local(landscape_key))
        with session_scope() as db:
            video_asset = _asset(db, kind="clip", key=video_key, project_id=project_id, mime="video/mp4", path=out_path,
                                 duration_s=plan.output_duration, width=decisions["output_size"][0],
                                 height=decisions["output_size"][1], info={"aspect": aspect})
            srt_asset = _asset(db, kind="subtitle", key=srt_key, project_id=project_id, mime="application/x-subrip",
                               path=srt_path, info={"format": "srt"})
            vtt_asset = _asset(db, kind="subtitle", key=vtt_key, project_id=project_id, mime="text/vtt", path=vtt_path,
                               info={"format": "vtt"})
            thumb_asset = _asset(db, kind="thumbnail", key=thumb_key, project_id=project_id, mime="image/jpeg",
                                 path=thumb_path, info={"size": list(size), "frame_time": round(out_t, 2)})
            if landscape_key:
                _asset(db, kind="thumbnail", key=landscape_key, project_id=project_id, mime="image/jpeg",
                       path=_local(landscape_key), info={"size": [1280, 720], "for": "youtube", "clip_id": str(clip_id), "aspect": aspect})
            render_row = Render(clip_id=clip_id, aspect=aspect, status="done", video_asset_id=video_asset.id,
                                srt_asset_id=srt_asset.id, vtt_asset_id=vtt_asset.id, thumbnail_asset_id=thumb_asset.id,
                                captions_burned=decisions["captions_burned"],
                                edit_decisions={k: v for k, v in decisions.items() if k != "still"},
                                quality_report=qc, subtitle_report=sub_report.to_dict())
            db.add(render_row)
            db.flush()
            results.append({"aspect": aspect, "render_id": str(render_row.id), "qc_passed": qc["passed"],
                            "review_words": len(sub_report.review_words)})
        shutil.rmtree(work / slug, ignore_errors=True)
    with session_scope() as db:
        db.get(Clip, clip_id).status = "rendered"
    shutil.rmtree(work, ignore_errors=True)
    return {"renders": results}
