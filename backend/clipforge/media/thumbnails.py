"""Thumbnail and cover generation (local, no image-generation API)."""

from __future__ import annotations

import logging
import subprocess
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..vendor.autoclip import captions as vcaptions
from . import ffmpeg_tools

log = logging.getLogger(__name__)


def best_frame_time(video: Path, start_s: float, end_s: float, *, scene_cuts: list[float] | None = None) -> float:
    """Pick the sharpest, best-exposed moment with a visible face."""
    import cv2

    detector = None
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions

        from . import models

        detector = FaceDetector.create_from_options(FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(models.ensure("face_detector"))), min_detection_confidence=0.35))
    except Exception as exc:  # noqa: BLE001 - degrade gracefully; logged
        log.debug("Face detector unavailable for thumbnails: %s", exc)
    cuts = scene_cuts or []
    best_t, best_score = (start_s + end_s) / 2, -1.0
    margin = min(0.6, (end_s - start_s) / 4)
    try:
        for t, frame in ffmpeg_tools.sample_frames(video, fps=2, width=480, start_s=start_s + margin, end_s=end_s - margin):
            if any(abs(t - c) < 0.4 for c in cuts):
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            sharp = min(1.0, np.log10(max(cv2.Laplacian(gray, cv2.CV_64F).var(), 1)) / 3)
            bright = 1.0 - abs(float(gray.mean()) - 128) / 128
            contrast = min(1.0, float(gray.std()) / 64)
            face = 0.0
            if detector is not None:
                from ..media.analysis import _detect_faces_tiled

                faces = _detect_faces_tiled(detector, mp, frame)
                face = min(1.0, max((f["area"] for f in faces), default=0.0) * 12) if faces else 0.0
            score = 0.4 * sharp + 0.15 * bright + 0.1 * contrast + 0.35 * face
            if score > best_score:
                best_t, best_score = t, score
    finally:
        if detector is not None:
            detector.close()
    return round(best_t, 3)


def extract_frame(video: Path, t: float, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(dest)], check=True, timeout=120)
    return dest


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = vcaptions.FONT_DIR / "Anton-Regular.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def compose(frame_path: Path, dest: Path, *, size: tuple[int, int], text: str | None, accent: str = "#FFD400") -> Path:
    """Cover-crop the frame to ``size`` and overlay bold hook text with a gradient."""
    image = Image.open(frame_path).convert("RGB")
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    image = image.resize((int(image.width * scale + 0.5), int(image.height * scale + 0.5)), Image.LANCZOS)
    left = (image.width - target_w) // 2
    top = max(0, min(image.height - target_h, int((image.height - target_h) * 0.35)))
    image = image.crop((left, top, left + target_w, top + target_h))
    if text:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        portrait = target_h > target_w
        band_top = int(target_h * (0.62 if portrait else 0.55))
        for y in range(band_top, target_h):
            alpha = int(200 * (y - band_top) / max(1, target_h - band_top))
            draw.line([(0, y), (target_w, y)], fill=(0, 0, 0, alpha))
        image = Image.alpha_composite(image.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(image)
        font_size = int(target_w * (0.095 if portrait else 0.075))
        font = _font(font_size)
        lines = textwrap.wrap(text.upper(), width=16 if portrait else 22)[:3]
        line_h = int(font_size * 1.1)
        y = target_h - int(target_h * 0.08) - line_h * len(lines)
        for n, line in enumerate(lines):
            width = draw.textlength(line, font=font)
            x = (target_w - width) / 2 if portrait else int(target_w * 0.05)
            draw.text((x, y + n * line_h), line, font=font, fill=accent if n == 0 else "#FFFFFF",
                      stroke_width=max(2, font_size // 14), stroke_fill="#000000")
        image = image.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=90, optimize=True)
    return dest
