"""The statistics the result tables are made of. Pure functions over observations.

Two rules run through all of it:

**Compare at the same answer rate.** A system can always lower its error by
answering less, so a lower risk only means something at matched coverage
(PRD §7.4). Every comparison here is either at a matched operating point or a
whole risk–coverage curve.

**Never evaluate a threshold on the data that chose it.** Every number that
describes what a certified threshold *delivered* comes from a held-out part the
threshold never saw — across many random splits, so the result is a
distribution rather than one lucky partition.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from aletheia.eval.calibrate import GRID, candidates
from aletheia.eval.observations import Observation
from aletheia.risk import ltt


@dataclass(frozen=True)
class Outcome:
    """What one certified threshold delivered on held-out data."""

    certified: bool
    threshold: float
    answer_rate: float
    risk: float
    """Selective risk: failures / answered. Zero when nothing was answered."""
    useful_rate: float
    """Share of all held-out queries answered with a correct, on-evidence claim."""
    answered: int
    unanswerable_answered_rate: float | None
    """Share of held-out *unanswerable* queries that still got an answer."""


def certify(calibration: Sequence[Observation], *, alpha: float, delta: float) -> ltt.Selection:
    return ltt.select(
        candidates(list(calibration)), n=len(calibration), alpha=alpha, delta=delta
    )


def operating_point(observations: Sequence[Observation], threshold: float) -> dict[str, float]:
    total = len(observations)
    answered = [o for o in observations if o.answered and o.statistic <= threshold]
    unanswerable = [o for o in observations if not o.answerable]
    return {
        "answer_rate": len(answered) / total if total else 0.0,
        "risk": sum(o.loss for o in answered) / len(answered) if answered else 0.0,
        "useful_rate": sum(o.useful for o in answered) / total if total else 0.0,
        "answered": float(len(answered)),
        "unanswerable_answered_rate": (
            sum(1 for o in unanswerable if o.answered and o.statistic <= threshold)
            / len(unanswerable)
            if unanswerable
            else math.nan
        ),
    }


def outcome(
    calibration: Sequence[Observation],
    test: Sequence[Observation],
    *,
    alpha: float,
    delta: float,
) -> Outcome:
    selection = certify(calibration, alpha=alpha, delta=delta)
    if not selection.certified:
        # Uncertified means abstain on everything: that is what the service does.
        return Outcome(False, 0.0, 0.0, 0.0, 0.0, 0, 0.0 if any(not o.answerable for o in test) else None)
    point = operating_point(test, selection.lambda_value)
    unanswerable = point["unanswerable_answered_rate"]
    return Outcome(
        certified=True,
        threshold=selection.lambda_value,
        answer_rate=point["answer_rate"],
        risk=point["risk"],
        useful_rate=point["useful_rate"],
        answered=int(point["answered"]),
        unanswerable_answered_rate=None if math.isnan(unanswerable) else unanswerable,
    )


def shuffled_split(
    ids: Sequence[str], fraction: float, rng: random.Random
) -> tuple[set[str], set[str]]:
    """Split query ids, not observations, so that parallel datasets can be split
    identically and a question never sits on both sides in either language."""
    order = sorted(ids)
    rng.shuffle(order)
    cut = round(len(order) * fraction)
    return set(order[:cut]), set(order[cut:])


def _mean_ci(values: Sequence[float]) -> tuple[float, float]:
    """Mean and a normal-approximation 95% half-width over trials."""
    if not values:
        return math.nan, math.nan
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, math.nan
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return mean, 1.96 * math.sqrt(var / len(values))


def summarise(outcomes: Sequence[Outcome], alpha: float) -> dict[str, float]:
    certified = [o for o in outcomes if o.certified]
    risk, risk_ci = _mean_ci([o.risk for o in certified if o.answered])
    unanswerable = [o.unanswerable_answered_rate for o in certified if o.unanswerable_answered_rate is not None]
    return {
        "trials": float(len(outcomes)),
        "certified_rate": len(certified) / len(outcomes) if outcomes else 0.0,
        "mean_threshold": _mean_ci([o.threshold for o in certified])[0],
        # Held-out selective risk, averaged over certified trials: the estimate
        # of the procedure's true risk that G1 is judged on.
        "mean_risk": risk,
        "risk_ci95": risk_ci,
        # How often a single held-out part came out above alpha. Nonzero is
        # expected — a finite test part is noisy — and is reported as such.
        "exceedance_rate": (
            sum(o.risk > alpha for o in certified) / len(certified) if certified else math.nan
        ),
        # Averaged over *all* trials: an uncertified trial answers nothing, and
        # leaving it out would overstate what the system delivers.
        "mean_answer_rate": _mean_ci([o.answer_rate for o in outcomes])[0],
        "mean_useful_rate": _mean_ci([o.useful_rate for o in outcomes])[0],
        "mean_unanswerable_answered_rate": _mean_ci(unanswerable)[0] if unanswerable else math.nan,
    }


def repeated(
    calibration_source: Sequence[Observation],
    test_source: Sequence[Observation] | None = None,
    *,
    alpha: float,
    delta: float,
    fraction: float,
    trials: int,
    seed: int = 0,
) -> dict[str, float]:
    """Certify on one part, evaluate on the rest, ``trials`` times.

    With ``test_source`` given, calibration and evaluation read *different*
    observations of the same query ids — another language, another loss
    definition — split on the same ids so no question is on both sides.
    """
    test_source = calibration_source if test_source is None else test_source
    by_id_cal = {o.query_id: o for o in calibration_source}
    by_id_test = {o.query_id: o for o in test_source}
    ids = sorted(set(by_id_cal) & set(by_id_test))
    if not ids:
        raise ValueError("calibration and test observations share no query ids")

    outcomes = []
    for trial in range(trials):
        cal_ids, test_ids = shuffled_split(ids, fraction, random.Random(seed * 100_003 + trial))
        outcomes.append(
            outcome(
                [by_id_cal[i] for i in sorted(cal_ids)],
                [by_id_test[i] for i in sorted(test_ids)],
                alpha=alpha, delta=delta,
            )
        )
    return summarise(outcomes, alpha)


def risk_coverage(observations: Sequence[Observation]) -> list[tuple[float, float]]:
    """(coverage, selective risk) when answering the k lowest-statistic
    responses, for every k. Coverage is over all queries."""
    total = len(observations)
    answered = sorted((o for o in observations if o.answered), key=lambda o: (o.statistic, o.query_id))
    points, failures = [], 0
    for k, obs in enumerate(answered, start=1):
        failures += obs.loss
        points.append((k / total, failures / k))
    return points


def aurc(observations: Sequence[Observation]) -> float:
    """Area under the risk–coverage curve, normalised by the coverage reached.
    Lower is better; it scores the statistic's ranking independently of any
    threshold."""
    points = risk_coverage(observations)
    return sum(risk for _, risk in points) / len(points) if points else math.nan


def risk_at_coverage(observations: Sequence[Observation], coverage: float) -> float:
    """Selective risk when answering the lowest-statistic ``coverage`` share of
    all queries — the matched-answer-rate comparison."""
    points = risk_coverage(observations)
    if not points:
        return math.nan
    reachable = [p for p in points if p[0] <= coverage + 1e-12]
    return reachable[-1][1] if reachable else points[0][1]


def transfer_test(
    observations: Sequence[Observation], threshold: float, *, alpha: float
) -> dict[str, float]:
    """Is a threshold certified elsewhere safe here? One hypothesis, no grid.

    Testing a *single* pre-chosen threshold needs no multiplicity correction,
    so its sample-size floor is ln(δ)/ln(1−α) — 59 answered responses at
    α=δ=0.05 against the 135 a full Learn-then-Test grid needs. That is the
    proposed correction for cross-lingual transfer: certify in the language
    with data, then *verify* in the new one with less than half the labels.
    """
    answered = [o for o in observations if o.answered and o.statistic <= threshold]
    failures = sum(o.loss for o in answered)
    return {
        "answered": float(len(answered)),
        "failures": float(failures),
        "pvalue": ltt.pvalue(len(answered), failures, alpha) if answered else 1.0,
    }


def transfer_floor(alpha: float, delta: float) -> int:
    return math.ceil(math.log(delta) / math.log(1 - alpha))


def grid_floor(alpha: float, delta: float) -> int:
    return ltt.minimum_certifiable_n(alpha, delta, len(GRID), correction=ltt.BONFERRONI)


def auroc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """P(score of a random positive > random negative), ties counted half.

    Used with corrupted claims as negatives and clean claims as positives: the
    probability the verifier ranks a true claim above a corrupted one.
    """
    if not positives or not negatives:
        return math.nan
    ranked = sorted([(s, 1) for s in positives] + [(s, 0) for s in negatives])
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        mid = (i + j + 1) / 2  # average 1-based rank of the tie block
        rank_sum += mid * sum(label for _, label in ranked[i:j])
        i = j
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
