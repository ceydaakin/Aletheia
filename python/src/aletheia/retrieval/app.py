"""Retrieval service — hybrid search over the bitemporal chunk store.

Lexical (Postgres FTS, language-aware) and dense (pgvector HNSW) arms run
concurrently, fuse with RRF, and the fused head is reranked down to what
generation sees. See ADR-0006.

The service works with no embeddings at all: RRF over a single list is that
list's ranking, so a corpus ingested with the null embedding backend is still
fully lexically retrievable. That is what keeps `docker compose up` viable on a
clean machine (PRD G6).
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from aletheia.contracts import Chunk, RetrieveRequest, RetrieveResponse
from aletheia.db import get_db
from aletheia.embedding import NullEmbedder, get_embedder
from aletheia.metrics import registry
from aletheia.retrieval.rerank import get_reranker
from aletheia.retrieval.search import (
    dense_search,
    lexical_search,
    reciprocal_rank_fusion,
    tenant_languages,
    unique_langs,
)
from aletheia.service import create_app
from aletheia.settings import get_settings

log = logging.getLogger("retrieval")

METRIC_ARM_HITS = "aletheia_retrieval_arm_results"
METRIC_ARM_SECONDS = "aletheia_retrieval_arm_duration_seconds"

_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    settings = get_settings()
    db = get_db()

    app.state.embedder = get_embedder(settings)
    app.state.reranker = get_reranker(settings)

    try:
        await db.open()
        _ready = await db.healthy()
    except Exception:
        # Serve health checks and report not-ready rather than crash-loop. A
        # database that is merely slow to start must not take the process with it
        # (ADR-0001: /readyz stops traffic, /healthz keeps the container alive).
        log.exception("could not open the database; running degraded")
        _ready = False

    log.info(
        "retrieval started",
        extra={
            "extra_fields": {
                "ready": _ready,
                "embedder": app.state.embedder.name,
                "reranker": app.state.reranker.name,
            }
        },
    )
    yield
    await db.close()


app = create_app("retrieval", ready=lambda: _ready, lifespan=lifespan)


def _parse_as_of(raw: str) -> datetime | None:
    """Interpret the API's ``as_of``, which selects the valid-time slice.

    None means "now", resolved by the database rather than here — the application
    clock has no business deciding what the store considers current.

    A malformed date must not silently fall back to now: the caller asked about a
    specific point in history, and answering about a different one is worse than
    refusing.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"as_of is not an ISO 8601 date: {raw!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@app.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    settings = get_settings()
    started = time.perf_counter()

    valid_at = _parse_as_of(req.as_of)
    # None: the database supplies both bounds. See _TEMPORAL_PREDICATE.
    known_at = None
    k = req.k or settings.retrieval_lexical_k

    # The language lookup gets its own short-lived connection, released before the
    # arms fan out. Holding it across the fan-out means every request occupies one
    # connection *plus* one per arm, so at concurrency 20 the requests starve each
    # other on the pool and retrieval times out — which the gateway then turns into
    # abstain(out_of_corpus). Measured: every request timing out at the 1.2 s stage
    # budget under a 20-way load test.
    async with get_db().connection() as conn:
        langs = unique_langs(req.lang, await tenant_languages(conn, req.tenant_id))

    # One lexical arm per language configuration, plus dense. A tenant holding
    # both Turkish and English documents would otherwise have one of the two
    # stemmed with the wrong rules.
    async def lexical(lang: str):
        arm_started = time.perf_counter()
        async with get_db().connection() as arm_conn:
            results = await lexical_search(
                arm_conn, req.tenant_id, req.query,
                k=k, valid_at=valid_at, known_at=known_at, lang=lang,
            )
        _record(f"lexical:{lang or 'default'}", results, arm_started)
        return f"lexical:{lang or 'default'}", results

    async def dense():
        embedder = app.state.embedder
        if isinstance(embedder, NullEmbedder):
            return "dense", []
        arm_started = time.perf_counter()
        vectors = embedder.embed([req.query])
        if not vectors or vectors[0] is None:
            return "dense", []
        async with get_db().connection() as arm_conn:
            results = await dense_search(
                arm_conn, req.tenant_id, vectors[0],
                k=settings.retrieval_dense_k, valid_at=valid_at, known_at=known_at,
            )
        _record("dense", results, arm_started)
        return "dense", results

    outcomes = await asyncio.gather(
        *(lexical(lang) for lang in langs), dense(), return_exceptions=True
    )

    arms = {}
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            # One arm failing degrades recall; both failing yields no chunks, and
            # the gateway turns that into abstain(out_of_corpus). Never a 500 —
            # an abstention is a correct answer, an error is not.
            log.warning("retrieval arm failed", exc_info=outcome)
            continue
        name, results = outcome
        if results:
            arms[name] = results

    fused = reciprocal_rank_fusion(arms, k=settings.retrieval_rrf_k)
    top_n = req.top_n or settings.retrieval_top_n
    reranked = app.state.reranker.rerank(req.query, fused, top_n=top_n)

    return RetrieveResponse(
        chunks=[
            Chunk(
                chunk_id=f.candidate.chunk_id,
                doc_id=f.candidate.doc_id,
                version=f.candidate.version,
                title=f.candidate.title,
                text=f.candidate.text,
                score=round(f.score, 6),
            )
            for f in reranked
        ],
        k=k,
        reranked_to=len(reranked),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _record(arm: str, results: list, started: float) -> None:
    registry.observe(METRIC_ARM_SECONDS, time.perf_counter() - started, arm=arm)
    registry.inc(METRIC_ARM_HITS, value=len(results), arm=arm)
