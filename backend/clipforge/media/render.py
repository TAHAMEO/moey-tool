"""Clip rendering with FFmpeg (Part 12) — one filtergraph pass per output.

Pipeline for one clip and aspect ratio:

1. Edit plan: kept intervals (internal silences removed), punch-in pattern,
   captions style, loudness target, optional licensed music and watermark.
2. Crop path from the vendored AutoClip reframer (face-aware, speaker-aware,
   One-Euro smoothed; never interpolates across scene cuts).
3. Pieces = kept intervals × crop segments. Each piece is trimmed, cropped
   (zoomed on alternating jump cuts), scaled, then concatenated; audio pieces
   get 20 ms fades so joins never click.
4. Words are re-timed onto the edited timeline and burned in with libass.
5. Loudness normalised to the target (-14 LUFS default), H.264/AAC MP4 with
   +faststart, then verified by quality checks.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..vendor.autoclip import captions as vcaptions
from ..vendor.autoclip import ffmpeg as vff
from ..vendor.autoclip.reframe import ReframeConfig, build_crop_path
from ..vendor.autoclip.reframe.croppath import CropPath, CropSegment, centre_crop, segment_crop_filter
from ..vendor.autoclip.transcript import Word
from . import ffmpeg_tools

log = logging.getLogger(__name__)

RATIOS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
PUNCH_IN = 1.12
FPS = 30


@dataclass
class EditPlan:
    clip_start: float
    clip_end: float
    keep: list[tuple[float, float]]
    aspect: str = "9:16"
    caption_style: str = "bold_pop"
    burn_captions: bool = True
    punch_in: bool = True
    loudness_lufs: float = -14.0
    watermark: str | None = None
    watermark_position: str = "top_right"
    #: Brand accent colour for the highlighted caption word (styles that highlight words).
    caption_accent: str | None = None
    music: str | None = None
    music_db: float = -22.0
    fade_s: float = 0.15
    removed_silence_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def output_duration(self) -> float:
        return sum(b - a for a, b in self.keep)

    def to_dict(self) -> dict:
        return asdict(self)


META_RE = re.compile(r"\b(let me (just )?(check|see|find|look|grab|pull up)|where (was|were) (i|we)|hold on|one (second|sec)|"
                     r"give me a (second|sec)|bear with me|as i was saying|what was i saying|sorry,? (where|what))\b", re.IGNORECASE)


def _filler_sentence_cuts(words: list[dict], clip_start: float, clip_end: float) -> list[tuple[float, float]]:
    """Internal sentences that are pure hesitation or meta-talk ("let me just check my notes")."""
    from .textutils import START_FILLERS, sentences, tokens

    inside = [w for w in words if w["start"] >= clip_start - 0.01 and w["end"] <= clip_end + 0.01]
    sents = sentences(inside)
    cuts = []
    for i, sent in enumerate(sents):
        if i == 0 or i == len(sents) - 1:
            continue  # never cut the hook or the ending
        toks = tokens(sent.text)
        filler_ratio = sum(1 for t in toks if t in START_FILLERS or t in ("okay", "ok", "so", "um", "uh")) / max(1, len(toks))
        if META_RE.search(sent.text) or (filler_ratio >= 0.5 and len(toks) <= 8):
            cuts.append((sent.start - 0.05, sent.end + 0.05))
    return cuts


def plan_edits(clip_start: float, clip_end: float, silences: list[tuple[float, float]], words: list[dict], *,
               remove_silences: bool = True, min_silence_s: float = 0.7, keep_pad_s: float = 0.15,
               remove_fillers: bool = True, **options) -> EditPlan:
    """Keep everything except internal pauses longer than ``min_silence_s`` and filler asides."""
    keep = [(clip_start, clip_end)]
    removed = 0.0
    notes: list[str] = []
    filler_cuts = _filler_sentence_cuts(words, clip_start, clip_end) if remove_fillers else []
    if remove_silences or filler_cuts:
        speech = [(w["start"], w["end"]) for w in words if w["end"] > clip_start and w["start"] < clip_end]
        cuts: list[tuple[float, float]] = []
        for a, b in (silences if remove_silences else []):
            a, b = max(a, clip_start + 0.3), min(b, clip_end - 0.3)
            if b - a < min_silence_s:
                continue
            ca, cb = a + keep_pad_s, b - keep_pad_s
            # Never cut into a word (silence detection is energy based).
            if any(ws < cb and we > ca for ws, we in speech):
                continue
            if cb - ca > 0.2:
                cuts.append((ca, cb))
        cuts += filler_cuts
        # Merge overlapping cuts (a filler sentence usually sits between two pauses).
        merged: list[tuple[float, float]] = []
        for ca, cb in sorted(cuts):
            if merged and ca <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], cb))
            else:
                merged.append((ca, cb))
        cuts = merged
        if cuts:
            keep = []
            cursor = clip_start
            for ca, cb in sorted(cuts):
                if ca > cursor + 0.25:
                    keep.append((round(cursor, 3), round(ca, 3)))
                cursor = cb
            if clip_end > cursor + 0.1:
                keep.append((round(cursor, 3), round(clip_end, 3)))
            removed = (clip_end - clip_start) - sum(b - a for a, b in keep)
            notes.append(f"removed {len(cuts)} pause(s)/aside(s), {removed:.1f}s total"
                         + (f" incl. {len(filler_cuts)} filler aside(s)" if filler_cuts else ""))
    return EditPlan(clip_start=clip_start, clip_end=clip_end, keep=keep, removed_silence_s=round(removed, 2), notes=notes,
                    **options)


def remap_words(words: list[dict], keep: list[tuple[float, float]]) -> list[Word]:
    """Map source-time words onto the edited output timeline."""
    out: list[Word] = []
    offset = 0.0
    for a, b in keep:
        for w in words:
            if w["start"] >= a - 0.05 and w["end"] <= b + 0.05:
                out.append(Word(text=w["text"], start=round(offset + max(0.0, w["start"] - a), 3),
                                end=round(offset + min(b, w["end"]) - a, 3), speaker=w.get("speaker")))
        offset += b - a
    return out


def crop_path_for(mezzanine: Path, plan: EditPlan, *, has_video: bool, transcript=None) -> CropPath:
    out_w, out_h = RATIOS[plan.aspect]
    info = ffmpeg_tools.probe(mezzanine)
    duration = plan.clip_end - plan.clip_start
    if not has_video:
        return CropPath(source_width=out_w, source_height=out_h,
                        segments=[CropSegment(0.0, duration, out_w, out_h, fit=False)])
    src_ratio = (info.width or 1) / (info.height or 1)
    if abs(src_ratio - out_w / out_h) < 0.02:
        return centre_crop(info.width, info.height, duration, aspect_w=out_w, aspect_h=out_h)
    aw, ah = (9, 16) if plan.aspect == "9:16" else (1, 1) if plan.aspect == "1:1" else (16, 9)
    try:
        path = build_crop_path(mezzanine, start_s=plan.clip_start, end_s=plan.clip_end, transcript=transcript,
                               config=ReframeConfig(aspect_w=aw, aspect_h=ah, sample_fps=4.0))
    except Exception as exc:  # noqa: BLE001 - reframing must never block a render
        log.warning("Reframing failed (%s); using centre crop", exc)
        plan.notes.append("face-aware reframing unavailable; centre crop used")
        path = centre_crop(info.width, info.height, duration, aspect_w=aw, aspect_h=ah)
    _protect_offscreen_content(mezzanine, path, plan)
    return path


def _protect_offscreen_content(mezzanine: Path, path: CropPath, plan: EditPlan, *, threshold: float = 0.3) -> None:
    """Switch no-face shots to the blurred 'fit' layout when cropping would cut text or graphics.

    Centre-cropping a slide, screen recording or title card to 9:16 slices its
    content. For shots without a tracked face we compare edge density outside
    the crop window with the whole frame; if a meaningful share of the detail
    lies outside, the whole frame is kept over a blurred fill instead.
    """
    import cv2
    import numpy as np

    from ..vendor.autoclip.reframe.croppath import Strategy

    for seg in path.segments:
        if seg.fit or seg.strategy != Strategy.GENERAL or seg.duration_s < 0.5:
            continue
        times = [plan.clip_start + seg.start_s + seg.duration_s * f for f in (0.25, 0.5, 0.75)]
        outside_share = []
        for t in times:
            frames = list(ffmpeg_tools.sample_frames(mezzanine, fps=1, width=640, start_s=t, end_s=t + 0.9))
            if not frames:
                continue
            gray = cv2.cvtColor(frames[0][1], cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 80, 160) > 0
            scale = gray.shape[1] / path.source_width
            x0 = int((seg.keyframes[0].x if seg.keyframes else (path.source_width - seg.width) / 2) * scale)
            x1 = int(x0 + seg.width * scale)
            total = edges.sum()
            if total < 500:  # flat frame: nothing to protect
                continue
            inside = edges[:, max(0, x0):min(edges.shape[1], x1)].sum()
            outside_share.append(1 - inside / total)
        if outside_share and float(np.median(outside_share)) >= threshold:
            seg.fit = True
            plan.notes.append(f"kept full frame for a {seg.duration_s:.1f}s shot with text/graphics outside the crop")


def _pieces(plan: EditPlan, path: CropPath) -> list[tuple[float, float, CropSegment]]:
    pieces = []
    for a, b in plan.keep:
        ra, rb = a - plan.clip_start, b - plan.clip_start
        for seg in path.segments:
            pa, pb = max(ra, seg.start_s), min(rb, seg.end_s)
            if pb - pa > 0.05:
                pieces.append((round(pa, 4), round(pb, 4), seg))
    return pieces


def build_filtergraph(plan: EditPlan, path: CropPath, *, has_video: bool, has_audio: bool, subtitle_name: str | None,
                      watermark_index: int | None, music_index: int | None,
                      still_at: float | None = None) -> tuple[str, str]:
    """Build the filter graph. With ``still_at`` a caption- and watermark-free frame of the
    reframed edit is also exposed as ``[still]`` (used for thumbnails)."""
    out_w, out_h = RATIOS[plan.aspect]
    pieces = _pieces(plan, path)
    parts: list[str] = []
    vlabels, alabels = [], []
    total = sum(pb - pa for pa, pb, _ in pieces)
    for n, (pa, pb, seg) in enumerate(pieces):
        if has_video:
            trim = f"[0:v]trim=start={pa:.4f}:end={pb:.4f},setpts=PTS-STARTPTS"
            if seg.fit:
                parts.append(f"{trim}[s{n}]")
                parts += [
                    f"[s{n}]split=2[fb{n}][ff{n}]",
                    f"[fb{n}]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=24:2[bg{n}]",
                    f"[ff{n}]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fg{n}]",
                    f"[bg{n}][fg{n}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p,fps={FPS}[v{n}]",
                ]
            else:
                piece_seg = CropSegment(start_s=pa, end_s=pb, width=seg.width, height=seg.height,
                                        keyframes=seg.keyframes, strategy=seg.strategy, zoom=seg.zoom)
                chain = [segment_crop_filter(piece_seg, relative=True)]
                if plan.punch_in and len(pieces) > 1 and n % 2 == 1:
                    chain.append(f"crop=iw/{PUNCH_IN}:ih/{PUNCH_IN}:(iw-ow)/2:(ih-oh)/2")
                chain += [f"scale={out_w}:{out_h}:flags=lanczos", "setsar=1", "format=yuv420p", f"fps={FPS}"]
                parts.append(f"{trim},{','.join(chain)}[v{n}]")
            vlabels.append(f"[v{n}]")
        if has_audio:
            dur = pb - pa
            fade = min(0.02, dur / 4)
            parts.append(f"[0:a]atrim=start={pa:.4f}:end={pb:.4f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
                         f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade:.3f}[a{n}]")
            alabels.append(f"[a{n}]")
    if not has_video:
        # Audiogram for audio-only sources: brand background + waveform.
        parts.append(f"color=c=0x101820:s={out_w}x{out_h}:r={FPS}:d={total:.3f}[bgc]")
        parts.append(f"{''.join(alabels)}concat=n={len(alabels)}:v=0:a=1,asplit=2[acat][awave]")
        parts.append(f"[awave]showwaves=s={out_w}x{out_h // 4}:mode=cline:colors=0xFFD400:rate={FPS}[wave]")
        parts.append("[bgc][wave]overlay=0:(H-h)/2,format=yuv420p[vcat]")
    else:
        if len(vlabels) == 1:
            parts.append(f"{vlabels[0]}null[vcat]")
        else:
            parts.append(f"{''.join(vlabels)}concat=n={len(vlabels)}:v=1:a=0[vcat]")
        if has_audio:
            if len(alabels) == 1:
                parts.append(f"{alabels[0]}anull[acat]")
            else:
                parts.append(f"{''.join(alabels)}concat=n={len(alabels)}:v=0:a=1[acat]")
    current = "[vcat]"
    if still_at is not None:
        t0 = max(0.0, min(still_at, total - 0.25))
        parts.append("[vcat]split=2[vmain][vstill]")
        parts.append(f"[vstill]trim=start={t0:.3f}:duration=0.2,setpts=PTS-STARTPTS[still]")
        current = "[vmain]"
    fade_out_at = max(0.0, total - plan.fade_s - 0.05)
    parts.append(f"{current}fade=t=in:st=0:d={plan.fade_s},fade=t=out:st={fade_out_at:.3f}:d={plan.fade_s}[vfade]")
    current = "[vfade]"
    if watermark_index is not None:
        margin = 40
        x = {"top_left": f"{margin}", "bottom_left": f"{margin}"}.get(plan.watermark_position, f"W-w-{margin}")
        y = {"bottom_left": f"H-h-{margin}", "bottom_right": f"H-h-{margin}"}.get(plan.watermark_position, f"{margin}")
        parts.append(f"[{watermark_index}:v]scale={int(out_w * 0.14)}:-1,format=rgba,colorchannelmixer=aa=0.85[wm]")
        parts.append(f"{current}[wm]overlay={x}:{y}[vwm]")
        current = "[vwm]"
    if subtitle_name:
        parts.append(f"{current}ass=filename={subtitle_name}:fontsdir=fonts[vout]")
    else:
        parts.append(f"{current}null[vout]")
    audio_out = ""
    if has_audio:
        speech = "[acat]"
        if music_index is not None:
            parts.append(f"{speech}asplit=2[sp1][sp2]")
            parts.append(f"[{music_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={plan.music_db}dB,"
                         f"atrim=0:{total:.3f}[mus]")
            parts.append("[mus][sp1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
            parts.append("[sp2][duck]amix=inputs=2:normalize=0:duration=first[mix]")
            speech = "[mix]"
        parts.append(f"{speech}loudnorm=I={plan.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000[aout]")
        audio_out = "[aout]"
    return ";".join(parts), audio_out


def caption_style_for(plan: EditPlan) -> vcaptions.CaptionStyle:
    """The preset caption style, with the brand accent colour on the highlighted word."""
    style = vcaptions.get_style(plan.caption_style)
    if plan.caption_accent and style.accent:  # presets without a highlight stay plain
        style = dataclasses.replace(style, accent=plan.caption_accent)
    return style


def render(mezzanine: Path, plan: EditPlan, words: list[dict], out_path: Path, work_dir: Path, *, has_video: bool,
           has_audio: bool, transcript=None, crop_path: CropPath | None = None, on_progress=None, cancelled=None,
           still_at: float | None = None, still_path: Path | None = None) -> dict:
    """Render one clip. Returns the edit decisions actually applied.

    ``still_at``/``still_path`` additionally write one clean frame (no captions or
    watermark) at that output time, from the same FFmpeg pass.
    """
    want_still = still_at is not None and still_path is not None
    out_w, out_h = RATIOS[plan.aspect]
    workspace = work_dir / out_path.stem
    workspace.mkdir(parents=True, exist_ok=True)
    path = crop_path or crop_path_for(mezzanine, plan, has_video=has_video, transcript=transcript)
    out_words = remap_words(words, plan.keep)
    subtitle_name = None
    cwd = None
    if plan.burn_captions and out_words:
        style = caption_style_for(plan)
        ass_path = vcaptions.write_ass(workspace / "captions.ass", out_words, style, width=out_w, height=out_h)
        cwd, subtitle_name, _fonts = vff.relative_filter_workspace(ass_path, vcaptions.FONT_DIR)
    inputs = ["-ss", f"{plan.clip_start:.4f}", "-t", f"{plan.clip_end - plan.clip_start:.4f}", "-i", str(mezzanine)]
    next_index = 1
    watermark_index = music_index = None
    if plan.watermark and Path(plan.watermark).exists():
        inputs += ["-i", plan.watermark]
        watermark_index = next_index
        next_index += 1
    if plan.music and Path(plan.music).exists() and has_audio:
        inputs += ["-stream_loop", "-1", "-i", plan.music]
        music_index = next_index
    graph, audio_label = build_filtergraph(plan, path, has_video=has_video, has_audio=has_audio,
                                           subtitle_name=subtitle_name, watermark_index=watermark_index,
                                           music_index=music_index, still_at=still_at if want_still else None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = [*inputs, "-filter_complex", graph, "-map", "[vout]"]
    if audio_label:
        args += ["-map", audio_label, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-r", str(FPS), "-g", str(FPS * 2), "-movflags", "+faststart", "-t", f"{plan.output_duration + 0.05:.3f}",
             str(out_path)]
    if want_still:
        still_path.parent.mkdir(parents=True, exist_ok=True)
        args += ["-map", "[still]", "-frames:v", "1", "-q:v", "2", "-update", "1", str(still_path)]
    ffmpeg_tools.run(args, duration_s=plan.output_duration, on_progress=on_progress, cancelled=cancelled, cwd=cwd)
    if cwd is not None:
        shutil.rmtree(cwd, ignore_errors=True)
    return {
        "aspect": plan.aspect,
        "output_size": [out_w, out_h],
        "kept_intervals": plan.keep,
        "removed_silence_s": plan.removed_silence_s,
        "punch_in_cuts": max(0, len(_pieces(plan, path)) // 2) if plan.punch_in else 0,
        "crop_strategies": sorted({str(s.strategy) for s in path.segments}),
        "crop_segments": len(path.segments),
        "captions_burned": bool(subtitle_name),
        "still": str(still_path) if want_still and still_path.exists() else None,
        "caption_style": plan.caption_style,
        "music": bool(music_index is not None),
        "watermark": bool(watermark_index is not None),
        "loudness_target": plan.loudness_lufs,
        "notes": plan.notes,
        "words": [{"text": w.text, "start": w.start, "end": w.end} for w in out_words],
    }


def quality_check(path: Path, *, expected_duration: float, aspect: str, loudness_target: float) -> dict:
    """Verify the rendered file meets the platform package requirements."""
    out_w, out_h = RATIOS[aspect]
    report: dict = {"checks": [], "passed": True}

    def check(name: str, ok: bool, detail: str, blocking: bool = True):
        report["checks"].append({"name": name, "ok": ok, "detail": detail, "blocking": blocking})
        if blocking and not ok:
            report["passed"] = False

    try:
        info = ffmpeg_tools.probe(path)
    except ffmpeg_tools.MediaError as exc:
        check("readable", False, str(exc))
        return report
    check("container", path.suffix == ".mp4", "MP4 container")
    check("video_codec", info.video_codec == "h264", f"video codec {info.video_codec}")
    check("audio_codec", info.audio_codec in ("aac", None), f"audio codec {info.audio_codec}")
    check("dimensions", (info.width, info.height) == (out_w, out_h), f"{info.width}x{info.height} (expected {out_w}x{out_h})")
    check("frame_rate", info.fps is not None and 23 <= info.fps <= 60, f"{info.fps:.2f} fps" if info.fps else "unknown fps")
    check("duration", abs(info.duration_s - expected_duration) <= 0.35,
          f"{info.duration_s:.2f}s (expected {expected_duration:.2f}s)")
    check("size_limit", path.stat().st_size < 300 * 1024 * 1024, f"{path.stat().st_size / 1e6:.1f} MB")
    if info.has_audio:
        loud = ffmpeg_tools.measure_loudness(path)
        lufs = loud.get("integrated_lufs")
        check("loudness", lufs is not None and abs(lufs - loudness_target) <= 2.0,
              f"{lufs} LUFS integrated (target {loudness_target})", blocking=False)
        peak = loud.get("true_peak_dbtp")
        check("true_peak", peak is not None and peak <= -0.5, f"true peak {peak} dBTP", blocking=False)
        report["loudness"] = loud
    visual = ffmpeg_tools.detect_black_and_freeze(path, info.duration_s)
    check("black_frames", visual["black_ratio"] < 0.2, f"{visual['black_s']}s black", blocking=False)
    report["visual"] = visual
    report["probe"] = info.to_dict()
    return report
