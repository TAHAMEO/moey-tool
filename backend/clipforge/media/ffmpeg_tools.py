"""FFmpeg/ffprobe operations used by the pipeline.

Untrusted inputs are only ever opened with protocol/demuxer allow-lists and
are immediately normalised into a *mezzanine* produced by our own FFmpeg pass
(H.264/AAC, constant frame rate, ≤1080p). Every later stage — OpenCV,
MediaPipe, PySceneDetect, rendering — reads only files we generated.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..vendor.autoclip import ffmpeg as vff

log = logging.getLogger(__name__)

SAFE_INPUT = list(vff.SAFE_INPUT_FLAGS)
ASR_RATE = 16_000


class MediaError(RuntimeError):
    def __init__(self, message: str, *, code: str = "media_error"):
        super().__init__(message)
        self.code = code


@dataclass
class ProbeResult:
    duration_s: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    rotation: int
    format_name: str
    bit_rate: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def probe(path: Path) -> ProbeResult:
    command = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", *SAFE_INPUT, str(path)]
    proc = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise MediaError("The file is not a readable video or audio file.", code="unreadable")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or (video or audio or {}).get("duration") or 0.0)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") not in ("0/0", ""):
        num, _, den = video["avg_frame_rate"].partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = None
    rotation = 0
    if video:
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(side["rotation"])
        rotation = int((video.get("tags") or {}).get("rotate", rotation))
    return ProbeResult(
        duration_s=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=fps,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        rotation=rotation,
        format_name=fmt.get("format_name", ""),
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate", "").isdigit() else None,
    )


def run(args: list[str], *, duration_s: float | None = None, on_progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None, cwd: Path | None = None) -> None:
    threads = get_settings().ffmpeg_threads
    if threads:
        args = ["-threads", str(threads), *args]
    try:
        vff.run(args, total_duration_s=duration_s, on_progress=on_progress, cancelled=cancelled, cwd=cwd)
    except vff.Cancelled:
        raise
    except vff.FFmpegError as exc:
        raise MediaError(f"FFmpeg failed: {str(exc)[:1500]}", code="ffmpeg") from exc


def validate_source(info: ProbeResult) -> None:
    settings = get_settings()
    if not (info.has_video or info.has_audio):
        raise MediaError("The file has no video or audio track.", code="no_streams")
    if info.duration_s <= 0.5:
        raise MediaError("The media is too short to process.", code="too_short")
    if info.duration_s > settings.max_source_duration_hours * 3600:
        raise MediaError(f"The media is longer than the {settings.max_source_duration_hours:g}-hour limit.", code="too_long")
    if info.width and info.height and info.width * info.height > 7680 * 4320:
        raise MediaError("Video resolution is above 8K and not supported.", code="too_large")


def make_mezzanine(src: Path, dest: Path, info: ProbeResult, *, on_progress=None, cancelled=None) -> ProbeResult:
    """Normalise untrusted media into a safe, edit-friendly MP4 (or M4A)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if info.has_video:
        fps = min(30.0, info.fps or 30.0) if (info.fps or 0) > 1 else 30.0
        vf = f"scale='if(gt(ih,1080),-2,iw)':'min(1080,ih)':flags=lanczos,fps={fps:.3f},format=yuv420p,setsar=1"
        args = [*SAFE_INPUT, "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(round(fps * 2)),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                "-map_metadata", "-1", "-sn", "-dn", str(dest)]
    else:
        args = [*SAFE_INPUT, "-i", str(src), "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-ac", "2", "-map_metadata", "-1", str(dest)]
    run(args, duration_s=info.duration_s, on_progress=on_progress, cancelled=cancelled)
    return probe(dest)


def make_proxy(mezzanine: Path, dest: Path, duration_s: float, *, cancelled=None) -> None:
    """Small 10 fps proxy for scene/visual analysis (fast to decode)."""
    run(["-i", str(mezzanine), "-an", "-vf", "scale=640:-2:flags=bilinear,fps=10", "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "28", "-g", "20", str(dest)], duration_s=duration_s, cancelled=cancelled)


def extract_asr_audio(src: Path, dest: Path, duration_s: float, *, cancelled=None) -> None:
    run(["-i", str(src), "-vn", "-ac", "1", "-ar", str(ASR_RATE), "-c:a", "pcm_s16le", str(dest)],
        duration_s=duration_s, cancelled=cancelled)


def read_wav(path: Path, start_s: float = 0.0, end_s: float | None = None) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        wav.setpos(min(wav.getnframes(), int(start_s * rate)))
        frames = wav.getnframes() - wav.tell() if end_s is None else int((end_s - start_s) * rate)
        raw = wav.readframes(max(0, frames))
    return np.frombuffer(raw, dtype=np.int16)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def loudness_curve(wav_path: Path, window_s: float = 0.5) -> list[float]:
    """RMS level in dBFS per window, computed in streaming blocks."""
    levels: list[float] = []
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        hop = int(rate * window_s)
        while True:
            raw = wav.readframes(hop)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
            levels.append(round(20 * math.log10(max(rms, 1e-6)), 2))
    return levels


def silences_from_curve(levels: list[float], window_s: float = 0.5, *, min_duration_s: float = 0.5) -> list[tuple[float, float]]:
    """Silent spans: windows well below the speech level (adaptive threshold)."""
    if not levels:
        return []
    arr = np.asarray(levels)
    speech = float(np.percentile(arr, 75))
    floor = float(np.percentile(arr, 10))
    threshold = max(floor + 6.0, min(speech - 18.0, -35.0))
    spans: list[tuple[float, float]] = []
    start = None
    for i, value in enumerate(arr):
        if value < threshold and start is None:
            start = i
        elif value >= threshold and start is not None:
            if (i - start) * window_s >= min_duration_s:
                spans.append((round(start * window_s, 3), round(i * window_s, 3)))
            start = None
    if start is not None and (len(arr) - start) * window_s >= min_duration_s:
        spans.append((round(start * window_s, 3), round(len(arr) * window_s, 3)))
    return spans


def sample_frames(video: Path, *, fps: float, width: int, start_s: float = 0.0, end_s: float | None = None):
    """Yield (t, rgb ndarray) frames decoded by FFmpeg at a fixed rate."""
    info = probe(video)
    if not info.has_video or not info.width or not info.height:
        return
    height = int(round(info.height * width / info.width / 2) * 2)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start_s:.3f}"]
    if end_s is not None:
        args += ["-t", f"{max(0.1, end_s - start_s):.3f}"]
    args += ["-i", str(video), "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = width * height * 3
    index = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield start_s + index / fps, np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            index += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def measure_loudness(path: Path) -> dict:
    """Integrated loudness / true peak via the ebur128 filter."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0?", "-af",
                           "ebur128=peak=true", "-f", "null", "-"], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    text = proc.stderr
    out: dict = {}
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    for key, label in (("integrated_lufs", "I:"), ("lra", "LRA:"), ("true_peak_dbtp", "Peak:")):
        idx = summary.find(label)
        if idx != -1:
            try:
                out[key] = float(summary[idx + len(label):].split()[0])
            except (ValueError, IndexError):
                pass
    return out


def detect_black_and_freeze(path: Path, duration_s: float) -> dict:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                           "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2", "-an", "-f", "null", "-"],
                          check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    black = sum(float(part.split("black_duration:")[1].split()[0]) for part in proc.stderr.splitlines() if "black_duration:" in part)
    freezes = sum(float(part.split("freeze_duration:")[1].split()[0]) for part in proc.stderr.splitlines() if "freeze_duration:" in part)
    return {"black_s": round(black, 2), "frozen_s": round(freezes, 2),
            "black_ratio": round(black / duration_s, 3) if duration_s else 0.0}


@lru_cache(maxsize=1)
def capabilities() -> dict:
    """Installed FFmpeg version and the build options CLIPFORGE relies on."""
    path = shutil.which("ffmpeg")
    if not path:
        return {"available": False}
    out = subprocess.run([path, "-hide_banner", "-version"], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
    first = out.splitlines()[0] if out else ""
    return {
        "available": True,
        "version": first.replace("ffmpeg version ", "").split(" ")[0],
        "libass": "--enable-libass" in out,
        "libx264": "--enable-libx264" in out,
        "gpl": "--enable-gpl" in out,
    }
