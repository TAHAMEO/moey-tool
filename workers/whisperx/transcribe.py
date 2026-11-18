"""WhisperX transcription worker (runs in its own virtual environment).

CLIPFORGE calls this script instead of importing WhisperX, so PyTorch and
pyannote never enter the API/worker environment. Contract (see
clipforge/media/transcription.py, WhisperXEngine):

    transcribe.py --check --model small --models-dir DIR
        exit 0 and print a short status when the model can be loaded from DIR
        without network access; exit 1 with the reason otherwise.

    transcribe.py --wav FILE --start S --end E --language en|auto --model small --models-dir DIR --out OUT.json
        transcribe FILE[S:E] (16 kHz mono WAV) and write
        {"language": "en", "words": [{"text", "start", "end", "conf"}]}
        with times relative to S.

    transcribe.py --download --model small --models-dir DIR [--language en]
        fetch the Whisper model (and the English alignment model) into DIR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SAMPLE_RATE = 16000


def _device() -> tuple[str, str]:
    import torch

    if torch.cuda.is_available():
        return "cuda", "float16"
    return "cpu", "int8"


def _load(model: str, models_dir: Path, language: str | None, *, offline: bool):
    import whisperx

    device, compute_type = _device()
    asr = whisperx.load_model(model, device, compute_type=compute_type, language=language,
                              download_root=str(models_dir / "whisperx"), local_files_only=offline)
    return asr, device


def _fill_missing_times(words: list[dict]) -> list[dict]:
    """WhisperX leaves some tokens (numbers, symbols) unaligned: interpolate from neighbours."""
    for i, word in enumerate(words):
        if "start" in word and "end" in word:
            continue
        prev_end = next((w["end"] for w in reversed(words[:i]) if "end" in w), 0.0)
        next_start = next((w["start"] for w in words[i + 1:] if "start" in w), prev_end + 0.3)
        word["start"], word["end"] = prev_end, max(prev_end + 0.05, next_start)
    return words


def check(args: argparse.Namespace) -> int:
    try:
        _load(args.model, Path(args.models_dir), None, offline=True)
    except Exception as exc:  # noqa: BLE001 - report the reason to the caller
        print(f"model '{args.model}' not available offline: {exc.__class__.__name__}: {exc}"[:300])
        return 1
    device, compute = _device()
    print(f"whisperx {args.model} ready on {device} ({compute})")
    return 0


def download(args: argparse.Namespace) -> int:
    import whisperx

    models_dir = Path(args.models_dir)
    _load(args.model, models_dir, None, offline=False)
    language = None if args.language in (None, "auto") else args.language
    whisperx.load_align_model(language_code=language or "en", device="cpu", model_dir=str(models_dir / "whisperx-align"),
                              model_cache_only=False)
    print("downloaded")
    return 0


def transcribe(args: argparse.Namespace) -> int:
    import whisperx

    models_dir = Path(args.models_dir)
    language = None if args.language in (None, "auto") else args.language
    asr, device = _load(args.model, models_dir, language, offline=True)
    audio = whisperx.load_audio(args.wav)
    segment = audio[int(args.start * SAMPLE_RATE): int(args.end * SAMPLE_RATE)]
    result = asr.transcribe(segment, batch_size=8, language=language)
    detected = result.get("language") or language or "en"
    words: list[dict] = []
    try:
        align_model, metadata = whisperx.load_align_model(language_code=detected, device=device,
                                                          model_dir=str(models_dir / "whisperx-align"),
                                                          model_cache_only=True)
        aligned = whisperx.align(result["segments"], align_model, metadata, segment, device,
                                 return_char_alignments=False)
        for seg in aligned.get("segments", []):
            for w in seg.get("words", []):
                entry = {"text": w["word"].strip(), "conf": float(w.get("score", 1.0))}
                if "start" in w and "end" in w:
                    entry["start"], entry["end"] = float(w["start"]), float(w["end"])
                if entry["text"]:
                    words.append(entry)
    except Exception as exc:  # noqa: BLE001 - no alignment model for this language: segment-level timing
        print(f"alignment unavailable ({exc}); using evenly spaced word times", file=sys.stderr)
        for seg in result.get("segments", []):
            tokens = seg["text"].split()
            span = (seg["end"] - seg["start"]) / max(1, len(tokens))
            words += [{"text": t, "start": seg["start"] + i * span, "end": seg["start"] + (i + 1) * span, "conf": 0.6}
                      for i, t in enumerate(tokens)]
    Path(args.out).write_text(json.dumps({"language": detected, "words": _fill_missing_times(words)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--wav")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=0.0)
    parser.add_argument("--language", default="auto")
    parser.add_argument("--model", default="small")
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.check:
        return check(args)
    if args.download:
        return download(args)
    if not (args.wav and args.out and args.end > args.start):
        parser.error("--wav, --out and --end > --start are required")
    return transcribe(args)


if __name__ == "__main__":
    raise SystemExit(main())
