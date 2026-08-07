"""Learn-then-Test threshold selection.

This is the seed of the week-7 work, present now because the rest of the system
is designed around its interface. It implements the exact form of the procedure,
which is valid for the loss we actually use:

The controlled loss is **binary per response** — "does the response we would
return at threshold λ contain at least one unsupported claim?" (ADR-0004).

The risk is **selective**: it is conditioned on having answered.

    R(λ) = P(response contains an unsupported claim | we answered at λ)

Not the marginal ``P(loss and answered)``. The difference is not cosmetic — the
marginal is always the smaller number, because abstentions dilute it — and the
selective form is the one that matches what a user reads. Someone who receives an
answer wants to know the chance *that answer* is wrong; the fact that the system
declined ten other questions does not make the answer they hold any safer. Testing
the marginal while reporting the selective is a bound that is arithmetically valid
and empirically false, which is exactly what this module measured before the
distinction was made explicit: held-out risk exceeded α in 56% of certified runs.

Conditioning on the answered count makes the test conditional on the selection
event. That is the standard treatment for selective risk and is what makes the
exact binomial applicable: given ``m`` answered responses, the failure count is
Binomial(m, R(λ)), so the p-value for

    H_λ :  R(λ) > α

is the exact binomial tail ``P(Bin(m, α) <= k)`` — no concentration inequality
needed, and no slack given away. Rejecting H_λ certifies λ. Testing a grid of λ
requires a multiplicity correction; Bonferroni at level δ/|Λ| is used here
because it is valid under arbitrary dependence between the tests, and the tests
across a λ grid are very much dependent.

What week 7 adds is not the maths but the evidence: a real calibration set, the
choice of confidence statistic (PRD open question 1), and the cross-lingual
transfer experiment.

Not yet handled — and the technical report must say so: the calibration labels
are themselves produced by a noisy verifier, so the guarantee below is
conditional on verifier accuracy (PRD open question 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, exp, lgamma, log, log1p


def binomial_tail(n: int, k: int, p: float) -> float:
    """Return ``P(X <= k)`` for ``X ~ Binomial(n, p)``.

    Computed in log space so that large ``n`` does not overflow the binomial
    coefficient.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must lie in [0, 1]")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0

    log_p, log_q = log(p), log1p(-p)
    total = 0.0
    for i in range(k + 1):
        log_term = (
            lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        )
        total += exp(log_term)
    return min(total, 1.0)


def pvalue(n: int, failures: int, alpha: float) -> float:
    """p-value for ``H_λ: R(λ) > α`` given ``failures`` losses out of ``n``.

    Small p means the observed failure count is too low to be consistent with a
    true risk above α, so H_λ is rejected and λ is certified.
    """
    return binomial_tail(n, failures, alpha)


@dataclass(frozen=True)
class LambdaCandidate:
    """One point on the threshold grid, evaluated on the calibration set."""

    value: float
    """The threshold itself: the maximum risk statistic we are willing to answer at."""
    failures: int
    """Calibration responses at this λ that contained an unsupported claim."""
    answered: int
    """Calibration responses we would have answered at this λ."""


@dataclass(frozen=True)
class Selection:
    lambda_value: float
    alpha: float
    delta: float
    n: int
    coverage: float
    """Fraction of the calibration set we would answer at the selected λ."""
    empirical_risk: float
    certified: bool
    """False means no λ on the grid could be certified — the honest outcome is to
    abstain on everything, not to quietly ship the least-bad threshold."""


def select(
    candidates: Sequence[LambdaCandidate],
    n: int,
    alpha: float,
    delta: float = 0.05,
) -> Selection:
    """Choose the λ with the highest coverage among those certified at level δ.

    Args:
        candidates: the λ grid, each evaluated on the same calibration set.
        n: calibration set size.
        alpha: tolerated risk.
        delta: error probability for the certification (confidence is 1 − δ).
    """
    if not candidates:
        raise ValueError("candidate grid is empty")
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie in (0, 1)")
    if not 0.0 < delta < 1.0:
        raise ValueError("delta must lie in (0, 1)")

    # Bonferroni across the grid: valid under arbitrary dependence between tests.
    level = delta / len(candidates)

    # Tested against the number *answered*, not the calibration set size: the
    # controlled quantity is the selective risk. Using n here would certify the
    # marginal risk, which abstentions make smaller — and would therefore issue a
    # bound that held-out data does not honour.
    certified = [
        c for c in candidates if c.answered > 0 and pvalue(c.answered, c.failures, alpha) <= level
    ]
    if not certified:
        # No threshold clears the bar. Answering anyway would mean quoting a
        # guarantee we did not earn.
        return Selection(
            lambda_value=0.0,
            alpha=alpha,
            delta=delta,
            n=n,
            coverage=0.0,
            empirical_risk=1.0,
            certified=False,
        )

    # Among valid thresholds, take the one that answers the most questions:
    # the guarantee is the constraint, coverage is the objective (PRD G2).
    best = max(certified, key=lambda c: (c.answered, c.value))
    return Selection(
        lambda_value=best.value,
        alpha=alpha,
        delta=delta,
        n=n,
        coverage=best.answered / n,
        # Selective, matching what was certified: failures among answered.
        empirical_risk=best.failures / best.answered if best.answered else 0.0,
        certified=True,
    )


def minimum_certifiable_n(alpha: float, delta: float, grid_size: int) -> int:
    """Smallest answered count that could certify a threshold, even flawlessly.

    A run with zero observed failures has p = (1−α)^m, and Bonferroni requires
    p ≤ δ/|Λ|. Solving for m gives a floor that no amount of system quality can
    get under — at α=0.05, δ=0.05 and a 50-point grid it is 135 responses.

    Worth reporting when certification fails, because "the verifier is bad" and
    "the calibration set is too small to say anything" look identical from the
    outside and have completely different fixes.
    """
    if not 0.0 < alpha < 1.0 or not 0.0 < delta < 1.0 or grid_size < 1:
        raise ValueError("alpha and delta must lie in (0, 1) and grid_size must be positive")
    return ceil(log(delta / grid_size) / log(1 - alpha))


def guarantee_text(selection: Selection, calibration_id: str) -> str:
    """Render the guarantee exactly as it appears in the API response.

    It always names the calibration run: a bound without the provenance of the
    calibration that produced it is a marketing claim (ADR-0004).
    """
    if not selection.certified:
        return "no guarantee could be certified on the current calibration set"
    confidence = round((1 - selection.delta) * 100)
    return (
        f"P(unsupported_claim) <= {selection.alpha:g} "
        f"with {confidence}% confidence, calibration_id={calibration_id}"
    )
