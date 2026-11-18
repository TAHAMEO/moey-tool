"""Thumbnail and cover generation (local, no image-generation API)."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..vendor.autoclip import captions as vcaptions
from . import ffmpeg_tools
from .textutils import strip_discourse

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


#: Lead-ins that waste cover space ("Here is the biggest mistake" -> "Biggest mistake").
_LEAD_IN = re.compile(r"^(here(?:'s| is| are)|this is|these are|let me tell you|i want to (?:talk about|tell you)|"
                      r"today(?: we're| i'm)? (?:talking about|going to)|the thing is)[,:]?\s+(?:the |a |an |my |why )?",
                      re.IGNORECASE)
#: Words a cover line must not end on after a cut ("...MOST SMALL" reads as broken).
_DANGLING = {"the", "a", "an", "of", "to", "and", "or", "but", "with", "for", "in", "on", "at", "by", "from", "most",
             "many", "some", "my", "your", "our", "their", "his", "her", "its", "is", "are", "was", "were", "be", "that",
             "this", "these", "those", "very", "so", "small", "big", "more", "less", "than", "as", "if", "when", "how"}


def cover_text(text: str) -> str:
    """Hook text without discourse markers and wasteful lead-ins."""
    cleaned = strip_discourse(re.sub(r"\s+", " ", text).strip())
    cleaned = _LEAD_IN.sub("", cleaned).strip(" ,.;:-")
    return cleaned or text.strip()


def _wrap(words: list[str], font, draw, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def fit_text(text: str, draw, *, width: int, portrait: bool, max_lines: int = 3) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Wrap by measured width, shrinking the font before cutting anything; a cut ends on a whole
    phrase with an ellipsis instead of a dangling word."""
    words = cover_text(text).upper().split()
    base = int(width * (0.095 if portrait else 0.075))
    max_width = width * (0.88 if portrait else 0.6)
    for factor in (1.0, 0.9, 0.8, 0.72):
        size = int(base * factor)
        font = _font(size)
        lines = _wrap(words, font, draw, max_width)
        if len(lines) <= max_lines:
            return lines, font, size
    kept = " ".join(lines[:max_lines]).split()
    while len(kept) > 2 and re.sub(r"[^a-z']", "", kept[-1].lower()) in _DANGLING:
        kept.pop()
    lines = _wrap([*kept[:-1], kept[-1].rstrip(",.;:") + "…"], font, draw, max_width)[:max_lines]
    return lines, font, size


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
        lines, font, font_size = fit_text(text, draw, width=target_w, portrait=portrait)
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
