"""Registry of local model files with integrity pinning.

Models are fetched only from their publishers' official hosts and verified by
size and SHA-256 before use. Hashes were recorded from the official files on
first retrieval (Google does not publish digests for these bundles), so any
later substitution is detected.
"""

from __future__ import annotations

import contextlib
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
                    handle.writelines(response.iter_bytes(1 << 20))
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


# ------------------------------------------------------------------ speech model
VOSK_MODEL = "vosk-model-en-us-0.22"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL}.zip"
VOSK_MANIFEST = Path(__file__).with_name("manifests") / f"{VOSK_MODEL}.sha256"
_VOSK_MAX_ZIP = 3 * 1024**3


def _vosk_manifest() -> dict[str, str]:
    entries = {}
    for line in VOSK_MANIFEST.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if digest and name:
            entries[name] = digest
    return entries


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_vosk_dir(root: Path) -> list[str]:
    """Return problems (empty when every file matches the pinned manifest)."""
    problems = []
    for name, digest in _vosk_manifest().items():
        path = root / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif _sha256_file(path) != digest:
            problems.append(f"checksum mismatch {name}")
    return problems


def _safe_extract(zip_path: Path, dest: Path) -> None:
    import zipfile

    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):  # zip-slip guard
                raise ModelUnavailable(f"Refusing unsafe path in archive: {member.filename}")
        archive.extractall(dest)


class _Progress:
    """Logs a long download's progress every 5 % (or every 30 s): size, speed and time left."""

    def __init__(self, label: str, total: int) -> None:
        import time

        self.label, self.total, self._time = label, total, time.monotonic
        self.started = self.last_log = self._time()
        self.next_percent = 5

    def update(self, done: int) -> None:
        now = self._time()
        percent = int(100 * done / self.total) if self.total else 0
        if percent < self.next_percent and now - self.last_log < 30:
            return
        self.last_log = now
        self.next_percent = percent + 5
        speed = done / max(now - self.started, 0.001)
        if self.total:
            left = (self.total - done) / max(speed, 1)
            log.info("%s: %d%% (%d of %d MB, %.1f MB/s, about %d min left)", self.label, percent,
                     done >> 20, self.total >> 20, speed / (1 << 20), int(left // 60) + 1)
        else:
            log.info("%s: %d MB (%.1f MB/s)", self.label, done >> 20, speed / (1 << 20))


def install_vosk(source: str | None = None) -> Path:
    """Install the English Vosk model, verified file-by-file against the pinned manifest.

    ``source`` may be a local .zip or an extracted folder (offline installs);
    otherwise the publisher's official archive is downloaded.
    """
    import shutil
    import tempfile

    root = get_settings().models_dir / "vosk"
    final = root / VOSK_MODEL
    if final.is_dir() and not verify_vosk_dir(final):
        return final
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root, prefix=".staging-"))
    try:
        if source and Path(source).is_dir():
            candidate = Path(source)
        else:
            zip_path = Path(source) if source else staging / f"{VOSK_MODEL}.zip"
            if not source:
                log.info("Downloading %s (about 1.8 GB)", VOSK_URL)
                size = 0
                try:
                    with httpx.stream("GET", VOSK_URL, timeout=httpx.Timeout(120, connect=20), follow_redirects=True) as response:
                        response.raise_for_status()
                        total = int(response.headers.get("content-length") or 0)
                        progress = _Progress("Speech model", total)
                        with open(zip_path, "wb") as handle:
                            for chunk in response.iter_bytes(1 << 20):
                                size += len(chunk)
                                if size > _VOSK_MAX_ZIP:
                                    raise ModelUnavailable("Download is larger than expected; aborted")
                                handle.write(chunk)
                                progress.update(size)
                except httpx.HTTPError as exc:
                    raise ModelUnavailable(f"Could not download {VOSK_URL}: {exc}") from exc
            _safe_extract(zip_path, staging / "x")
            candidate = staging / "x" / VOSK_MODEL
            if not candidate.is_dir():  # archive without the top-level folder
                candidate = staging / "x"
        problems = verify_vosk_dir(candidate)
        if problems:
            raise ModelUnavailable("Speech model failed verification: " + "; ".join(problems[:5]))
        if final.exists():
            shutil.rmtree(final)
        if candidate.is_relative_to(staging):
            candidate.replace(final)
        else:
            shutil.copytree(candidate, final)
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------- faster-whisper speech model
#: Approximate download sizes (MB), used for progress when the hub does not report sizes.
WHISPER_SIZES_MB = {"tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145, "small": 484, "small.en": 484,
                    "medium": 1530, "medium.en": 1530}


def faster_whisper_dir(size: str | None = None) -> Path:
    return get_settings().models_dir / "faster-whisper" / (size or get_settings().faster_whisper_model)


def faster_whisper_installed(size: str | None = None) -> bool:
    target = faster_whisper_dir(size)
    return (target / "model.bin").is_file() and (target / "config.json").is_file()


def install_faster_whisper(size: str | None = None, on_progress=None) -> Path:
    """Download a faster-whisper (CTranslate2 Whisper) model from its publisher on Hugging Face.

    It is fetched into a staging folder and moved into place only when complete, so an
    interrupted download is never mistaken for a model. ``on_progress(done_bytes, total_bytes)``
    is called about once a second while it downloads (``total_bytes`` may be 0 when unknown).
    """
    import shutil

    size = size or get_settings().faster_whisper_model
    target = faster_whisper_dir(size)
    if faster_whisper_installed(size):
        return target
    try:
        from faster_whisper import download_model
    except ImportError as exc:
        raise ModelUnavailable("faster-whisper is not installed") from exc
    staging = target.with_name(target.name + ".download")
    staging.mkdir(parents=True, exist_ok=True)
    total = WHISPER_SIZES_MB.get(size, 0) << 20
    progress = _Progress(f"Speech model ({size})", total)
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(1.0):
            done = 0
            for path in staging.rglob("*"):
                with contextlib.suppress(OSError):
                    if path.is_file():
                        done += path.stat().st_size
            progress.update(done)
            if on_progress is not None:
                with contextlib.suppress(Exception):
                    on_progress(done, total)

    watcher = threading.Thread(target=watch, name="whisper-download-progress", daemon=True)
    watcher.start()
    log.info("Downloading the faster-whisper %s speech model (about %d MB)", size, total >> 20)
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # huggingface_hub: "local_dir_use_symlinks is deprecated"
            download_model(size, output_dir=str(staging))
    except Exception as exc:  # network or hub errors: reported; the partial files are kept to resume
        raise ModelUnavailable(f"Could not download the faster-whisper {size} model: {exc}"[:400]) from exc
    finally:
        stop.set()
        watcher.join(timeout=5)
    if not ((staging / "model.bin").is_file() and (staging / "config.json").is_file()):
        raise ModelUnavailable(f"The faster-whisper {size} download is incomplete")
    shutil.rmtree(staging / ".cache", ignore_errors=True)
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    if on_progress is not None:
        with contextlib.suppress(Exception):
            on_progress(total, total)
    return target
