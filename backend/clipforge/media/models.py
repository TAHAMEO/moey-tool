"""Registry of local model files with integrity pinning.

Models are fetched only from their publishers' official hosts and verified by
size and SHA-256 before use. Hashes were recorded from the official files on
first retrieval (Google does not publish digests for these bundles), so any
later substitution is detected.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

_BASE = "https://storage.googleapis.com/mediapipe-models"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    url: str
    sha256: str
    size: int
    description: str
    license: str = "Apache-2.0"


MODELS: dict[str, ModelSpec] = {
    "face_landmarker": ModelSpec(
        "face_landmarker", "face_landmarker.task",
        f"{_BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff", 3758596,
        "MediaPipe face landmarks (reframing, active-speaker cue)",
    ),
    "face_detector": ModelSpec(
        "face_detector", "blaze_face_short_range.tflite",
        f"{_BASE}/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
        "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f", 229746,
        "MediaPipe BlazeFace short-range detector (thumbnails, visual scoring)",
    ),
    "text_embedder": ModelSpec(
        "text_embedder", "universal_sentence_encoder.tflite",
        f"{_BASE}/text_embedder/universal_sentence_encoder/float32/1/universal_sentence_encoder.tflite",
        "89ad3c74175dd8caa398cc22b657296d94302d20c525c12b58b29420f7249749", 6120274,
        "Universal Sentence Encoder (local semantic embeddings for memory and knowledge)",
    ),
    "yamnet": ModelSpec(
        "yamnet", "yamnet.tflite",
        f"{_BASE}/audio_classifier/yamnet/float32/1/yamnet.tflite",
        "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf", 4126810,
        "YAMNet audio events (laughter, applause, music) for highlight scoring",
    ),
}

_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    pass


def model_path(key: str) -> Path:
    spec = MODELS[key]
    return get_settings().models_dir / "mediapipe" / spec.filename


def _verify(path: Path, spec: ModelSpec) -> bool:
    if not path.exists() or path.stat().st_size != spec.size:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == spec.sha256


_verified: set[str] = set()


def is_available(key: str) -> bool:
    if key in _verified:
        return True
    ok = _verify(model_path(key), MODELS[key])
    if ok:
        _verified.add(key)
    return ok


def ensure(key: str) -> Path:
    """Return a verified local path, downloading from the official host if needed."""
    spec = MODELS[key]
    path = model_path(key)
    with _lock:
        if is_available(key):
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        log.info("Downloading model %s from %s", key, spec.url)
        try:
            with httpx.stream("GET", spec.url, timeout=120, follow_redirects=True) as response:
                response.raise_for_status()
                with open(tmp, "wb") as handle:
                    for chunk in response.iter_bytes(1 << 20):
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise ModelUnavailable(f"Could not download {key}: {exc}") from exc
        if not _verify(tmp, spec):
            tmp.unlink(missing_ok=True)
            raise ModelUnavailable(f"Integrity check failed for {key}; file discarded")
        tmp.replace(path)
        _verified.add(key)
        return path


def status() -> list[dict]:
    return [
        {"key": s.key, "description": s.description, "available": is_available(s.key), "license": s.license}
        for s in MODELS.values()
    ]
