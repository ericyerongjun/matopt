"""Optional local embeddings for the recall index.

DeepSeek has no embedding endpoint, so dense retrieval runs locally and is
off by default (``memory_embedding_backend = "none"``): BM25 plus the term
graph already cover exact notation and named results, which matter most in
mathematics. Two CPU backends are supported, both without torch:

``model2vec``  static embeddings (MinishLab, MIT; numpy only, ~30-130 MB).
               Default model ``minishlab/potion-retrieval-32M``.
``fastembed``  ONNX models through onnxruntime (Qdrant, Apache-2.0).
               Default model ``BAAI/bge-small-en-v1.5``.

A backend that is not installed, or whose model cannot be loaded, disables
itself with one warning; retrieval falls back to the lexical and graph
rankers. Vectors are L2-normalised float32 so cosine similarity is a dot
product and can be scored with numpy without a vector extension.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Protocol

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"model2vec": "minishlab/potion-retrieval-32M", "fastembed": "BAAI/bge-small-en-v1.5"}
MAX_CHARS = 4_000


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(vectors) -> np.ndarray:
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class _Model2Vec:
    def __init__(self, model: str):
        from model2vec import StaticModel  # type: ignore[import-not-found]
        self.name = f"model2vec:{model}"
        self._model = StaticModel.from_pretrained(model)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _normalise(self._model.encode([t[:MAX_CHARS] for t in texts]))


class _FastEmbed:
    def __init__(self, model: str):
        from fastembed import TextEmbedding  # type: ignore[import-not-found]
        self.name = f"fastembed:{model}"
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _normalise(list(self._model.embed([t[:MAX_CHARS] for t in texts])))


_lock = threading.Lock()
_cache: dict[tuple[str, str], Optional[Embedder]] = {}


def get_embedder(backend: str, model: str = "") -> Optional[Embedder]:
    """The configured embedder, built once per process; ``None`` when off or unavailable."""
    backend = (backend or "none").lower()
    if backend == "none":
        return None
    model = model or DEFAULT_MODELS.get(backend, "")
    key = (backend, model)
    with _lock:
        if key not in _cache:
            try:
                if backend == "model2vec":
                    _cache[key] = _Model2Vec(model)
                elif backend == "fastembed":
                    _cache[key] = _FastEmbed(model)
                else:
                    raise ValueError(f"unknown embedding backend {backend!r}")
            except Exception:  # noqa: BLE001 — optional dependency or model download
                logger.warning("Recall embeddings unavailable (%s %s); using lexical retrieval only",
                               backend, model, exc_info=True)
                _cache[key] = None
        return _cache[key]


def set_embedder(backend: str, model: str, embedder: Optional[Embedder]) -> None:
    """Install an embedder for ``(backend, model)``; for tests."""
    with _lock:
        _cache[((backend or "none").lower(), model or DEFAULT_MODELS.get(backend, ""))] = embedder
