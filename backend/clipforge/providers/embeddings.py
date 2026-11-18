"""Local, zero-cost text embeddings.

``local-hybrid-v1`` concatenates two L2-normalised parts:

* 100-d Universal Sentence Encoder vector (MediaPipe, runs on CPU in ~10 ms)
  for semantic similarity, and
* 256-d signed feature hashing of stemmed unigrams/bigrams for exact lexical
  overlap (names, numbers, product terms that USE blurs).

On a small memory-retrieval benchmark the hybrid beat either part alone
(10/12 vs 9/12 USE-only vs 7/12 lexical-only top-1). If the USE model cannot be
loaded the semantic slots are zero, so stored vectors stay compatible and
search degrades to lexical matching instead of failing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np

log = logging.getLogger(__name__)

SEM_DIMS = 100
LEX_DIMS = 256
DIMS = SEM_DIMS + LEX_DIMS
EMBEDDER_ID = "local-hybrid-v1"
_SEM_WEIGHT = 0.8
_LEX_WEIGHT = 0.6

_STOP = set(
    "the a an is are was were be been to of and or for on in with what how should my i me can which who "
    "where when does do it this that these those you your we our they their as at by from about into".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = [w[:6] for w in _TOKEN_RE.findall(text.lower()) if w not in _STOP]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def lexical_vector(text: str) -> np.ndarray:
    vec = np.zeros(LEX_DIMS, dtype=np.float32)
    for tok in _tokens(text):
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
        vec[h % LEX_DIMS] += 1.0 if (h >> 32) & 1 else -1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


class LocalHybridEmbedder:
    id = EMBEDDER_ID
    dims = DIMS

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._embedder = None
        self._semantic_ok: bool | None = None

    def _load(self):
        if self._semantic_ok is not None:
            return self._embedder
        try:
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.text import TextEmbedder, TextEmbedderOptions

            from ..media import models

            path = models.ensure("text_embedder")
            options = TextEmbedderOptions(base_options=BaseOptions(model_asset_path=str(path)), l2_normalize=True)
            self._embedder = TextEmbedder.create_from_options(options)
            self._semantic_ok = True
        except Exception as exc:  # model missing, no EGL, etc.
            log.warning("Semantic embedder unavailable, using lexical-only vectors: %s", exc)
            self._semantic_ok = False
        return self._embedder

    @property
    def semantic_available(self) -> bool:
        with self._lock:
            self._load()
        return bool(self._semantic_ok)

    def embed_one(self, text: str) -> list[float]:
        text = (text or "").strip()[:4000] or "empty"
        with self._lock:
            embedder = self._load()
            if embedder is not None:
                sem = np.asarray(embedder.embed(text).embeddings[0].embedding, dtype=np.float32)
                if sem.shape[0] != SEM_DIMS:
                    sem = np.resize(sem, SEM_DIMS)
            else:
                sem = np.zeros(SEM_DIMS, dtype=np.float32)
        vec = np.concatenate([sem * _SEM_WEIGHT, lexical_vector(text) * _LEX_WEIGHT])
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm else vec).astype(np.float32).tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


_instance: LocalHybridEmbedder | None = None
_instance_lock = threading.Lock()


def get_embedder() -> LocalHybridEmbedder:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = LocalHybridEmbedder()
        return _instance


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom else 0.0
