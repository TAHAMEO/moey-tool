"""The cover frame is taken from a caption-free still written by the same render pass.

Regression: covers used to be grabbed from the finished video, so the hook text was
drawn on top of burned-in captions (doubled text).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from clipforge.media import render


def _gray(path: Path, w: int = 270, h: int = 480) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float32)


def test_render_writes_caption_free_still(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=120)
    words = [{"text": w, "start": 0.3 + i * 0.45, "end": 0.7 + i * 0.45, "conf": 1.0}
             for i, w in enumerate(["this", "caption", "must", "never", "appear", "on", "the", "cover", "image", "at", "all"])]
    plan = render.plan_edits(0.0, 6.0, [], words, remove_silences=False, aspect="9:16", caption_style="bold_pop",
                             burn_captions=True, punch_in=False, loudness_lufs=-14.0, music=None, music_db=-22.0,
                             watermark=None, watermark_position="top_right")
    out, still = tmp_path / "clip.mp4", tmp_path / "still.jpg"
    decisions = render.render(src, plan, words, out, tmp_path / "work", has_video=True, has_audio=True,
                              still_at=2.0, still_path=still)
    assert decisions["captions_burned"] and decisions["still"] == str(still) and still.exists()
    captioned = tmp_path / "captioned.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2.08", "-i", str(out), "-frames:v", "1", str(captioned)],
                   check=True)
    clean, burned = _gray(still), _gray(captioned)
    assert clean.shape == burned.shape
    diff = np.abs(clean - burned)
    h = diff.shape[0]
    caption_band = diff[int(h * 0.55): int(h * 0.92)].mean()
    top_band = diff[int(h * 0.05): int(h * 0.40)].mean()
    # The still matches the video everywhere except where captions are burned in.
    assert caption_band > 3 * max(top_band, 1.0), (caption_band, top_band)


def test_cover_text_fits_without_dangling_words():
    from PIL import Image, ImageDraw

    from clipforge.media.thumbnails import cover_text, fit_text

    assert cover_text("So here is the biggest mistake most small businesses make") == \
        "biggest mistake most small businesses make"
    assert cover_text("The thing is, nobody tells you this") == "nobody tells you this"
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, _, size = fit_text("Here is the biggest mistake most small businesses make with pricing", draw,
                              width=1080, portrait=True)
    assert " ".join(lines) == "BIGGEST MISTAKE MOST SMALL BUSINESSES MAKE WITH PRICING" and len(lines) <= 3
    long = ("nobody tells you that raising your prices can actually bring you better clients who stay longer, "
            "refer their friends and complain a lot less than the bargain hunters do")
    lines, _, size = fit_text(long, draw, width=1080, portrait=True)
    assert len(lines) == 3 and lines[-1].endswith("…") and size >= int(1080 * 0.095 * 0.72)
    last_word = lines[-1].rstrip("…").split()[-1].lower()
    assert last_word not in {"the", "a", "of", "to", "and", "most", "small", "with"}


def test_brand_accent_colours_the_highlighted_caption_word(tmp_path):
    from clipforge.vendor.autoclip import captions as vcaptions

    words = [{"text": w, "start": 0.2 + i * 0.4, "end": 0.5 + i * 0.4, "conf": 1.0} for i, w in enumerate(["brand", "colour", "test"])]
    plan = render.plan_edits(0.0, 3.0, [], words, remove_silences=False, aspect="9:16", caption_style="bold_pop",
                             burn_captions=True, punch_in=False, loudness_lufs=-14.0, music=None, music_db=-22.0,
                             watermark=None, watermark_position="top_right", caption_accent="#12AB34")
    style = render.caption_style_for(plan)  # what render() burns in
    assert style.accent == "#12AB34"
    ass = vcaptions.write_ass(tmp_path / "c.ass", render.remap_words(words, plan.keep), style, width=1080, height=1920)
    text = ass.read_text()
    assert vcaptions.ass_colour_override("#12AB34") in text
    assert vcaptions.ass_colour_override("#FFE500") not in text  # the preset's own highlight colour was replaced
    plain = render.plan_edits(0.0, 3.0, [], words, remove_silences=False, caption_style="clean_lower", caption_accent="#12AB34")
    assert render.caption_style_for(plain).accent is None  # styles without a highlight stay plain
