"""Embedding backends.

The default is :class:`NullEmbedder`, which stores no vectors. That is deliberate:
the scaffold must come up on a clean machine without pulling torch (PRD G6), and
week 2's milestone is versioned ingestion, not dense retrieval. Chunks ingested
with NULL embeddings are still fully lexically retrievable, and week 3 backfills
them — the ``embedding IS NULL`` predicate is the backfill's work queue.

Switching backend is a config change (``EMBEDDING_BACKEND=sentence-transformers``),
not a code change. Changing the *model* is not: the column is ``vector(768)``, and
a model with another dimension needs a migration. Mixed-dimension vectors and
silently incomparable ones are the same bug wearing different hats.
"""

from __future__ import annotations

import logging
from typing import Protocol

from aletheia.settings import Settings

log = logging.getLogger("embedding")

EMBEDDING_DIM = 768
"""Matches intfloat/multilingual-e5-base and the chunks.embedding column."""


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float] | None]: ...


class NullEmbedder:
    """Stores no vectors. Dense retrieval is unavailable; lexical search is not."""

    name = "null"
    dimension = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        return [None] * len(texts)


class SentenceTransformerEmbedder:
    """Real embeddings, behind the ``models`` extra.

    The model is loaded on first use rather than at construction so that importing
    this module — which the ingestion service does unconditionally — never pulls
    several gigabytes into memory for a process that may only be serving health
    checks.
    """

    name = "sentence-transformers"

    def __init__(self, model_name: str, *, batch_size: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.dimension = EMBEDDING_DIM
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("loading embedding model", extra={"extra_fields": {"model": self.model_name}})
            self._model = SentenceTransformer(self.model_name)
            actual = int(self._model.get_sentence_embedding_dimension())
            if actual != EMBEDDING_DIM:
                # Failing loudly here beats writing vectors that compare against
                # nothing and produce quietly terrible retrieval.
                raise ValueError(
                    f"{self.model_name} produces {actual}-dimensional vectors, but the "
                    f"chunks.embedding column is vector({EMBEDDING_DIM}); "
                    "changing the embedding model requires a migration"
                )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vectors]


def get_embedder(settings: Settings) -> Embedder:
    backend = settings.embedding_backend.lower()
    if backend in ("null", "none", ""):
        return NullEmbedder()
    if backend in ("sentence-transformers", "st"):
        return SentenceTransformerEmbedder(settings.embedding_model)
    raise ValueError(f"unknown EMBEDDING_BACKEND {settings.embedding_backend!r}")
