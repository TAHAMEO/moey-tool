"""Specialist media jobs.

``source.acquire`` runs on the ``default`` queue (it may need OAuth tokens).
Everything that opens untrusted media — including ``source.download``, which
fetches public videos with yt-dlp — runs on the ``media`` queue, whose workers
have no master key. Every job reuses earlier results for the same source
(content-addressed) instead of recomputing them.
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
from ..storage import asset_present, get_storage

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
            result = acq.acquire(db, source, ctx.payload["access"],
                                 on_progress=lambda f, detail: ctx.progress(0.1 + 0.85 * f, label="Downloading video",
                                                                            detail=detail))
        except acq.AcquisitionError as exc:
            if exc.retryable:
                raise RetryableError(str(exc)) from exc
            raise PermanentError(str(exc), code="acquire", details={"state": exc.state}) from exc
    return result


# ------------------------------------------------------------------ download
@handler("source.download", queue="media")
def download_job(ctx: JobContext) -> dict:
    """A public YouTube/Instagram video, fetched with yt-dlp (see ingestion/public_download.py)."""
    from ..ingestion import public_download
    from ..storage import sha256_file

    source_id = uuid.UUID(ctx.payload["source_id"])
    url = ctx.payload["url"]
    with session_scope() as db:
        source = db.get(Source, source_id)
        if source.media_asset_id and source.media_status == "available" and \
                asset_present(db.get(MediaAsset, source.media_asset_id)):
            ctx.event("Reusing the video downloaded earlier", stage="acquire")
            return {"media_asset_id": str(source.media_asset_id), "method": "public_download", "reused": True}
    settings = get_settings()
    work = settings.work_dir / "download" / str(source_id)
    shutil.rmtree(work, ignore_errors=True)
    ctx.progress(0.02, label="Downloading video", stage="acquire")
    try:
        result = public_download.download(
            url, work, max_bytes=settings.max_download_mb * 1024 * 1024,
            max_seconds=settings.max_source_duration_hours * 3600, cache_dir=settings.data_dir / "cache" / "yt-dlp",
            on_progress=lambda f, _label, detail=None: ctx.progress(0.02 + 0.93 * f, label="Downloading video",
                                                                    detail=detail),
            cancelled=ctx.cancelled)
    except public_download.DownloadError as exc:
        shutil.rmtree(work, ignore_errors=True)
        if exc.retryable:
            raise RetryableError(str(exc)) from exc
        raise PermanentError(str(exc), code="download", label="Source unavailable",
                             details={"state": exc.state, "detail": exc.detail}) from exc
    ctx.progress(0.96, label="Checking the download", stage="acquire")
    sha = sha256_file(result.path)
    with session_scope() as db:
        source = db.get(Source, source_id)
        asset = acq._store_source_media(
            db, source, result.path, sha=sha, mime="video/mp4" if result.path.suffix == ".mp4" else "application/octet-stream",
            origin={"method": "public_download", "tool": "yt-dlp", "url": url, "extractor": result.info.get("extractor_key"),
                    "license": result.info.get("license")})
        source.source_metadata = {**(source.source_metadata or {}), "download": result.info}
        if result.info.get("title") and not (source.source_metadata or {}).get("title"):
            source.source_metadata = {**source.source_metadata, "title": result.info["title"]}
        asset_id = asset.id
    shutil.rmtree(work, ignore_errors=True)
    return {"media_asset_id": str(asset_id), "method": "public_download", "license": result.info.get("license"),
            "title": result.info.get("title"), "duration": result.info.get("duration")}


# ------------------------------------------------------------------- prepare
@handler("media.prepare", queue="media")
def prepare_job(ctx: JobContext) -> dict:
    source_id = uuid.UUID(ctx.payload["source_id"])
    with session_scope() as db:
        source = db.get(Source, source_id)
        existing = {a.kind: a for a in db.execute(select(MediaAsset).where(
            MediaAsset.source_id == source_id, MediaAsset.kind.in_(("mezzanine", "proxy", "asr_audio")))).scalars()
            if asset_present(a)}  # files removed by storage clean-up are made again
        if "mezzanine" in existing and "asr_audio" in existing:
            mez = existing["mezzanine"]
            ctx.event("Reusing prepared media from an earlier run", stage="prepare")
            return {"mezzanine": mez.storage_key, "proxy": existing.get("proxy").storage_key if existing.get("proxy") else None,
                    "asr": existing["asr_audio"].storage_key, "duration": mez.duration_s, "has_video": bool(mez.width),
                    "has_audio": True, "width": mez.width, "height": mez.height, "reused": True}
        original = db.get(MediaAsset, source.media_asset_id)
        if not asset_present(original):
            raise PermanentError("The original video was removed by storage clean-up (Settings → Data keeps source "
                                 "videos for a set number of days). Upload it or paste its link again.",
                                 code="source_removed", label="Source unavailable")
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
        # Made again after a clean-up: the old rows (files gone) are replaced, so exactly one of each remains.
        for stale in db.execute(select(MediaAsset).where(MediaAsset.source_id == source_id,
                                                         MediaAsset.kind.in_(("mezzanine", "proxy", "asr_audio")))).scalars():
            db.delete(stale)
        db.flush()
        _asset(db, kind="mezzanine", key=mez_key, source_id=source_id, mime="video/mp4" if mez_info.has_video else "audio/mp4",
               path=mez_path, duration_s=mez_info.duration_s, width=mez_info.width, height=mez_info.height,
               info={"probe": mez_info.to_dict(), "original_probe": info.to_dict()})
        if proxy_key:
            _asset(db, kind="proxy", key=proxy_key, source_id=source_id, mime="video/mp4", path=_local(proxy_key))
        _asset(db, kind="asr_audio", key=asr_key, source_id=source_id, mime="audio/wav", path=asr_path,
               duration_s=mez_info.duration_s)
        if not db.execute(select(Analysis.id).where(Analysis.source_id == source_id, Analysis.kind == "audio_profile")).first():
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
        engine = _download_speech_model(ctx, language, exc)
    try:
        return _transcribe(ctx, source_id, language, engine)
    finally:
        transcription.release_models()  # free the model's memory for rendering in this worker


def _download_speech_model(ctx: JobContext, language: str, missing: Exception):
    """No speech model yet (setup could not download one): fetch faster-whisper once, with progress."""
    from ..media import models

    settings = get_settings()
    if not settings.auto_download_speech_model:
        raise PermanentError(f"No speech-recognition engine is available: {missing}. Install a model "
                             "(see docs/INSTALL.md).", code="no_asr_engine")
    size = settings.faster_whisper_model
    ctx.progress(0.0, label="Downloading the speech model", stage="transcribe",
                 message=f"No speech model is installed yet: downloading faster-whisper {size} (one time)")

    def report(done: int, total: int) -> None:
        if total:
            ctx.progress(min(0.99, done / total), label="Downloading the speech model",
                         message=f"Speech model: {done >> 20} of {total >> 20} MB (one time)")

    try:
        models.install_faster_whisper(size, on_progress=report)
    except models.ModelUnavailable as exc:
        raise RetryableError(f"No speech model is installed and downloading one failed ({exc}). Check the internet "
                             "connection and press Retry, or run CLIPFORGE setup again.") from exc
    try:
        return transcription.select_engine(language)
    except transcription.TranscriptionUnavailable as exc:
        raise PermanentError(f"No speech-recognition engine is available: {exc}.", code="no_asr_engine") from exc


def _transcribe(ctx: JobContext, source_id: uuid.UUID, language: str, engine) -> dict:
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
        path.write_text(json.dumps({"words": deduped, "engine": engine.name, "model": engine.model_label}),
                        encoding="utf-8")
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
        mez = next((a for a in db.execute(select(MediaAsset).where(MediaAsset.source_id == source_id,
                                                                    MediaAsset.kind == "mezzanine")).scalars()
                    if asset_present(a)), None)
        if mez is None:
            raise PermanentError("This clip's source video is no longer stored (storage clean-up removes source videos "
                                 "after the number of days set in Settings → Data). Process the video again to "
                                 "re-render it.", code="source_removed", label="Source removed")
        return {"mezzanine": _local(mez.storage_key), "has_video": bool(mez.width), "duration": mez.duration_s or 0.0}


def load_words(source_id: uuid.UUID) -> list[dict]:
    with session_scope() as db:
        transcript = db.execute(select(Transcript).where(Transcript.source_id == source_id, Transcript.status == "completed")
                                .order_by(Transcript.word_level.desc(), Transcript.created_at.desc())).scalars().first()
        if transcript is None:
            return []
        key = transcript.storage_key
    return json.loads(_local(key).read_text(encoding="utf-8"))["words"]


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
        shorts_cfg = app_settings.get_section(db, "shorts")
        platforms = project.request.get("platforms") or app_settings.get_section(db, "publishing").platforms
        ab_arm = None
        if _ab_applies(shorts_cfg):
            from ..shorts import abtest

            ab_arm = abtest.assign(db, clip_id, shorts_cfg.ab_test, shorts_cfg.ab_min_per_arm)
        learned_band = None
        if shorts_cfg.adaptive_length and shorts_cfg.retention_loop:
            from ..analytics import retention

            learned_band = retention.best_length_band(db, shorts_cfg)
        brand = app_settings.get_section(db, "brand")
        aspects = project.request.get("aspects") or clips_cfg.aspects
        scores = dict(clip.scores or {})
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
    shorts_plan = shorts_stage(words, analyses, start, end, has_video=paths["has_video"], scores=scores, cfg=shorts_cfg,
                               platforms=list(platforms), ab_arm=ab_arm, learned_band=learned_band)
    start, end = shorts_plan["start"], shorts_plan["end"]
    results = []
    for n, aspect in enumerate(aspects):
        if aspect in done:
            continue
        ctx.check()
        ctx.progress(n / len(aspects), label="Rendering", stage="render", message=f"Rendering clip {rank} ({aspect})")
        plan = render.plan_edits(start, end, silences, words,
                                 remove_silences=options.get("remove_silences", clips_cfg.remove_silences),
                                 min_silence_s=clips_cfg.min_silence_to_cut_s, aspect=aspect,
                                 caption_style=options.get("caption_style") or ("kinetic" if shorts_cfg.kinetic_captions else None)
                                 or project.request.get("caption_style") or clips_cfg.caption_style,
                                 burn_captions=options.get("burn_captions", project.request.get("burn_captions", clips_cfg.burn_captions)),
                                 punch_in=clips_cfg.punch_in_zoom, loudness_lufs=clips_cfg.loudness_lufs,
                                 music=options.get("music_path"), music_db=clips_cfg.music_volume_db,
                                 watermark=options.get("watermark_path"), watermark_position=brand.watermark_position,
                                 caption_accent=brand.accent_color, **shorts_plan["plan_options"])
        if shorts_plan.get("reorder"):
            plan.keep = reorder_keep(plan.keep, shorts_plan["reorder"])
        if shorts_cfg.rehook:
            visual = analyses.get("visual") if isinstance(analyses.get("visual"), list) else []
            shorts_plan["record"]["retention_plan"] = apply_rehook(plan, words, shorts_cfg, visual=visual, seed=str(clip_id))
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
        if shorts_cfg.loop_seam and aspect == "9:16":
            finish_loop(out_path, plan.output_duration, shorts_plan, has_audio=True)
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
        if shorts_cfg.seo_transcript and "seo" not in shorts_plan["record"]:
            shorts_plan["record"]["seo"] = export_transcript(decisions["words"], f"{base_key}/clip{rank:02d}_transcript.txt",
                                                             project_id=project_id)
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
        repoint_packages(db, clip_id)
        clip = db.get(Clip, clip_id)
        clip.status = "rendered"
        if shorts_plan["record"]:
            record = shorts_plan["record"]
            clip.shorts = {**(clip.shorts or {}), **record}
            if "hook" in record:
                clip.hook_score = record["hook"]["after"]["score"]
            if "loop" in record:
                clip.loop_score = record["loop"]["assessment"]["score"]
            if (start, end) != (clip.start_s, clip.end_s):
                clip.shorts["original_bounds"] = clip.shorts.get("original_bounds") or [clip.start_s, clip.end_s]
                clip.start_s, clip.end_s = start, end
                spoken = " ".join(w["text"] for w in words if w["end"] > start + 1e-6 and w["start"] < end - 1e-6)
                clip.transcript_text = spoken or clip.transcript_text
    shutil.rmtree(work, ignore_errors=True)
    return {"renders": results, "hook": (shorts_plan["record"].get("hook") or {}).get("status")}


def repoint_packages(db, clip_id: uuid.UUID) -> int:
    """After a clip is made again (Adjust → Re-render, or a change asked for in Brain Chat), its
    not-yet-published packages use the new video, subtitles and covers — not the replaced render,
    which publishing would refuse. Returns how many packages changed."""
    from ..db.models import OutputPackage
    from ..publishing import schedule

    renders = {r.aspect: r for r in db.execute(select(Render).where(Render.clip_id == clip_id, Render.status == "done")
                                               .order_by(Render.created_at)).scalars()}  # the newest per aspect
    vertical = renders.get("9:16") or next(iter(renders.values()), None)
    if vertical is None:
        return 0
    landscape = next((a for a in db.execute(select(MediaAsset).where(
        MediaAsset.kind == "thumbnail", MediaAsset.info["clip_id"].astext == str(clip_id))
        .order_by(MediaAsset.created_at.desc())).scalars() if asset_present(a)), None)
    changed = 0
    for package in db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip_id)).scalars():
        if package.render_id == vertical.id or package.status == "published":
            continue
        if schedule.remote_publication(db, package.id) is not None:
            continue  # already uploaded to YouTube, waiting for its time
        package.render_id = vertical.id
        if package.platform == "youtube":
            package.subtitle_asset_id = vertical.srt_asset_id
            package.thumbnail_asset_id = landscape.id if landscape else vertical.thumbnail_asset_id
        else:
            package.cover_asset_id = vertical.thumbnail_asset_id
        changed += 1
    return changed


def _ab_applies(cfg) -> bool:
    """An A/B test only runs when the thing it varies is made at all."""
    if cfg.ab_test == "title":
        return cfg.packaging_variants
    if cfg.ab_test == "first_frame":
        return cfg.packaging_variants and cfg.text_hook
    if cfg.ab_test == "hook":
        return cfg.text_hook and (cfg.packaging_variants or (cfg.hook_scorer and cfg.hook_reconstruct))
    return False


def shorts_stage(words: list[dict], analyses: dict, start: float, end: float, *, has_video: bool, scores: dict,
                 cfg, platforms: list[str] | None = None, ab_arm: dict | None = None,
                 learned_band: str | None = None) -> dict:
    """First-3-seconds hook and loop-seam decisions for one clip (source seconds), shared by every
    aspect ratio. With all Shorts switches off it changes nothing. ``ab_arm`` is this clip's A/B
    test arm ({dimension, arm}); ``learned_band`` the length band your retention numbers favour."""
    from ..shorts import hook as shook
    from ..shorts import loop as sloop

    out: dict = {"start": start, "end": end, "plan_options": {}, "record": {}, "reorder": None}
    if cfg.hook_scorer:
        visual = analyses.get("visual") if isinstance(analyses.get("visual"), list) else []
        scenes = analyses.get("scenes") if isinstance(analyses.get("scenes"), list) else []
        hcfg = shook.HookConfig(threshold=cfg.hook_threshold, max_first_line_s=cfg.max_first_line_s,
                                runway_search_s=cfg.runway_search_s, min_duration_s=cfg.min_s,
                                text_seconds=cfg.text_hook_seconds, tension_first=cfg.tension_first)
        result = shook.reconstruct(words, start, end, visual=visual, scenes=scenes, has_video=has_video, cfg=hcfg,
                                   rebuild=cfg.hook_reconstruct)
        alternate = None
        if result.status == "held" and cfg.alternate_hooks:
            # P3: other openings that score better (start at a stronger line later, or a setup line earlier).
            options = shook.alternates(words, start, end, visual=visual, scenes=scenes, has_video=has_video, cfg=hcfg)
            if options and cfg.alternate_hooks_apply and options[0]["score"] >= cfg.hook_threshold:
                retry = shook.reconstruct(words, options[0]["start"], end, visual=visual, scenes=scenes,
                                          has_video=has_video, cfg=hcfg, rebuild=cfg.hook_reconstruct)
                if retry.status != "held":
                    alternate = options[0]
                    retry.changes.insert(0, f"stronger opening used: {alternate['change']}")
                    retry.before, retry.status = result.before, "improved"
                    result = retry
                    if not cfg.hook_reconstruct:
                        out["start"] = alternate["start"]
            out["record"]["alternate_hooks"] = options
        out["record"]["hook"] = result.to_dict()
        if alternate:
            out["record"]["hook"]["alternate_used"] = alternate
        out["plan_options"]["fade_in"] = False  # frame one is the picture, not black
        if cfg.hook_reconstruct:
            out["start"] = result.keep[0][0] if len(result.keep) == 1 else result.start
            if len(result.keep) > 1:
                out["reorder"] = result.keep
            if cfg.text_hook and result.text_hook:
                out["plan_options"].update(hook_text=result.text_hook, hook_text_s=cfg.text_hook_seconds,
                                           hook_text_y=shook.text_position(visual, out["start"]))
    prefer_max_s, max_s = cfg.prefer_max_s, cfg.max_s
    if cfg.adaptive_length:
        from ..shorts import length as slength

        text = " ".join(w["text"] for w in words if w["end"] > out["start"] + 1e-6 and w["start"] < out["end"] - 1e-6)
        lplan = slength.target(text, platforms or ["youtube"], learned_band=learned_band, floor_s=cfg.min_s,
                               ceiling_s=cfg.max_s)
        prefer_max_s, max_s = lplan.prefer_max_s, lplan.max_s
        out["record"]["length_plan"] = lplan.to_dict()
    if cfg.loop_seam or cfg.adaptive_length:
        lcfg = sloop.LoopConfig(threshold=cfg.loop_threshold, search_s=cfg.loop_search_s, min_duration_s=cfg.min_s,
                                prefer_max_s=prefer_max_s, max_duration_s=max_s)
        pacing = (float(scores.get("emotion", 0.5)) + float(scores.get("clarity", 0.5))) / 2
        plan = sloop.choose_end(words, out["start"], out["end"], pacing=pacing, cfg=lcfg)
        out["end"] = plan.end
        if not cfg.loop_seam:  # adaptive length only: choose the end, no blend
            plan.bridge_s = 0.0
            plan.changes = [c for c in plan.changes if "blend" not in c]
        out["record"]["loop"] = plan.to_dict()
        if cfg.loop_seam:
            out["plan_options"]["fade_out"] = False  # a loop must not end in black
    span_text = " ".join(w["text"] for w in words if w["end"] > out["start"] + 1e-6 and w["start"] < out["end"] - 1e-6)
    if cfg.kinetic_captions and span_text:
        from ..shorts import captions as scaptions

        out["plan_options"]["caption_power"] = scaptions.power_scores(span_text, idf=scaptions.source_idf(words))
    if cfg.packaging_variants and span_text:
        from ..shorts import packaging as spack

        frames = spack.first_frame_texts(span_text, spack.PackagingConfig(first_frame_words=cfg.first_frame_words))
        if ab_arm and ab_arm.get("dimension") == "first_frame" and ab_arm.get("arm") == "B" and len(frames) > 1:
            frames = [frames[1], frames[0], *frames[2:]]  # A/B test: this clip carries the second option
        out["record"]["first_frame_texts"] = frames
        if cfg.text_hook and frames:
            # The first frame is the thumbnail on Shorts: 1-2 big words, made from the clip's own words.
            visual = analyses.get("visual") if isinstance(analyses.get("visual"), list) else []
            out["plan_options"].update(hook_text=frames[0], hook_text_s=cfg.text_hook_seconds,
                                       hook_text_y=shook.text_position(visual, out["start"]), fade_in=False)
            out["record"]["first_frame_text"] = frames[0]
            hook = out["record"].get("hook")
            if hook is not None:
                scenes = analyses.get("scenes") if isinstance(analyses.get("scenes"), list) else []
                after = shook.score_opening(words, out["start"], out["end"], visual=visual, scenes=scenes,
                                            has_video=has_video, text_hook=frames[0],
                                            cfg=shook.HookConfig(threshold=cfg.hook_threshold,
                                                                 max_first_line_s=cfg.max_first_line_s))
                hook.update(text_hook=frames[0], after=after.to_dict())
    if ab_arm and ab_arm.get("dimension") == "hook" and ab_arm.get("arm") == "B" and out["plan_options"].get("hook_text"):
        # A/B test of the hook style: this clip opens without on-screen text (spoken opening only).
        out["record"]["text_hook_removed_for_test"] = out["plan_options"].pop("hook_text")
        out["record"].pop("first_frame_text", None)
    if ab_arm:
        out["record"]["ab"] = ab_arm
    return out


def apply_rehook(plan, words: list[dict], cfg, *, visual: list, seed: str) -> dict:
    """Add the re-hook (punch-in zoom and a text line before the exit point) to an edit plan;
    returns the retention plan that is stored on the clip."""
    from ..shorts import hook as shook
    from ..shorts import retention as sret

    out_words = [{"text": w.text, "start": w.start, "end": w.end} for w in render.remap_words(words, plan.keep)]
    rcfg = sret.RehookConfig(window_s=(cfg.rehook_from_s, cfg.rehook_to_s), min_clip_s=cfg.rehook_min_clip_s,
                             zoom=cfg.rehook_zoom, text=cfg.rehook_text, framing=cfg.rehook_framing)
    rplan = sret.plan_rehook(out_words, plan.output_duration, cfg=rcfg, seed=seed)
    if rplan.zoom:
        plan.zoom_windows.append(list(rplan.zoom))
    if rplan.text:
        y = shook.text_position(visual, sret.source_time(plan.keep, rplan.text_start or 0.0))
        plan.overlays.append({"text": rplan.text, "start": rplan.text_start, "seconds": rplan.text_seconds, "y": y})
    return rplan.to_dict()


def export_transcript(words: list[dict], key: str, *, project_id) -> dict:
    """The clip's full spoken transcript as plain text (one sentence per line), kept with the clip
    for search, titles and descriptions."""
    from ..media import textutils as tu
    from ..shorts import packaging as spack

    lines = [s.text.strip() for s in tu.sentences(words)]
    text = "\n".join(lines) + "\n"
    path = _writable(key)
    path.write_text(text, encoding="utf-8")
    _commit(key, path)
    with session_scope() as db:
        asset = _asset(db, kind="transcript", key=key, project_id=project_id, mime="text/plain", path=path,
                       info={"format": "txt", "words": len(words)})
        asset_id = str(asset.id)
    joined = " ".join(lines)
    return {"transcript_asset_id": asset_id, "transcript_key": key, "words": len(words),
            "keyword": spack.focus_keyword(joined), "keywords": tu.keywords(spack.content_text(joined), top_k=8)}


def reorder_keep(keep: list[tuple[float, float]], outline: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Put the kept intervals in the order of ``outline`` (the hook's "open on the question"
    order), cutting intervals at the outline's boundaries."""
    ordered: list[tuple[float, float]] = []
    for oa, ob in outline:
        for a, b in keep:
            pa, pb = max(a, oa), min(b, ob)
            if pb - pa > 0.05:
                ordered.append((round(pa, 3), round(pb, 3)))
    return ordered or keep


def finish_loop(video: Path, duration_s: float, shorts_plan: dict, *, has_audio: bool) -> None:
    """After rendering: measure how alike the last and first frames are, and blend them when the
    cut doesn't loop on its own."""
    from ..shorts import loop as sloop

    record = shorts_plan["record"].get("loop")
    if not record:
        return
    similarity = sloop.frame_similarity(video)
    record["visual_similarity"] = similarity
    if record.get("bridge_s"):
        bridged = video.with_name(video.stem + "_loop.mp4")
        try:
            sloop.bridge(video, bridged, record["bridge_s"], duration_s, has_audio=has_audio)
            bridged.replace(video)
            record["bridged"] = True
        except Exception as exc:  # noqa: BLE001 - keep the un-bridged render, say why
            record["bridged"] = False
            record.setdefault("changes", []).append(f"bridge failed: {exc}"[:200])
