"""Local-first speech recognition with word-level timestamps.

Engine order (first available wins, configurable):

1. **WhisperX** — isolated venv (PyTorch, alignment, optional diarization),
   invoked as a subprocess so its heavy dependencies never enter this process.
2. **faster-whisper** — CTranslate2 Whisper, CPU int8, no PyTorch.
3. **Vosk** — small Kaldi models, CPU only, very light.
4. **Cloud ASR** (OpenAI-compatible ``/audio/transcriptions``) — only when the
   owner allows paid transcription and no local engine is available.

Long media is split into ~5-minute chunks at quiet points; every chunk is a
checkpoint, so a failure re-runs only the chunks that did not finish.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import numpy as np

from ..config import BACKEND_DIR, REPO_DIR, get_settings
from . import ffmpeg_tools

log = logging.getLogger(__name__)

CHUNK_S = 300.0
SEARCH_S = 20.0


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass
class WordOut:
    text: str
    start: float
    end: float
    conf: float = 1.0

    def to_dict(self) -> dict:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3), "conf": round(self.conf, 3)}


# --------------------------------------------------------------------------- engines
class Engine:
    name = "base"
    quality = 1
    punctuated = False
    languages: tuple[str, ...] = ()

    def availability(self) -> tuple[bool, str]:
        raise NotImplementedError

    def transcribe(self, wav: Path, start_s: float, end_s: float, language: str | None) -> list[WordOut]:
        raise NotImplementedError

    @property
    def model_label(self) -> str:
        return ""


class VoskEngine(Engine):
    name = "vosk"
    quality = 2
    punctuated = False
    languages = ("en",)
    _model = None
    _lock = threading.Lock()

    def _model_dir(self) -> Path | None:
        settings = get_settings()
        if settings.vosk_model_path and (settings.vosk_model_path / "conf" / "model.conf").exists():
            return settings.vosk_model_path
        root = settings.models_dir / "vosk"
        if root.exists():
            for child in sorted(root.iterdir()):
                if (child / "conf" / "model.conf").exists() or (child / "am").exists():
                    return child
        return None

    @property
    def model_label(self) -> str:
        path = self._model_dir()
        return path.name if path else ""

    def availability(self) -> tuple[bool, str]:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False, "vosk package not installed"
        path = self._model_dir()
        return (True, f"model {path.name}") if path else (False, "no Vosk model in models/vosk")

    def _load(self):
        with VoskEngine._lock:
            if VoskEngine._model is None:
                import vosk

                vosk.SetLogLevel(-1)
                VoskEngine._model = vosk.Model(str(self._model_dir()))
            return VoskEngine._model

    def transcribe(self, wav: Path, start_s: float, end_s: float, language: str | None) -> list[WordOut]:
        import vosk

        model = self._load()
        recognizer = vosk.KaldiRecognizer(model, ffmpeg_tools.ASR_RATE)
        recognizer.SetWords(True)
        samples = ffmpeg_tools.read_wav(wav, start_s, end_s)
        words: list[WordOut] = []
        block = 8000
        data = samples.tobytes()
        step = block * 2
        for offset in range(0, len(data), step):
            if recognizer.AcceptWaveform(data[offset:offset + step]):
                words.extend(self._words(json.loads(recognizer.Result()), start_s))
        words.extend(self._words(json.loads(recognizer.FinalResult()), start_s))
        return words

    @staticmethod
    def _words(result: dict, offset: float) -> list[WordOut]:
        return [WordOut(w["word"], offset + float(w["start"]), offset + float(w["end"]), float(w.get("conf", 1.0)))
                for w in result.get("result", []) if w.get("word")]


class FasterWhisperEngine(Engine):
    name = "faster_whisper"
    quality = 3
    punctuated = True
    languages = ("*",)
    _models: ClassVar[dict[str, object]] = {}  # shared across instances: one loaded model per size
    _lock = threading.Lock()

    @property
    def model_label(self) -> str:
        return get_settings().faster_whisper_model

    def _local_model(self) -> str | None:
        settings = get_settings()
        local = settings.models_dir / "faster-whisper" / settings.faster_whisper_model
        if (local / "model.bin").exists():
            return str(local)
        hub = Path.home() / ".cache" / "huggingface" / "hub"
        if any(hub.glob(f"models--Systran--faster-whisper-{settings.faster_whisper_model}/snapshots/*/model.bin")):
            return settings.faster_whisper_model
        return None

    def availability(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False, "faster-whisper not installed"
        model = self._local_model()
        if model:
            return True, f"model {get_settings().faster_whisper_model}"
        return False, ("model not downloaded (run `clipforge download-models --faster-whisper`; "
                       "needs access to huggingface.co)")

    def transcribe(self, wav: Path, start_s: float, end_s: float, language: str | None) -> list[WordOut]:
        from faster_whisper import WhisperModel

        model_ref = self._local_model()
        if model_ref is None:
            raise TranscriptionUnavailable("faster-whisper model not available")
        with FasterWhisperEngine._lock:
            model = FasterWhisperEngine._models.get(model_ref)
            if model is None:
                model = FasterWhisperEngine._models[model_ref] = WhisperModel(model_ref, device="cpu", compute_type="int8")
        audio = ffmpeg_tools.read_wav(wav, start_s, end_s).astype(np.float32) / 32768.0
        segments, _info = model.transcribe(audio, language=language if language and language != "auto" else None,
                                           word_timestamps=True, vad_filter=True, beam_size=5)
        words: list[WordOut] = []
        for segment in segments:
            for w in segment.words or []:
                text = w.word.strip()
                if text:
                    words.append(WordOut(text, start_s + w.start, start_s + w.end, float(w.probability)))
        return words


class WhisperXEngine(Engine):
    """Runs ``workers/whisperx/transcribe.py`` in its own virtual environment."""

    name = "whisperx"
    quality = 3
    punctuated = True
    languages = ("*",)

    @property
    def model_label(self) -> str:
        return get_settings().faster_whisper_model

    def _script(self) -> Path:
        for base in (REPO_DIR, BACKEND_DIR.parent, Path("/app")):
            candidate = base / "workers" / "whisperx" / "transcribe.py"
            if candidate.exists():
                return candidate
        return REPO_DIR / "workers" / "whisperx" / "transcribe.py"

    def availability(self) -> tuple[bool, str]:
        python = get_settings().whisperx_python
        if not python or not Path(python).exists():
            return False, "isolated WhisperX environment not configured (CLIPFORGE_WHISPERX_PYTHON)"
        try:
            proc = subprocess.run([str(python), str(self._script()), "--check", "--model", self.model_label,
                                   "--models-dir", str(get_settings().models_dir)],
                                  check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"WhisperX check failed: {exc}"
        if proc.returncode == 0:
            return True, proc.stdout.strip()[:200] or "ready"
        return False, (proc.stdout + proc.stderr).strip().splitlines()[-1][:300] if (proc.stdout + proc.stderr).strip() else "not ready"

    def transcribe(self, wav: Path, start_s: float, end_s: float, language: str | None) -> list[WordOut]:
        python = get_settings().whisperx_python
        out_path = wav.parent / f"whisperx_{int(start_s)}_{int(end_s)}.json"
        proc = subprocess.run([str(python), str(self._script()), "--wav", str(wav), "--start", str(start_s),
                               "--end", str(end_s), "--language", language or "auto", "--model", self.model_label,
                               "--models-dir", str(get_settings().models_dir), "--out", str(out_path)],
                              check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=max(600, int((end_s - start_s) * 6)))
        if proc.returncode != 0:
            raise TranscriptionUnavailable(f"WhisperX failed: {(proc.stderr or proc.stdout)[-500:]}")
        data = json.loads(out_path.read_text(encoding="utf-8"))
        out_path.unlink(missing_ok=True)
        return [WordOut(w["text"], start_s + w["start"], start_s + w["end"], w.get("conf", 1.0)) for w in data["words"]]


ENGINES: dict[str, Engine] = {"whisperx": WhisperXEngine(), "faster_whisper": FasterWhisperEngine(), "vosk": VoskEngine()}
ORDER = ("whisperx", "faster_whisper", "vosk")


def release_models() -> None:
    """Free the speech models loaded in this process (the large Vosk model alone holds about
    5.5 GB) so rendering, which runs in the same media worker, has the memory it needs."""
    import gc

    with VoskEngine._lock:
        VoskEngine._model = None
    with FasterWhisperEngine._lock:
        FasterWhisperEngine._models.clear()
    gc.collect()


def engines_status() -> list[dict]:
    statuses = []
    for name in ORDER:
        ok, reason = ENGINES[name].availability()
        statuses.append({"engine": name, "available": ok, "detail": reason, "quality": ENGINES[name].quality,
                         "punctuated": ENGINES[name].punctuated})
    return statuses


def select_engine(language: str | None, preference: str = "auto") -> Engine:
    names = [preference] if preference != "auto" and preference in ENGINES else list(ORDER)
    reasons = []
    for name in names:
        engine = ENGINES[name]
        ok, reason = engine.availability()
        if ok and (engine.languages == ("*",) or not language or language in engine.languages or language == "auto"):
            return engine
        reasons.append(f"{name}: {reason if not ok else 'language ' + str(language) + ' not supported'}")
    raise TranscriptionUnavailable("No local transcription engine is available (" + "; ".join(reasons) + ")")


# --------------------------------------------------------------------- chunking
def plan_chunks(wav: Path, *, chunk_s: float | None = None, search_s: float | None = None) -> list[tuple[float, float]]:
    """Split long audio near the quietest point around each nominal boundary."""
    chunk_s = chunk_s or CHUNK_S
    search_s = search_s or SEARCH_S
    duration = ffmpeg_tools.wav_duration(wav)
    if duration <= chunk_s * 1.2:
        return [(0.0, round(duration, 3))]
    levels = ffmpeg_tools.loudness_curve(wav, 0.25)
    bounds = [0.0]
    target = chunk_s
    while target < duration - chunk_s * 0.3:
        lo, hi = int((target - search_s) / 0.25), int((target + search_s) / 0.25)
        window = levels[max(lo, 0):min(hi, len(levels))]
        cut = (max(lo, 0) + int(np.argmin(window))) * 0.25 if window else target
        bounds.append(round(cut + 0.125, 3))
        target = cut + chunk_s
    bounds.append(round(duration, 3))
    return list(itertools.pairwise(bounds))


# ------------------------------------------------------------- punctuation
_QUESTION_START = re.compile(r"^(what|why|how|when|where|who|which|do|does|did|is|are|was|were|can|could|would|should|will|have|has|isn't|aren't|don't|didn't)$")
_PROPER = {"i": "I", "i'm": "I'm", "i've": "I've", "i'll": "I'll", "i'd": "I'd", "youtube": "YouTube", "instagram": "Instagram",
           "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday", "friday": "Friday",
           "saturday": "Saturday", "sunday": "Sunday", "january": "January", "america": "America", "google": "Google",
           "ai": "AI", "tv": "TV", "ok": "OK"}


def restore_punctuation(words: list[dict], *, long_pause: float = 0.6, short_pause: float = 0.35) -> list[dict]:
    """Sentence boundaries and casing for engines that output bare lowercase words.

    Boundaries come from pauses (the strongest prosodic cue available without a
    punctuation model); sentences that open with an interrogative get "?".
    """
    if not words:
        return words
    out = [dict(w) for w in words]
    sentence_start = 0
    for i, word in enumerate(out):
        token = word["text"].strip()
        word["text"] = _PROPER.get(token.lower(), token)
        is_last = i == len(out) - 1
        gap = (out[i + 1]["start"] - word["end"]) if not is_last else 99.0
        length = i - sentence_start + 1
        boundary = gap >= long_pause or (gap >= short_pause and length >= 8) or length >= 40 or is_last
        if boundary:
            first = out[sentence_start]
            first["text"] = first["text"][:1].upper() + first["text"][1:]
            mark = "?" if _QUESTION_START.match(out[sentence_start]["text"].lower()) else "."
            if not re.search(r"[.!?…]$", word["text"]):
                word["text"] += mark
            sentence_start = i + 1
        elif gap >= 0.25 and length >= 6 and not re.search(r"[,.!?]$", word["text"]):
            word["text"] += ","
    return out


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
