"""Tests for the Learn-then-Test threshold selection seed."""

from __future__ import annotations

import math

import pytest

from aletheia.risk.ltt import LambdaCandidate, binomial_tail, guarantee_text, pvalue, select


def _reference_tail(n: int, k: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


@pytest.mark.parametrize(
    ("n", "k", "p"),
    [(10, 0, 0.05), (10, 3, 0.05), (50, 2, 0.1), (100, 7, 0.05), (200, 20, 0.15)],
)
def test_binomial_tail_matches_direct_summation(n: int, k: int, p: float) -> None:
    assert binomial_tail(n, k, p) == pytest.approx(_reference_tail(n, k, p), rel=1e-9)


def test_binomial_tail_edges() -> None:
    assert binomial_tail(10, -1, 0.5) == 0.0
    assert binomial_tail(10, 10, 0.5) == 1.0
    assert binomial_tail(10, 20, 0.5) == 1.0
    assert binomial_tail(10, 3, 0.0) == 1.0
    assert binomial_tail(10, 3, 1.0) == 0.0
    # Large n must not overflow the binomial coefficient.
    assert 0.0 <= binomial_tail(20_000, 500, 0.05) <= 1.0


def test_binomial_tail_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        binomial_tail(-1, 0, 0.5)
    with pytest.raises(ValueError):
        binomial_tail(10, 0, 1.5)


def test_pvalue_decreases_as_observed_failures_decrease() -> None:
    """Fewer observed failures is stronger evidence that the true risk is below α."""
    n, alpha = 500, 0.05
    assert pvalue(n, 5, alpha) < pvalue(n, 15, alpha) < pvalue(n, 25, alpha)


def test_select_prefers_the_highest_coverage_certified_threshold() -> None:
    n, alpha = 1000, 0.05
    candidates = [
        # Very conservative: almost no failures, but answers little.
        LambdaCandidate(value=0.1, failures=2, answered=300),
        # The sweet spot: still comfortably certified, answers far more.
        LambdaCandidate(value=0.3, failures=20, answered=800),
        # Too permissive: failure count is consistent with a risk above α.
        LambdaCandidate(value=0.9, failures=90, answered=1000),
    ]
    result = select(candidates, n=n, alpha=alpha, delta=0.05)

    assert result.certified
    assert result.lambda_value == 0.3
    assert result.coverage == pytest.approx(0.8)
    assert result.empirical_risk == pytest.approx(0.02)


def test_select_reports_failure_rather_than_shipping_the_least_bad_threshold() -> None:
    """If nothing is certified, the honest outcome is to abstain on everything."""
    candidates = [
        LambdaCandidate(value=0.5, failures=80, answered=1000),
        LambdaCandidate(value=0.9, failures=120, answered=1000),
    ]
    result = select(candidates, n=1000, alpha=0.05, delta=0.05)

    assert not result.certified
    assert result.coverage == 0.0
    assert "no guarantee" in guarantee_text(result, "cal_x")


def test_select_applies_a_multiplicity_correction() -> None:
    """A borderline λ that passes alone must not pass inside a large grid.

    Without the correction, testing more thresholds would make certification
    easier — exactly backwards.
    """
    n, alpha = 200, 0.05
    borderline = LambdaCandidate(value=0.4, failures=4, answered=150)

    alone = select([borderline], n=n, alpha=alpha, delta=0.05)
    padding = [
        LambdaCandidate(value=0.4 + 0.001 * i, failures=199, answered=200) for i in range(1, 400)
    ]
    in_grid = select([borderline, *padding], n=n, alpha=alpha, delta=0.05)

    assert alone.certified
    assert not in_grid.certified


def test_select_validates_its_arguments() -> None:
    good = [LambdaCandidate(value=0.3, failures=1, answered=10)]
    for kwargs in (
        {"candidates": [], "n": 10, "alpha": 0.05},
        {"candidates": good, "n": 0, "alpha": 0.05},
        {"candidates": good, "n": 10, "alpha": 0.0},
        {"candidates": good, "n": 10, "alpha": 1.0},
    ):
        with pytest.raises(ValueError):
            select(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        select(good, n=10, alpha=0.05, delta=0.0)


def test_guarantee_text_names_the_calibration_run() -> None:
    result = select(
        [LambdaCandidate(value=0.3, failures=5, answered=900)], n=1000, alpha=0.05, delta=0.05
    )
    text = guarantee_text(result, "cal_2026_07_tr")

    assert "0.05" in text
    assert "95% confidence" in text
    # A bound without provenance is a marketing claim (ADR-0004).
    assert "cal_2026_07_tr" in text
