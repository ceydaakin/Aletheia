"""Hybrid search: lexical, dense, and Reciprocal Rank Fusion (ADR-0006).

Two invariants run through everything here.

**Filtering happens before ranking.** Tenant and both temporal intervals are SQL
predicates, not a post-filter over a top-k. Post-filtering silently shrinks the
result set whenever superseded versions crowd out current ones, and that is a
recall bug that presents as a corpus gap.

**Recall is the metric that matters.** A chunk retrieval misses cannot be
recovered by anything downstream — the reranker and the verifier can only discard.
Hence wide arms fused narrow, rather than narrow arms.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from psycopg import AsyncConnection

log = logging.getLogger("retrieval.search")

# Text search configurations built by migration 0003.
_TS_CONFIG = {"tr": "turkish_unaccent", "en": "english_unaccent"}
_DEFAULT_TS_CONFIG = "simple"

# RRF's smoothing constant, from Cormack et al. Results are famously insensitive
# to it; it is here as a named constant rather than tuned.
RRF_K = 60

_COLUMNS = (
    "c.chunk_id, c.doc_id, c.version, d.title, c.text, c.lang, c.span_start, c.span_end"
)

# Title lives on the document, not the chunk. The join is on the documents primary
# key, so it costs an index lookup per candidate rather than a scan.
_DOCUMENT_JOIN = """
    JOIN documents d
      ON d.tenant_id = c.tenant_id AND d.doc_id = c.doc_id AND d.version = c.version
"""

# Applied to both arms. Written once so the two cannot drift apart — an arm that
# forgets the transaction-time predicate would return chunks a correction retired.
#
# Both bounds COALESCE to the *database's* now(), never the application's. That is
# not defensive style: recorded_at is written by the database, so comparing it
# against a client clock makes the result depend on the skew between two machines.
# A client running a fraction of a second behind cannot see rows it just wrote —
# observed on this project's own dev setup with sub-second skew, which presents as
# an empty result set rather than as an error.
_TEMPORAL_PREDICATE = """
    c.tenant_id = %(tenant_id)s
    AND c.valid_from  <= COALESCE(%(valid_at)s::timestamptz, now())
    AND COALESCE(%(valid_at)s::timestamptz, now()) < c.valid_to
    AND c.recorded_at <= COALESCE(%(known_at)s::timestamptz, now())
    AND COALESCE(%(known_at)s::timestamptz, now()) < c.superseded_at
