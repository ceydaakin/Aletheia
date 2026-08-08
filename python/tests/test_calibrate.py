"""Calibration mechanics, and a synthetic check that the bound actually holds.

The Learn-then-Test maths is tested in test_ltt.py. What is tested here is the
layer that turns labelled responses into a grid and a threshold — where an
off-by-one or a leaked split would produce a bound that is arithmetically valid
and empirically false.

The last test is the one that matters most: on data whose true risk is known by
construction, does a threshold certified at alpha actually keep held-out risk at
or below alpha? If that ever fails, everything downstream is decoration.
"""

from __future__ import annotations

import random

import pytest

from aletheia.eval.calibrate import (
    GRID,
    Observation,
    candidates,
    evaluate_at,
    risk_coverage_curve,
    split,
)
from aletheia.risk import ltt


def obs(statistic: float, loss: bool, *, answered: bool = True, ident: str = "q") -> Observation:
    return Observation(
        query_id=ident, query="q", answer="a",
        statistic=statistic, loss=loss, answered=answered,
    )


# --- Grid construction -----------------------------------------------------


def test_a_threshold_only_counts_responses_it_would_answer() -> None:
    """Abstention carries no loss — declining to answer is never wrong.

    Counting abstentions as failures would make every threshold look terrible;
    counting them as successes would make a threshold of zero look perfect.
    """
    observations = [obs(0.1, loss=False), obs(0.8, loss=True)]
    low, high = candidates(observations, grid=(0.5, 0.9))

    assert (low.answered, low.failures) == (1, 0)
    assert (high.answered, high.failures) == (2, 1)


def test_unanswered_responses_are_excluded_entirely() -> None:
    """A pipeline that produced nothing is not an abstention decision — there was
    never a response to gate."""
    observations = [obs(0.1, loss=False), obs(1.0, loss=False, answered=False)]
    (candidate,) = candidates(observations, grid=(1.0,))

    assert candidate.answered == 1


def test_coverage_is_monotone_in_the_threshold() -> None:
    """A larger threshold answers a superset. If this breaks, the grid search is
    optimising over something that is not a threshold."""
    observations = [obs(i / 20, loss=False) for i in range(20)]
    answered = [c.answered for c in candidates(observations)]

    assert answered == sorted(answered)


# --- Held-out evaluation ---------------------------------------------------


def test_evaluate_at_reports_risk_among_answered_only() -> None:
    observations = [obs(0.1, loss=False), obs(0.2, loss=True), obs(0.9, loss=True)]
    result = evaluate_at(observations, 0.5)

    assert result["answer_rate"] == pytest.approx(2 / 3)
    # One of the two answered responses failed — the third was abstained on.
    assert result["empirical_risk"] == pytest.approx(0.5)


def test_evaluate_at_with_nothing_answered() -> None:
    result = evaluate_at([obs(0.9, loss=True)], 0.1)

    assert result["answer_rate"] == 0.0
    # No answers means no opportunity to be wrong, not a 100% failure rate.
    assert result["empirical_risk"] == 0.0


def test_risk_coverage_curve_covers_the_grid() -> None:
    curve = risk_coverage_curve([obs(0.5, loss=False)])
    assert len(curve) == len(GRID)
    assert {"threshold", "answer_rate", "empirical_risk", "n", "failures"} <= set(curve[0])


# --- Splitting -------------------------------------------------------------


def test_split_is_disjoint_and_complete() -> None:
    observations = [obs(i / 10, loss=False, ident=f"q{i}") for i in range(10)]
    calibration, test = split(observations)

    assert len(calibration) + len(test) == len(observations)
    assert not {o.query_id for o in calibration} & {o.query_id for o in test}


def test_split_is_deterministic() -> None:
    """No seed to record and no shuffle to reproduce (PRD G6)."""
    observations = [obs(i / 10, loss=False, ident=f"q{i}") for i in range(10)]
    assert split(observations)[0] == split(observations)[0]


# --- The bound actually holds ----------------------------------------------


def synthetic(n: int, *, true_risk: float, rng: random.Random) -> list[Observation]:
    """Responses whose statistic is informative about their loss.

    Low-statistic responses are mostly correct and high-statistic ones mostly
    wrong, which is what a working verifier produces. The overall failure rate is
    ``true_risk``.
    """
    observations = []
    for i in range(n):
        statistic = rng.random()
        # P(loss) rises with the statistic; scaled so the mean is true_risk.
        p = min(1.0, 2 * true_risk * statistic)
        observations.append(obs(statistic, loss=rng.random() < p, ident=f"q{i}"))
    return observations


