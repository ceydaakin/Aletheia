"""Retrieval metrics, checked against hand-computed values.

Every expected number here is derived by hand in the assertion or its comment.
A metric test that computes the expectation the same way the implementation does
proves only that the code is consistent with itself.
"""

from __future__ import annotations

import math

import pytest

from aletheia.eval.metrics import (
    dcg,
    hit_rate_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarise,
)


def test_recall_counts_relevant_found_not_positions() -> None:
    retrieved = ["a", "b", "c", "d"]
    assert recall_at_k(retrieved, {"a", "z"}, 4) == 0.5
    assert recall_at_k(retrieved, {"a", "b"}, 4) == 1.0
    assert recall_at_k(retrieved, {"z"}, 4) == 0.0


def test_recall_respects_the_cutoff() -> None:
    retrieved = ["x", "x2", "a"]
    assert recall_at_k(retrieved, {"a"}, 2) == 0.0
    assert recall_at_k(retrieved, {"a"}, 3) == 1.0


def test_recall_with_no_relevant_chunks_is_zero_not_one() -> None:
    """A malformed eval row must not silently inflate the average.

    Returning 1.0 for "found everything there was to find" would make an
    unlabelled query look like a perfect result.
    """
    assert recall_at_k(["a"], set(), 10) == 0.0


def test_duplicate_ids_do_not_inflate_recall() -> None:
    assert recall_at_k(["a", "a", "a"], {"a", "b"}, 10) == 0.5


def test_precision() -> None:
    assert precision_at_k(["a", "x", "b", "y"], {"a", "b"}, 4) == 0.5
    assert precision_at_k(["a", "x"], {"a", "b"}, 1) == 1.0
    assert precision_at_k([], {"a"}, 5) == 0.0
    assert precision_at_k(["a"], {"a"}, 0) == 0.0


def test_precision_divides_by_what_was_returned() -> None:
    """With fewer results than k, the denominator is the result count."""
    assert precision_at_k(["a", "x"], {"a"}, 10) == 0.5


def test_reciprocal_rank_uses_the_first_hit() -> None:
    assert reciprocal_rank(["a", "b"], {"a", "b"}) == 1.0
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a"}) == 0.0


def test_dcg_matches_the_definition() -> None:
    # 1/log2(2) + 0/log2(3) + 1/log2(4) = 1 + 0 + 0.5
    assert dcg([1.0, 0.0, 1.0]) == pytest.approx(1.5)


def test_ndcg_is_one_for_a_perfect_ranking() -> None:
    assert ndcg_at_k(["a", "b", "x"], {"a", "b"}, 3) == pytest.approx(1.0)


def test_ndcg_ideal_accounts_for_how_many_relevant_exist() -> None:
    """Two relevant chunks ranked first must score 1.0, not 2/k.

    Normalising by k ones instead of min(len(relevant), k) would make a query with
    few relevant chunks unable to reach 1.0 however perfectly it ranked them.
    """
    assert ndcg_at_k(["a", "b", "x", "y", "z"], {"a", "b"}, 5) == pytest.approx(1.0)


def test_ndcg_penalises_a_later_hit() -> None:
    early = ndcg_at_k(["a", "x", "y"], {"a"}, 3)
    late = ndcg_at_k(["x", "y", "a"], {"a"}, 3)
    assert early == pytest.approx(1.0)
    assert late == pytest.approx(1 / math.log2(4))
    assert late < early


def test_hit_rate_is_not_recall() -> None:
    """One of three gold chunks is a success for the user and a recall of 0.33."""
    retrieved, relevant = ["a", "x"], {"a", "b", "c"}
    assert hit_rate_at_k(retrieved, relevant, 2) == 1.0
    assert recall_at_k(retrieved, relevant, 2) == pytest.approx(1 / 3)


def test_summarise_macro_averages() -> None:
    results = [
        (["a", "x"], {"a"}),   # recall@1 = 1.0
        (["x", "b"], {"b"}),   # recall@1 = 0.0, recall@3 = 1.0
    ]
    summary = summarise(results, ks=(1, 3))

    assert summary["recall@1"] == pytest.approx(0.5)
    assert summary["recall@3"] == pytest.approx(1.0)
    assert summary["mrr"] == pytest.approx((1.0 + 0.5) / 2)
    assert summary["queries"] == 2.0


def test_summarise_weights_queries_equally() -> None:
    """Macro, not micro: a query with many gold chunks must not dominate."""
    many_gold_missed = (["x"], {f"g{i}" for i in range(20)})
    one_gold_found = (["a"], {"a"})
    summary = summarise([many_gold_missed, one_gold_found], ks=(1,))

    assert summary["recall@1"] == pytest.approx(0.5)


def test_summarise_of_nothing_is_empty() -> None:
    assert summarise([]) == {}
