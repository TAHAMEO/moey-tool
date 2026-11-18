"""Generate a deterministic ~95 s test video with real speech, faces and scene cuts.

Speech: FFmpeg's built-in flite TTS, one sentence at a time with natural pauses.
Visuals: three scenes — a real (public-domain) face on the left, a text card,
and the face on the right — so scene detection, face-aware reframing,
transcription and highlight selection all have genuine signal to work with.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

SCENES = [
    ("left", "slt", [
        "Welcome back to the channel.",
        "Today we are talking about pricing.",
        "Here is the biggest mistake most small businesses make with pricing.",
        "They charge too little because they are afraid of losing customers.",
        "But low prices attract the worst customers.",
    ]),
    ("card", "awb", [
        "Um, okay, so, let me just check my notes here.",
        "Let me give you a quick example.",
        "A designer I worked with doubled her rates last year.",
        "She lost two clients, but her income went up by forty percent.",
        "That is the power of charging what you are worth.",
    ]),
    ("right", "slt", [
        "So here is my number one tip.",
        "Never compete on price.",
        "Compete on value.",
        "If you remember one thing from this video, remember that.",
        "And if you found this helpful, subscribe for more.",
    ]),
    ("left", "rms", [
        "Now let us talk about raising your prices without losing trust.",
        "First, tell your best clients early.",
        "Second, explain the extra value they will get.",
        "Third, give them a deadline to lock in the old rate.",
        "Most of them will stay, and the ones who leave were not a good fit anyway.",
    ]),
]
GAP_S = 0.7


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def build(dest: Path) -> dict:
    work = dest.parent / "build"
    work.mkdir(parents=True, exist_ok=True)
    pieces: list[Path] = []
    scene_spans: list[tuple[str, float, float]] = []
    script: list[dict] = []
    t = 0.0
    index = 0
    for scene_kind, voice, sentences in SCENES:
        scene_start = t
        for sentence in sentences:
            wav = work / f"s{index:02d}.wav"
            text = sentence.replace("'", "").replace(":", "")
            _run(["-f", "lavfi", "-i", f"flite=text='{text}':voice={voice}", "-af", "aresample=48000",
                  "-ac", "1", str(wav)])
            gap = work / f"g{index:02d}.wav"
            _run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(GAP_S), str(gap)])
            length = _duration(wav)
            script.append({"text": sentence, "start": round(t, 2), "end": round(t + length, 2), "scene": scene_kind})
            pieces += [wav, gap]
            t += length + GAP_S
            index += 1
        scene_spans.append((scene_kind, scene_start, t))
    concat_list = work / "audio.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in pieces))
    audio = work / "speech.wav"
    _run(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(audio)])
    total = _duration(audio)

    face = FIXTURES / "astronaut.jpg"
    face_scenes = sum(1 for kind, _s, _e in scene_spans if kind != "card")
    labels = "".join(f"[face{i}]" for i in range(face_scenes))
    filters = ["[0:v]format=yuv420p[bg]", f"[1:v]scale=640:640,format=rgba,split={face_scenes}{labels}"]
    current = "bg"
    face_index = 0
    for n, (kind, start, end) in enumerate(scene_spans):
        enable = f"between(t,{start:.2f},{end:.2f})"
        if kind == "card":
            filters.append(
                f"[{current}]drawbox=x=0:y=0:w=iw:h=ih:color=0x1b2a4a@1:t=fill:enable='{enable}',"
                f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='PRICING EXAMPLE':"
                f"fontsize=96:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:enable='{enable}'[v{n}]")
        else:
            x = "220+20*sin(t/3)" if kind == "left" else "1060+20*sin(t/3)"
            filters.append(f"[{current}][face{face_index}]overlay=x='{x}':y=220:enable='{enable}'[v{n}]")
            face_index += 1
        current = f"v{n}"
    filters.append(f"[{current}]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='CLIPFORGE TEST':"
                   f"fontsize=28:fontcolor=white@0.6:x=40:y=40[vout]")
    _run([
        "-f", "lavfi", "-i", f"color=c=0x2d3436:s=1920x1080:r=30:d={total:.2f}",
        "-loop", "1", "-framerate", "30", "-t", f"{total:.2f}", "-i", str(face),
        "-i", str(audio),
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(dest),
    ])
    return {"path": str(dest), "duration": total, "script": script,
            "scenes": [{"kind": k, "start": round(s, 2), "end": round(e, 2)} for k, s, e in scene_spans]}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES / "generated" / "talk.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    import json

    print(json.dumps(build(target), indent=2))