def _violation_rate(correction: str, *, runs: int, n: int, alpha: float, delta: float) -> tuple[int, float]:
    rng = random.Random(20260808)
    certified_runs = 0
    violations = 0
    for _ in range(runs):
        observations = synthetic(n, true_risk=0.25, rng=rng)
        calibration_set, test_set = split(observations)
        selection = ltt.select(
            candidates(calibration_set), n=len(calibration_set),
            alpha=alpha, delta=delta, correction=correction,
        )
        if not selection.certified:
            continue
        certified_runs += 1
        if evaluate_at(test_set, selection.lambda_value)["empirical_risk"] > alpha:
            violations += 1
    return certified_runs, (violations / certified_runs if certified_runs else 0.0)


def test_fixed_sequence_certifies_nothing_on_this_grid() -> None:
    """Documents a negative result so it is not rediscovered.

    Fixed-sequence testing needs its first hypothesis to be the easiest to reject.
    Ascending λ orders by risk, but power comes from sample size, and the smallest
    threshold answers almost nothing — so the walk stops at step one having
    certified nothing. On paper it would more than halve the calibration set
    needed; in practice it certifies strictly less than Bonferroni here.
    """
    fixed_runs, _ = _violation_rate(ltt.FIXED_SEQUENCE, runs=60, n=1200, alpha=0.10, delta=0.05)
    bonferroni_runs, _ = _violation_rate(ltt.BONFERRONI, runs=60, n=1200, alpha=0.10, delta=0.05)

    assert fixed_runs == 0
    assert bonferroni_runs > 10, (
        "if Bonferroni also stopped certifying, this test is measuring sample size "
        "rather than the correction"
    )


def test_minimum_n_reflects_the_correction() -> None:
    assert ltt.minimum_certifiable_n(0.05, 0.05, 50, ltt.FIXED_SEQUENCE) == 59
    assert ltt.minimum_certifiable_n(0.05, 0.05, 50, ltt.BONFERRONI) == 135


def test_certified_threshold_bounds_held_out_risk() -> None:
    """The end-to-end claim, on data where the truth is known by construction.

    Runs the whole select-then-evaluate loop many times on fresh samples and
    checks that certification is honest: among runs that certified a threshold,
    held-out risk should exceed alpha in at most a small fraction — far below the
    1-delta confidence the procedure claims.

    This is the test that would catch a leaked split, an off-by-one in the grid,
    or a missing multiplicity correction. All three produce a bound that looks
    fine until measured.
    """
    # n is large because Bonferroni over a 50-point grid is demanding: certifying
    # at delta/50 needs the observed failure rate several sigma below alpha, which
    # a few hundred responses cannot supply. That is a real property of the
    # procedure and the reason a 24-query dataset can never produce a bound.
    alpha, delta = 0.10, 0.05
    rng = random.Random(20260807)

    certified_runs = 0
    violations = 0
    for _ in range(60):
        observations = synthetic(4000, true_risk=0.25, rng=rng)
        calibration_set, test_set = split(observations)

        selection = ltt.select(
            candidates(calibration_set), n=len(calibration_set), alpha=alpha,
            delta=delta, correction=ltt.BONFERRONI,
        )
        if not selection.certified:
            continue
        certified_runs += 1
        if evaluate_at(test_set, selection.lambda_value)["empirical_risk"] > alpha:
            violations += 1

    assert certified_runs > 30, "the procedure certified almost nothing; not a real test"
    rate = violations / certified_runs
    # Generous versus the nominal delta=0.05: held-out risk is itself a noisy
    # estimate on n=300, so exceeding alpha occasionally is sampling noise rather
    # than a broken bound. A rate near 0.5 would mean the threshold is not
    # controlling anything.
    assert rate < 0.20, f"held-out risk exceeded alpha in {rate:.0%} of certified runs"


def test_nothing_is_certified_when_every_response_fails() -> None:
    """The honest outcome is to abstain on everything, not to ship a threshold."""
    observations = [obs(i / 100, loss=True, ident=f"q{i}") for i in range(100)]
    selection = ltt.select(candidates(observations), n=len(observations), alpha=0.05)

    assert not selection.certified


def test_a_perfect_system_certifies_the_widest_threshold() -> None:
    """n=300 rather than 100 on purpose.

    With a 50-point grid the Bonferroni level is delta/50 = 0.001, and a flawless
    run of n responses gives p = (1-alpha)^n. That only clears 0.001 past n ≈ 135,
    so a smaller sample certifies nothing however perfect it is. Refusing to
    certify there is correct behaviour, not a defect — and it is worth knowing
    that a 24-query dataset can never produce a bound at alpha=0.05.
    """
    observations = [obs(i / 300, loss=False, ident=f"q{i}") for i in range(300)]
    selection = ltt.select(candidates(observations), n=len(observations), alpha=0.05)

    assert selection.certified
    assert selection.lambda_value == max(GRID)
    assert selection.coverage == pytest.approx(1.0)


def test_a_sample_too_small_to_certify_says_so() -> None:
    """The bound needs a minimum n before it can exist at all."""
    observations = [obs(i / 100, loss=False, ident=f"q{i}") for i in range(100)]
    selection = ltt.select(candidates(observations), n=len(observations), alpha=0.05)

    assert not selection.certified
