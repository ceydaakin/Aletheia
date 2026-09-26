"""Drift detection: an anytime-valid test that live traffic is still exchangeable
with the calibration set.

The guarantee holds only under exchangeability (report §7.4). Age is a crude
proxy for that having lapsed; this module is the direct test.

**What is tested.** The sequence of risk statistics — the calibration set's,
followed by live traffic's in arrival order — is exchangeable. That is the
hypothesis the bound was derived under, restricted to the one quantity the
controller acts on. It cannot see a shift in P(loss | statistic), because live
traffic has no labels; that limitation is reported, not hidden.

**How.** Conformal test martingales (Vovk et al., *Testing exchangeability
on-line*). For each live statistic, a conformal p-value is its randomised rank
among every statistic seen so far, calibration included. Under
exchangeability these p-values are independent and uniform, so any betting
strategy against them is a test martingale: its wealth has expectation one.
Ville's inequality then gives

    P(wealth ever reaches L) <= 1 / L

under the null, **at every horizon at once**. That is the property a monitor
needs and a repeated KS test does not have: checking on every request never
inflates the false-alarm rate.

**Betting strategy.** Vovk's *Simple Jumper*: three betting functions — bet
that p-values are small (the statistic got worse), large (got better), or not at
all — with a small fraction ``jump`` of wealth redistributed evenly at every
step. A plain product of bets bleeds wealth during a long stable stretch and is
then slow to react to a change that starts late; the redistribution caps that
loss, so the detection delay after a change point stays bounded (tested).

Both directions count as drift. An improvement in the statistic is not harmful
by itself, but it is still evidence that traffic is no longer what the bound
was fitted on, and the bound says nothing about traffic it was not fitted on.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

# Betting functions f(p) = 1 + eps * (p - 1/2): each integrates to one over
# [0, 1], so each is a fair bet under uniform p-values.
EPSILONS = (-1.0, 0.0, 1.0)
DEFAULT_JUMP = 0.01


@dataclass(frozen=True)
class MonitorState:
    n: int
    log_wealth: float
    """log of the martingale's value. Stored in log space so that a long run of
    unremarkable traffic cannot underflow it to zero."""
    weights: tuple[float, ...]
    """Share of wealth currently behind each betting function; sums to one."""
    alarmed: bool

    @property
    def wealth(self) -> float:
        return math.exp(min(self.log_wealth, 700.0))


def initial() -> MonitorState:
    share = 1.0 / len(EPSILONS)
    return MonitorState(n=0, log_wealth=0.0, weights=(share,) * len(EPSILONS), alarmed=False)


def conformal_p(*, greater: int, equal: int, size: int, theta: float) -> float:
    """Randomised conformal p-value of the newest point in a bag of ``size``.

    ``greater`` counts bag members strictly above the new statistic, ``equal``
    those tied with it *including itself*. Large statistics — higher risk —
    get small p-values. The randomisation over ties is what makes the p-values
    exactly uniform and independent; without it they are only conservative,
    and the product of conservative p-values is not guaranteed to be a
    martingale.
    """
    if equal < 1 or greater < 0 or greater + equal > size:
        raise ValueError(f"inconsistent bag: greater={greater} equal={equal} size={size}")
    return (greater + theta * equal) / size


def step(
    state: MonitorState, p: float, *, alarm_level: float, jump: float = DEFAULT_JUMP
) -> MonitorState:
    """Bet on one p-value. Returns a new state; the input is not modified."""
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p-value out of range: {p}")
    share = 1.0 / len(EPSILONS)
    mixed = [(1.0 - jump) * w + jump * share for w in state.weights]
    grown = [w * (1.0 + eps * (p - 0.5)) for w, eps in zip(mixed, EPSILONS, strict=True)]
    factor = sum(grown)
    log_wealth = state.log_wealth + math.log(factor)
    return MonitorState(
        n=state.n + 1,
        log_wealth=log_wealth,
        weights=tuple(g / factor for g in grown),
        # Latched: once the evidence says the bound has lapsed, a quiet stretch
        # afterwards does not restore it. Only a new calibration does.
        alarmed=state.alarmed or log_wealth >= math.log(alarm_level),
    )


def theta(calibration_id: str, n: int) -> float:
    """The tie-breaking uniform for the n-th live statistic of a calibration.

    Derived from a hash rather than drawn from an RNG so that the monitor's
    state is a pure function of the stored statistics: replaying the table
    reproduces the alarm exactly. It is independent of the statistic values,
    which is all the conformal argument requires of it.
    """
    digest = hashlib.sha256(f"{calibration_id}:{n}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64
