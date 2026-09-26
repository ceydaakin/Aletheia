"""The result-table statistics, checked where the right answer is known."""

from __future__ import annotations

import math
import random

import pytest

from aletheia.eval import analysis
from aletheia.eval.observations import Observation


def obs(ident: str, statistic: float, loss: bool, *, answerable=True, useful=False, answered=True):
    return Observation(
        query_id=ident, query="?", answer="a", statistic=statistic, loss=loss,
        answered=answered, useful=useful, answerable=answerable,
    )


def synthetic(n: int, seed: int, *, risk_slope: float = 0.6) -> list[Observation]:
    """Loss probability rises with the statistic, so thresholds matter."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        s = rng.random()
        out.append(obs(f"q{i:05d}", s, rng.random() < risk_slope * s, useful=rng.random() < 0.5))
    return out


def test_operating_point() -> None:
    data = [
        obs("a", 0.1, False, useful=True),
        obs("b", 0.2, True),
        obs("c", 0.9, True),
        obs("d", 0.3, False, answerable=False),
    ]
    point = analysis.operating_point(data, 0.5)
    assert point["answer_rate"] == pytest.approx(0.75)
    assert point["risk"] == pytest.approx(1 / 3)
    assert point["useful_rate"] == pytest.approx(0.25)
    assert point["unanswerable_answered_rate"] == pytest.approx(1.0)


def test_uncertified_means_answering_nothing() -> None:
    tiny = [obs(f"q{i}", 0.1, False) for i in range(20)]
    result = analysis.outcome(tiny, tiny, alpha=0.05, delta=0.05)
    assert not result.certified
    assert result.answer_rate == 0.0


def test_repeated_splits_hold_the_bound_on_synthetic_data() -> None:
    """The procedure's held-out risk must sit at or below alpha on average —
    the property G1 is judged on."""
    data = synthetic(3000, seed=1)
    summary = analysis.repeated(data, alpha=0.1, delta=0.05, fraction=0.6, trials=30, seed=2)
    assert summary["certified_rate"] == 1.0
    assert summary["mean_risk"] <= 0.1
    assert 0.0 < summary["mean_answer_rate"] < 1.0


def test_split_is_by_id_and_disjoint() -> None:
    ids = [f"q{i}" for i in range(100)]
    a, b = analysis.shuffled_split(ids, 0.6, random.Random(0))
    assert len(a) == 60 and len(b) == 40
    assert not a & b
    assert a | b == set(ids)


def test_repeated_with_a_different_test_source_uses_matching_ids() -> None:
    source = synthetic(2000, seed=3)
    # Same ids, but every response in the target fails: the transferred
    # threshold must show that as risk 1.
    target = [obs(o.query_id, o.statistic, True) for o in source]
    summary = analysis.repeated(source, target, alpha=0.1, delta=0.05, fraction=0.6, trials=5)
    assert summary["mean_risk"] == pytest.approx(1.0)


def test_repeated_needs_shared_ids() -> None:
    with pytest.raises(ValueError):
        analysis.repeated([obs("a", 0.1, False)], [obs("b", 0.1, False)],
                          alpha=0.1, delta=0.05, fraction=0.5, trials=1)


def test_risk_coverage_and_aurc() -> None:
    data = [obs("a", 0.1, False), obs("b", 0.2, True), obs("c", 0.3, False), obs("x", 1.0, False, answered=False)]
    points = analysis.risk_coverage(data)
    assert points == [(0.25, 0.0), (0.5, 0.5), (0.75, pytest.approx(1 / 3))]
    assert analysis.aurc(data) == pytest.approx((0 + 0.5 + 1 / 3) / 3)


def test_a_perfect_ranking_has_lower_aurc_than_a_random_one() -> None:
    perfect = [obs(f"p{i}", i / 100, i >= 80) for i in range(100)]
    inverted = [obs(f"p{i}", 1 - i / 100, i >= 80) for i in range(100)]
    assert analysis.aurc(perfect) < analysis.aurc(inverted)


def test_risk_at_coverage() -> None:
    data = [obs("a", 0.1, False), obs("b", 0.2, True), obs("c", 0.3, False), obs("d", 0.4, True)]
    assert analysis.risk_at_coverage(data, 0.5) == pytest.approx(0.5)
    assert analysis.risk_at_coverage(data, 0.25) == 0.0


def test_transfer_floor_is_below_the_grid_floor() -> None:
    assert analysis.transfer_floor(0.05, 0.05) == 59
    assert analysis.grid_floor(0.05, 0.05) == 135


def test_transfer_test_rejects_with_enough_clean_answers() -> None:
    clean = [obs(f"q{i}", 0.1, False) for i in range(60)]
    assert analysis.transfer_test(clean, 0.5, alpha=0.05)["pvalue"] <= 0.05
    few = clean[:40]
    assert analysis.transfer_test(few, 0.5, alpha=0.05)["pvalue"] > 0.05


def test_auroc() -> None:
    assert analysis.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert analysis.auroc([0.1, 0.2], [0.9, 0.8]) == 0.0
    assert analysis.auroc([0.5], [0.5]) == 0.5
    assert math.isnan(analysis.auroc([], [0.1]))
