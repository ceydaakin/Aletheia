"""Retrieval metrics.

Pure functions over ranked id lists — no database, no service, no I/O — so they
can be tested against hand-computed values rather than against themselves.

All of them take ``retrieved`` in rank order and ``relevant`` as a set. Ties must
already be broken by the caller; these functions do not reorder.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant chunks appearing in the top k.

    The metric that matters most for this system: a chunk retrieval misses cannot
    be recovered by the reranker or the verifier, so a recall failure becomes an
    abstention or an answer built on second-best evidence (ADR-0006).

    Undefined with no relevant chunks; returns 0.0 so that a malformed eval row
    cannot silently inflate an average.
    """
    if not relevant:
        return 0.0
    hits = len(set(retrieved[:k]) & relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if k <= 0 or not retrieved:
        return 0.0
    top = retrieved[:k]
    return len(set(top) & relevant) / len(top)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1/rank of the first relevant chunk, or 0 if none was retrieved."""
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


def dcg(gains: Sequence[float]) -> float:
    """Discounted cumulative gain with the log2(rank+1) discount."""
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-relevance nDCG.

    The ideal ranking puts every relevant chunk first, so the denominator is the
    DCG of ``min(len(relevant), k)`` ones — not of ``k`` ones, which would make a
    query with two relevant chunks unable to score 1.0 however perfectly it ranked.
    """
    if not relevant:
        return 0.0
    gains = [1.0 if chunk_id in relevant else 0.0 for chunk_id in retrieved[:k]]
    ideal = dcg([1.0] * min(len(relevant), k))
    return dcg(gains) / ideal if ideal else 0.0


def hit_rate_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """1.0 if any relevant chunk is in the top k.

    Distinct from recall: for a question answerable from any one of three chunks,
    retrieving one is a success for the user and a recall of 0.33.
    """
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


def summarise(
    results: Sequence[tuple[Sequence[str], set[str]]],
    ks: Sequence[int] = (1, 3, 5, 10, 20),
) -> dict[str, float]:
    """Macro-average every metric over a query set.

    Macro, not micro: every query counts once regardless of how many relevant
    chunks it has, so a single query with twenty gold chunks cannot dominate the
    reported number.
    """
    if not results:
        return {}

    summary: dict[str, float] = {}
    n = len(results)
    for k in ks:
        summary[f"recall@{k}"] = sum(recall_at_k(r, g, k) for r, g in results) / n
        summary[f"hit@{k}"] = sum(hit_rate_at_k(r, g, k) for r, g in results) / n
        summary[f"ndcg@{k}"] = sum(ndcg_at_k(r, g, k) for r, g in results) / n
    summary["mrr"] = sum(reciprocal_rank(r, g) for r, g in results) / n
    summary["queries"] = float(n)
    return summary
