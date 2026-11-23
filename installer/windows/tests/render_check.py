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
    version = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
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
    print("live:", live_check(work, src))
    return 1 if failed else 0


def live_check(work: Path, src: Path) -> str:
    """OBS -> app over SRT with a passphrase, with the shipped FFmpeg on both ends (reported, not fatal)."""
    import re
    import socket
    import time

    from clipforge.media import live

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    secret = "abcdefghijklmnopqrstuvwxyz012345"
    out = work / "live"
    out.mkdir(exist_ok=True)
    listener = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-i",
                                 live.srt_listen_url(port, secret), "-map", "0:v:0?", "-map", "0:a:0?",
                                 "-c", "copy", "-f", "segment", "-segment_time", "4", "-reset_timestamps", "1",
                                 "-segment_format", "mpegts", str(out / "seg_%06d.ts")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(2)
    push = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-t", "10", "-i", str(src),
                           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-f", "mpegts",
                           re.sub(r"^srt://[^:/]+:", "srt://127.0.0.1:", live.srt_publish_url(port, secret))],
                          capture_output=True, text=True, timeout=90, check=False)
    time.sleep(3)
    listener.terminate()
    try:
        _, err = listener.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        listener.kill()
        _, err = listener.communicate()
    segments = sorted(out.glob("seg_*.ts"))
    ok = push.returncode == 0 and segments and sum(p.stat().st_size for p in segments) > 10_000
    detail = f"push exit {push.returncode}, {len(segments)} segment(s), listener exit {listener.returncode}"
    tail = (push.stderr.strip().splitlines() or [""])[-1][:160] + " | " + (err.decode(errors="replace").strip().splitlines() or [""])[-1][:160]
    return ("OK " if ok else "FAILED ") + detail + ("" if ok else f" ({tail})")


if __name__ == "__main__":
    sys.exit(main())