"""


def ts_config(lang: str) -> str:
    """Text search configuration for a language code."""
    return _TS_CONFIG.get((lang or "")[:2].lower(), _DEFAULT_TS_CONFIG)


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    doc_id: str
    version: int
    title: str
    text: str
    lang: str
    span_start: int = 0
    span_end: int = 0
    score: float = 0.0
    """The arm's native score. Kept for debugging only: RRF uses rank, never this
    (ADR-0006 — the two arms' scores are not comparable)."""


@dataclass
class Fused:
    candidate: Candidate
    score: float
    """RRF score: the sum of 1/(k+rank) over the arms that returned this chunk."""
    ranks: dict[str, int] = field(default_factory=dict)
    """Rank per arm, for the ablation table and for debugging a surprising result."""


def _row_to_candidate(row: dict, score_key: str) -> Candidate:
    return Candidate(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        version=int(row["version"]),
        title=row.get("title") or "",
        text=row["text"],
        lang=row.get("lang") or "",
        span_start=int(row.get("span_start") or 0),
        span_end=int(row.get("span_end") or 0),
        score=float(row.get(score_key) or 0.0),
    )


async def tenant_languages(conn: AsyncConnection, tenant_id: str) -> list[str]:
    """Languages present in a tenant's corpus.

    Read from ``documents`` rather than ``chunks``: same answer, orders of
    magnitude fewer rows. Used to decide which lexical configurations to run when
    the caller does not say what language the query is in.
    """
    cur = await conn.execute(
        "SELECT DISTINCT lang FROM documents WHERE tenant_id = %s AND lang <> ''",
        (tenant_id,),
    )
    return sorted(row["lang"] for row in await cur.fetchall())


async def lexical_search(
    conn: AsyncConnection,
    tenant_id: str,
    query: str,
    *,
    k: int,
    valid_at: datetime | None = None,
    known_at: datetime | None = None,
    lang: str = "",
) -> list[Candidate]:
    """BM25-style ranking over the language-aware tsvector.

    ``query_tsquery`` (migration 0003) rather than ``websearch_to_tsquery``: the
    built-in parsers join terms with AND, which requires the chunk to contain
    every word of the question. Questions are longer than the passages that answer
    them, so that puts a floor on recall near zero — measured at 0.02 on
    bootstrap-tr. Disjunctive matching plus ts_rank_cd is what BM25 actually does.
    """
    config = ts_config(lang)
    cur = await conn.execute(
        f"""
        WITH q AS (SELECT query_tsquery(%(config)s::regconfig, %(query)s) AS tsq)
        SELECT {_COLUMNS}, ts_rank_cd(c.tsv, q.tsq) AS score
        FROM chunks c
        {_DOCUMENT_JOIN}
        CROSS JOIN q
        WHERE {_TEMPORAL_PREDICATE}
          AND c.tsv @@ q.tsq
        ORDER BY score DESC, c.chunk_id
        LIMIT %(k)s
        """,
        {
            "config": config,
            "query": query,
            "tenant_id": tenant_id,
            "valid_at": valid_at,
            "known_at": known_at,
            "k": k,
        },
    )
    return [_row_to_candidate(row, "score") for row in await cur.fetchall()]


async def dense_search(
    conn: AsyncConnection,
    tenant_id: str,
    embedding: Sequence[float],
    *,
    k: int,
    valid_at: datetime | None = None,
    known_at: datetime | None = None,
) -> list[Candidate]:
    """Cosine nearest neighbours over the pgvector HNSW index.

    ``embedding IS NOT NULL`` is not defensive clutter: chunks ingested with the
    null backend have no vector, and without the predicate they would sort last
    rather than be excluded — padding the result list with unranked chunks
    (ADR-0006).
    """
    vector = "[" + ",".join(f"{v:.6g}" for v in embedding) + "]"
    cur = await conn.execute(
        f"""
        SELECT {_COLUMNS}, 1 - (c.embedding <=> %(vector)s::vector) AS score
        FROM chunks c
        {_DOCUMENT_JOIN}
        WHERE {_TEMPORAL_PREDICATE}
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> %(vector)s::vector, c.chunk_id
        LIMIT %(k)s
        """,
        {
            "vector": vector,
            "tenant_id": tenant_id,
            "valid_at": valid_at,
            "known_at": known_at,
            "k": k,
        },
    )
    return [_row_to_candidate(row, "score") for row in await cur.fetchall()]


def reciprocal_rank_fusion(
    arms: dict[str, Sequence[Candidate]],
    *,
    k: int = RRF_K,
    limit: int | None = None,
) -> list[Fused]:
    """Fuse ranked lists by ``Σ 1 / (k + rank)``.

    Rank-based rather than score-based on purpose: BM25 scores are unbounded and
    corpus-dependent while cosine similarities are bounded, so any weighted sum
    needs a normalisation with a free parameter that has to be retuned whenever
    the corpus shifts — which is exactly what this project cannot rely on
    staying still (ADR-0006).

    Handles any number of arms, including one. With a single list this is that
    list's own ranking, which is what makes lexical-only operation work unchanged
    when no embeddings exist.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")

    fused: dict[str, Fused] = {}
    for arm, candidates in arms.items():
        for rank, candidate in enumerate(candidates, start=1):
            entry = fused.get(candidate.chunk_id)
            if entry is None:
                entry = Fused(candidate=candidate, score=0.0)
                fused[candidate.chunk_id] = entry
            entry.score += 1.0 / (k + rank)
            entry.ranks[arm] = rank

    # chunk_id breaks ties so that two runs over the same corpus produce the same
    # ordering — eval reproducibility depends on it (PRD G6).
    ordered = sorted(fused.values(), key=lambda f: (-f.score, f.candidate.chunk_id))
    return ordered[:limit] if limit is not None else ordered


def unique_langs(requested: str, available: Iterable[str]) -> list[str]:
    """Which lexical configurations to run.

    An explicit language wins. Otherwise every language in the corpus gets its own
    arm and RRF fuses them — a tenant holding both Turkish and English documents
    would otherwise have one of the two stemmed with the wrong rules, which costs
    exactly the recall this arm exists to provide.
    """
    if requested:
        return [requested]
    configs: list[str] = []
    for lang in available:
        config = ts_config(lang)
        if config not in {ts_config(existing) for existing in configs}:
            configs.append(lang)
    return configs or [""]
