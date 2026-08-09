"""Lazy MiniLM embedding service and pgvector serialization helpers."""

from __future__ import annotations

import logging
import os
import threading
from time import perf_counter
from typing import Any, Callable

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_DIMENSIONS = 384

logger = logging.getLogger(__name__)

_MODEL: Any = None
_MODEL_NAME: str | None = None
_MODEL_LOCK = threading.Lock()


class EmbeddingError(RuntimeError):
    """Normalized embedding failure."""


def vector_literal(values: list[float], *, dimensions: int = DEFAULT_DIMENSIONS) -> str:
    """Serialize a validated embedding for a parameterized pgvector cast."""
    if len(values) != dimensions:
        raise EmbeddingError(f"Embedding has {len(values)} dimensions; expected {dimensions}.")
    try:
        return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("Embedding contained a non-numeric value.") from exc


class EmbeddingService:
    """Process-lazy wrapper around the locked all-MiniLM-L6-v2 model."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        threads: int = 2,
        model_loader: Callable[[str], Any] | None = None,
    ):
        self.model_name = model_name
        self.dimensions = dimensions
        self.threads = threads
        self._model_loader = model_loader
        self._injected_model: Any = None

    def _load_default_model(self) -> Any:
        global _MODEL, _MODEL_NAME
        if _MODEL is None:
            with _MODEL_LOCK:
                if _MODEL is None:
                    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                    os.environ.setdefault("OMP_NUM_THREADS", str(self.threads))
                    os.environ.setdefault("MKL_NUM_THREADS", str(self.threads))
                    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                    logger.info("embedding_model_load_started model=%s", self.model_name)
                    try:
                        import torch
                        from sentence_transformers import SentenceTransformer

                        torch.set_num_threads(self.threads)
                        model = SentenceTransformer(self.model_name, device="cpu")
                    except Exception as exc:
                        raise EmbeddingError("Could not load the MiniLM embedding model.") from exc
                    _MODEL = model
                    _MODEL_NAME = self.model_name
                    logger.info("embedding_model_load_completed model=%s", self.model_name)
        if _MODEL_NAME != self.model_name:
            raise EmbeddingError("The process already loaded a different embedding model.")
        return _MODEL

    def get_model(self) -> Any:
        """Load the model once per process, or once per injected test service."""
        if self._model_loader is None:
            model = self._load_default_model()
        else:
            if self._injected_model is None:
                self._injected_model = self._model_loader(self.model_name)
            model = self._injected_model

        try:
            dimension_reader = getattr(model, "get_embedding_dimension", None)
            if dimension_reader is None:
                dimension_reader = model.get_sentence_embedding_dimension
            actual_dimensions = int(dimension_reader())
        except Exception as exc:
            raise EmbeddingError("Could not determine the embedding model dimensions.") from exc
        if actual_dimensions != self.dimensions:
            raise EmbeddingError(
                f"Embedding model returned {actual_dimensions} dimensions; expected {self.dimensions}."
            )
        return model

    def embed(self, text: str) -> list[float]:
        """Embed one non-empty string as a normalized 384-dimensional vector."""
        vectors = self.embed_batch([text])
        return vectors[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch with normalized vectors and strict dimension validation."""
        if not isinstance(texts, list) or not texts:
            raise EmbeddingError("At least one text value is required.")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("Embedding text values must be non-empty strings.")

        model = self.get_model()
        started = perf_counter()
        try:
            encoded = model.encode(
                [text.strip() for text in texts],
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
                batch_size=min(32, len(texts)),
            )
            vectors = [[float(value) for value in row] for row in encoded]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("MiniLM embedding failed.") from exc

        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingError(f"MiniLM must return exactly {self.dimensions} dimensions per text.")
        logger.info(
            "embedding_completed model=%s count=%d duration_ms=%d",
            self.model_name,
            len(vectors),
            round((perf_counter() - started) * 1000),
        )
        return vectors
