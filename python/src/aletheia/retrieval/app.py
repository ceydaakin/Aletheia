"""Retrieval service — hybrid search over the bitemporal chunk store.

**Scaffold.** Returns deterministic placeholder chunks so the pipeline runs end to
end. The real implementation lands in week 3:

* BM25 via Postgres full-text search, with a Turkish-aware analyzer.
* Dense retrieval via pgvector HNSW over a multilingual embedding model.
* Reciprocal Rank Fusion over both lists, then a cross-encoder rerank.
* An ``as_of`` predicate selecting the valid-time slice (ADR-0002).
"""

from __future__ import annotations

import time

from fastapi import Request

from aletheia.contracts import Chunk, RetrieveRequest, RetrieveResponse
from aletheia.service import create_app

app = create_app("retrieval")

# Placeholder corpus. Deliberately small and obviously fake: an empty result must
# be reachable so that the gateway's out_of_corpus path is exercised in dev.
_STUB_CORPUS: list[Chunk] = [
    Chunk(
        chunk_id="doc_412:v3:chunk_18",
        doc_id="doc_412",
        version=3,
        title="Service Agreement (rev. 3)",
        text=(
            "Either party may terminate this agreement by giving thirty (30) days "
            "written notice to the other party."
        ),
        score=0.91,
    ),
    Chunk(
        chunk_id="doc_412:v3:chunk_19",
        doc_id="doc_412",
        version=3,
        title="Service Agreement (rev. 3)",
        text=(
            "The notice requirements in this section apply to agreements executed "
            "after 1 January 2024."
        ),
        score=0.74,
    ),
    Chunk(
        chunk_id="doc_907:v1:chunk_03",
        doc_id="doc_907",
        version=1,
        title="Data Protection Notice",
        text=(
            "Personal data is retained for the period required by applicable law and "
            "deleted thereafter."
        ),
        score=0.41,
    ),
]


@app.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, request: Request) -> RetrieveResponse:
    started = time.perf_counter()

    # Crude lexical filter, purely so that an out-of-corpus query actually
    # returns nothing rather than always returning the same three chunks.
    terms = {t.strip(".,;:?!").lower() for t in req.query.split() if len(t) > 3}
    hits = [c for c in _STUB_CORPUS if terms & {w.strip(".,;:?!").lower() for w in c.text.split()}]

    reranked = hits[: min(len(hits), 6)]
    return RetrieveResponse(
        chunks=reranked,
        k=req.k,
        reranked_to=len(reranked),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
