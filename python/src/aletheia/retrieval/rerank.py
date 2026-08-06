"""Cross-encoder reranking over the fused candidate list.

This is the one stage that buys precision back. The arms are deliberately wide
because recall cannot be recovered downstream; the reranker is what turns a wide,
noisy list into the handful of chunks generation actually sees.

It is also the retrieval stage's latency bottleneck, and it sits before generation
even starts. The budget is 1.2 s for all of retrieval (ADR-0001), which is what
sets the fused top-k the reranker is allowed to see.

Default is :class:`NullReranker`, which preserves fusion order. That keeps the
stack runnable without torch (PRD G6) and makes "no reranker" a real, measurable
ablation rather than a hypothetical one (PRD §7.3a).
"""

from __future__ import annotations

import logging
from typing import Protocol

from aletheia.retrieval.search import Fused
from aletheia.settings import Settings

log = logging.getLogger("retrieval.rerank")


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, fused: list[Fused], *, top_n: int) -> list[Fused]: ...


class NullReranker:
    """Truncates to top_n, preserving RRF order."""

    name = "null"

    def rerank(self, query: str, fused: list[Fused], *, top_n: int) -> list[Fused]:
        return fused[:top_n]


class CrossEncoderReranker:
    """Scores (query, chunk) pairs jointly. Behind the ``models`` extra.

    Loaded lazily so that importing this module — which the retrieval service does
    unconditionally — never pulls gigabytes into a process that may only be
    serving health checks.
    """

    name = "cross-encoder"

    def __init__(self, model_name: str, *, batch_size: int = 16, max_candidates: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        # Cross-encoding is O(candidates), not O(log n): every pair is a forward
        # pass. This cap is what keeps the stage inside its latency budget.
        self.max_candidates = max_candidates
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            log.info("loading reranker", extra={"extra_fields": {"model": self.model_name}})
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, fused: list[Fused], *, top_n: int) -> list[Fused]:
        if not fused:
            return []
        # Anything past the cap keeps its fusion rank and stays below the reranked
        # head, rather than being dropped: truncating here would throw away recall
        # the arms just paid for.
        head, tail = fused[: self.max_candidates], fused[self.max_candidates :]

        model = self._load()
        scores = model.predict(
            [(query, f.candidate.text) for f in head],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        ordered = [
            f for _, f in sorted(
                zip(scores, head, strict=True),
                key=lambda pair: (-float(pair[0]), pair[1].candidate.chunk_id),
            )
        ]
        return (ordered + tail)[:top_n]


def get_reranker(settings: Settings) -> Reranker:
    backend = settings.reranker_backend.lower()
    if backend in ("null", "none", ""):
        return NullReranker()
    if backend in ("cross-encoder", "ce"):
        return CrossEncoderReranker(
            settings.reranker_model, max_candidates=settings.rerank_max_candidates
        )
    raise ValueError(f"unknown RERANKER_BACKEND {settings.reranker_backend!r}")
