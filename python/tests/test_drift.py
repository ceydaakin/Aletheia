"""Drift detection: the test that decides when the guarantee has lapsed.

The two properties that matter are the two ways it can fail. A monitor that
alarms under exchangeability takes a working system offline for nothing; one
that never alarms under a real shift keeps quoting a bound that no longer holds.
Both are checked by simulation where the truth is known, the same way the bound
itself is validated (report §5.4).
"""

from __future__ import annotations

import math
import random

import pytest

from aletheia.risk import drift


def run_monitor(reference: list[float], live: list[float], *, alarm_level: float, seed: int):
    """Feed live statistics through the monitor exactly as the service does:
    each p-value is computed against the calibration statistics plus every live
    statistic seen so far, the new one included."""
    rng = random.Random(seed)
    bag = sorted(reference)
    state = drift.initial()
    for index, value in enumerate(live):
        bag.append(value)
        greater = sum(1 for b in bag if b > value)
        equal = sum(1 for b in bag if b == value)
        p = drift.conformal_p(greater=greater, equal=equal, size=len(bag), theta=rng.random())
        state = drift.step(state, p, alarm_level=alarm_level)
        if state.alarmed:
            return state, index + 1
    return state, None


def test_conformal_p_bounds() -> None:
    assert drift.conformal_p(greater=0, equal=1, size=10, theta=0.0) == 0.0
    assert drift.conformal_p(greater=9, equal=1, size=10, theta=1.0) == 1.0
    assert 0.0 < drift.conformal_p(greater=4, equal=1, size=10, theta=0.5) < 1.0


def test_conformal_p_rejects_an_impossible_bag() -> None:
    with pytest.raises(ValueError):
        drift.conformal_p(greater=5, equal=0, size=10, theta=0.5)
    with pytest.raises(ValueError):
        drift.conformal_p(greater=8, equal=3, size=10, theta=0.5)


def test_step_is_pure() -> None:
    state = drift.initial()
    after = drift.step(state, 0.01, alarm_level=100)
    assert state == drift.initial()
    assert after.n == 1


def test_wealth_grows_on_small_p_values_and_not_on_typical_ones() -> None:
    """The first bet is neutral — the two directional bets start with equal
    shares and cancel — so wealth moves once one direction has been favoured."""
    small = middling = drift.initial()
    for _ in range(5):
        small = drift.step(small, 0.001, alarm_level=100)
        middling = drift.step(middling, 0.5, alarm_level=100)
    assert small.log_wealth > 0
    assert middling.log_wealth == pytest.approx(0.0, abs=1e-12)


def test_weights_stay_a_distribution() -> None:
    state = drift.initial()
    rng = random.Random(1)
    for _ in range(500):
        state = drift.step(state, rng.random(), alarm_level=1e9)
        assert math.isclose(sum(state.weights), 1.0, rel_tol=1e-9)
        assert all(w > 0 for w in state.weights)


def test_alarm_latches() -> None:
    state = drift.initial()
    for _ in range(200):
        state = drift.step(state, 0.001, alarm_level=100)
    assert state.alarmed
    for _ in range(200):
        state = drift.step(state, 0.9, alarm_level=100)
    assert state.alarmed, "a lapsed guarantee does not come back by itself"


def test_false_alarm_rate_under_exchangeability() -> None:
    """Ville's inequality: P(ever alarming) <= 1/alarm_level, at every horizon.
    At level 20 that is 5%; the simulation must land at or under it, with room
    for sampling noise over 300 runs."""
    alarms = 0
    runs = 300
    for seed in range(runs):
        rng = random.Random(seed)
        reference = [rng.betavariate(2, 5) for _ in range(150)]
        live = [rng.betavariate(2, 5) for _ in range(400)]
        _, when = run_monitor(reference, live, alarm_level=20, seed=seed + 10_000)
        alarms += when is not None
    assert alarms / runs <= 0.08


def test_detects_an_upward_shift_in_risk() -> None:
    """Live traffic whose statistic is systematically worse than calibration —
    the case where continuing to quote the bound is most dangerous."""
    delays = []
    for seed in range(20):
        rng = random.Random(seed)
        reference = [rng.betavariate(2, 5) for _ in range(150)]
        live = [rng.betavariate(5, 2) for _ in range(400)]
        _, when = run_monitor(reference, live, alarm_level=100, seed=seed)
        assert when is not None, f"seed {seed}: shift never detected"
        delays.append(when)
    assert sorted(delays)[len(delays) // 2] <= 60


def test_detects_a_shift_that_starts_late() -> None:
    """A change point after a long exchangeable stretch. A plain mixture
    martingale has bled its wealth by then and reacts slowly; the jumper's
    redistribution is what keeps the delay bounded."""
    rng = random.Random(3)
    reference = [rng.betavariate(2, 5) for _ in range(150)]
    live = [rng.betavariate(2, 5) for _ in range(1000)] + [rng.betavariate(5, 2) for _ in range(300)]
    _, when = run_monitor(reference, live, alarm_level=100, seed=3)
    assert when is not None
    assert when - 1000 <= 120


def test_initial_state_is_neutral() -> None:
    state = drift.initial()
    assert state.n == 0
    assert state.log_wealth == 0.0
    assert not state.alarmed


def test_theta_is_deterministic_and_in_range() -> None:
    a = drift.theta("cal_x", 5)
    assert a == drift.theta("cal_x", 5)
    assert a != drift.theta("cal_x", 6)
    assert 0.0 <= a < 1.0
