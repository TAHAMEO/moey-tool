"""Render a real clip on Windows with the FFmpeg the app ships (ffmpeg-binaries), the way a
downloaded video is rendered: source -> mezzanine -> multi-piece 9:16 edit with captions, text
hook, loudness normalisation. Run with the installed app's Python:

    <install dir>\\runtime\\venv\\Scripts\\python.exe render_check.py

Prints one line per check and exits 1 if the app's own render fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def main() -> int:
    from clipforge import desktop

    folder = desktop._ffmpeg_dir()
    if folder:
        os.environ["PATH"] = os.pathsep.join([str(folder), os.environ.get("PATH", "")])
    version = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    print("ffmpeg:", version)

    from clipforge.media import ffmpeg_tools, render

    work = Path(tempfile.mkdtemp(prefix="cf-render-"))
    src = work / "source.mp4"
    # 44.1 kHz audio with pauses (silence removal makes many pieces), 1280x720 video.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=40",
                    "-af", "volume='if(between(mod(t\\,6)\\,4\\,5)\\,0\\,1)':eval=frame", "-ac", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    info = ffmpeg_tools.probe(src)
    mez = work / "mezzanine.mp4"
    ffmpeg_tools.make_mezzanine(src, mez, info)
    print("mezzanine:", ffmpeg_tools.probe(mez).to_dict())

    words, t = [], 0.3
    for n in range(80):
        if 4 <= (t % 6) < 5:
            t += 1.0
        words.append({"text": ("Price" if n % 7 == 0 else "word") + ("." if n % 6 == 5 else ""), "start": round(t, 2),
                      "end": round(t + 0.32, 2), "conf": 1.0})
        t += 0.4
    silences = [(6 * k + 4.0, 6 * k + 5.0) for k in range(7)]
    failed = 0
    for style in ("bold_pop", "kinetic"):
        plan = render.plan_edits(2.0, 32.0, silences, words, aspect="9:16", caption_style=style, burn_captions=True,
                                 punch_in=True, hook_text="BIGGEST MISTAKE", fade_in=False)
        plan.zoom_windows.append([20.0, 21.5])
        plan.overlays.append({"text": "NOW THE IMPORTANT PART", "start": 19.5, "seconds": 2.0, "y": 0.42})
        out = work / f"clip_{style}.mp4"
        try:
            decisions = render.render(mez, plan, words, out, work / "w", has_video=True, has_audio=True)
            qc = render.quality_check(out, expected_duration=plan.output_duration, aspect="9:16",
                                      loudness_target=plan.loudness_lufs)
            ok = qc["passed"]
            print(f"render {style}: {'OK' if ok else 'QC FAILED'} pieces={len(plan.keep)} "
                  f"duration={qc.get('probe', {}).get('duration_s')} captions={decisions['captions_burned']}")
            failed += 0 if ok else 1
        except Exception:  # noqa: BLE001 - report everything
            failed += 1
            print(f"render {style}: FAILED")
            traceback.print_exc(limit=3)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
