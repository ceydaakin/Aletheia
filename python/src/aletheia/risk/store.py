"""Calibration storage.

A threshold is only meaningful together with the corpus state it was fitted on
and the alpha it certifies, so those travel with it and the response quotes the
run by id (ADR-0004).

Two rules the read path enforces:

* An **uncertified** run is never selected. :func:`aletheia.risk.ltt.select`
  records the case where no lambda on the grid could be certified; that row is
  kept as evidence, not as a threshold.
* A **stale** run is never selected. The guarantee holds only while live traffic
  stays exchangeable with the calibration set (PRD §5.2), and age is the crudest
  available proxy for that having stopped being true.

The sharper signals live here too: :func:`corpus_changes_since` reads
``corpus_events`` for documents that changed after the calibration's corpus
snapshot, and :func:`observe_drift` feeds each live statistic to the conformal
test martingale of :mod:`aletheia.risk.drift`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import AsyncConnection

from aletheia.risk import drift
from aletheia.risk.ltt import Selection

log = logging.getLogger("risk.store")


@dataclass(frozen=True)
class CalibrationRecord:
    calibration_id: str
    tenant_id: str
    alpha: float
    delta: float
    threshold: float
    n: int
    coverage: float
    empirical_risk: float
    certified: bool
    statistic_name: str
    lang: str
    created_at: datetime
    corpus_known_at: datetime | None = None
    loss_source: str = "verifier"
    hallucination_rate: float = 0.0

    def is_stale(self, max_age_hours: int, *, now: datetime) -> bool:
        return now - self.created_at > timedelta(hours=max_age_hours)

    def guarantee(self) -> str:
        """The sentence that appears in the API response.

        Always names the run: a bound without the provenance of the calibration
        that produced it is a marketing claim.
        """
        if not self.certified:
            return (
                "no guarantee in force: no threshold could be certified on the "
                f"calibration set (calibration_id={self.calibration_id})"
            )
        confidence = round((1 - self.delta) * 100)
        return (
            f"P(unsupported_claim) <= {self.alpha:g} with {confidence}% confidence, "
            f"calibration_id={self.calibration_id}"
        )


async def save(
    conn: AsyncConnection,
    *,
    calibration_id: str,
    tenant_id: str,
    selection: Selection,
    corpus_known_at: datetime,
    statistic_name: str,
    lang: str = "",
    loss_source: str = "verifier",
    hallucination_rate: float = 0.0,
) -> CalibrationRecord:
    """Persist one Learn-then-Test run, certified or not."""
    cur = await conn.execute(
        """
        INSERT INTO calibrations (
            calibration_id, tenant_id, alpha, delta, threshold, n,
            coverage, empirical_risk, certified, corpus_known_at,
            statistic_name, lang, loss_source, hallucination_rate
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING created_at
        """,
        (
            calibration_id, tenant_id, selection.alpha, selection.delta,
            selection.lambda_value, selection.n, selection.coverage,
            selection.empirical_risk, selection.certified, corpus_known_at,
            statistic_name, lang, loss_source, hallucination_rate,
        ),
    )
    row = await cur.fetchone()
    return CalibrationRecord(
        calibration_id=calibration_id,
        tenant_id=tenant_id,
        alpha=selection.alpha,
        delta=selection.delta,
        threshold=selection.lambda_value,
        n=selection.n,
        coverage=selection.coverage,
        empirical_risk=selection.empirical_risk,
        certified=selection.certified,
        statistic_name=statistic_name,
        lang=lang,
        created_at=row["created_at"],
        corpus_known_at=corpus_known_at,
        loss_source=loss_source,
        hallucination_rate=hallucination_rate,
    )


async def latest(
    conn: AsyncConnection, tenant_id: str, alpha: float
) -> CalibrationRecord | None:
    """Most recent certified run for a tenant at this alpha.

    Matching alpha exactly rather than "any run at or below the requested alpha":
    a threshold certified for 0.10 says nothing about 0.05, and quoting it for a
    tighter budget would be inventing a guarantee.
    """
    cur = await conn.execute(
        """
        SELECT * FROM calibrations
        WHERE tenant_id = %s AND alpha = %s AND certified
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (tenant_id, alpha),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return CalibrationRecord(
        calibration_id=row["calibration_id"],
        tenant_id=row["tenant_id"],
        alpha=float(row["alpha"]),
        delta=float(row["delta"]),
        threshold=float(row["threshold"]),
        n=int(row["n"]),
        coverage=float(row["coverage"]),
        empirical_risk=float(row["empirical_risk"]),
        certified=bool(row["certified"]),
        statistic_name=row["statistic_name"],
        lang=row["lang"],
        created_at=row["created_at"],
        corpus_known_at=row["corpus_known_at"],
        loss_source=row["loss_source"],
        hallucination_rate=float(row["hallucination_rate"]),
    )


async def save_examples(
    conn: AsyncConnection,
    calibration_id: str,
    examples: list[tuple[str, str, float, bool, str, str]],
) -> None:
    """Store the calibration set itself.

    Kept so a run can be reproduced and audited, and so the label mix — human
    versus LLM-judge versus verifier — can be reported. Verifier-only labels are
    noisy and the bound is conditional on that noise (PRD open question 4).
    """
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO calibration_examples
                (calibration_id, query, answer, statistic, loss, label_source, split)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(calibration_id, *example) for example in examples],
        )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


async def corpus_changes_since(
    conn: AsyncConnection, tenant_id: str, since: datetime
) -> int:
    """Documents created, amended or corrected after ``since``.

    Any change counts. A threshold on "how much" change is tolerable would be a
    second, uncalibrated threshold sitting in front of the calibrated one.
    """
    cur = await conn.execute(
        """
        SELECT count(DISTINCT doc_id) AS n FROM corpus_events
        WHERE tenant_id = %s AND occurred_at > %s
        """,
        (tenant_id, since),
    )
    row = await cur.fetchone()
    return int(row["n"])


@dataclass(frozen=True)
class DriftMonitor:
    """A monitor's state plus the parameters it was started with. The level and
    jump are pinned at creation: Ville's inequality holds only for a level fixed
    before the sequence, so a later config change applies to new calibrations,
    never to a martingale already running."""

    state: drift.MonitorState
    alarm_level: float
    jump: float


def _state_from(row) -> drift.MonitorState:
    return drift.MonitorState(
        n=int(row["n"]),
        log_wealth=float(row["log_wealth"]),
        weights=tuple(float(w) for w in row["weights"]),
        alarmed=row["alarmed_at"] is not None,
    )


async def observe_drift(
    conn: AsyncConnection,
    calibration_id: str,
    statistic: float,
    *,
    alarm_level: float,
    jump: float = drift.DEFAULT_JUMP,
) -> drift.MonitorState:
    """Feed one live statistic to the calibration's martingale.

    Must run inside a transaction: the monitor row is locked for the whole
    read–bet–write so that concurrent requests are applied in sequence.
    """
    initial = drift.initial()
    await conn.execute(
        """
        INSERT INTO drift_monitors (calibration_id, weights, alarm_level, jump)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (calibration_id) DO NOTHING
        """,
        (calibration_id, list(initial.weights), alarm_level, jump),
    )
    cur = await conn.execute(
        "SELECT * FROM drift_monitors WHERE calibration_id = %s FOR UPDATE",
        (calibration_id,),
    )
    row = await cur.fetchone()
    state = _state_from(row)
    # The pinned parameters win over the caller's: see DriftMonitor.
    alarm_level, jump = float(row["alarm_level"]), float(row["jump"])

    # The bag is the calibration statistics plus every live one so far, the new
    # one included (it is the +1 in size and in equal). Calibration responses
    # with no generated answer are left out: the gateway never asks the
    # controller about those, so they have no live counterpart, and including
    # them would itself look like drift.
    cur = await conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE statistic > %(s)s) AS greater,
            count(*) FILTER (WHERE statistic = %(s)s) AS equal,
            count(*) AS size
        FROM (
            SELECT statistic FROM calibration_examples
            WHERE calibration_id = %(c)s AND split = 'calibration' AND answer <> ''
            UNION ALL
            SELECT statistic FROM drift_observations WHERE calibration_id = %(c)s
        ) bag
        """,
        {"s": statistic, "c": calibration_id},
    )
    counts = await cur.fetchone()
    p = drift.conformal_p(
        greater=int(counts["greater"]),
        equal=int(counts["equal"]) + 1,
        size=int(counts["size"]) + 1,
        theta=drift.theta(calibration_id, state.n),
    )
    new = drift.step(state, p, alarm_level=alarm_level, jump=jump)

    await conn.execute(
        "INSERT INTO drift_observations (calibration_id, statistic, p_value) VALUES (%s, %s, %s)",
        (calibration_id, statistic, p),
    )
    await conn.execute(
        """
        UPDATE drift_monitors
        SET n = %s, log_wealth = %s, weights = %s, updated_at = now(),
            alarmed_at = CASE WHEN %s AND alarmed_at IS NULL THEN now() ELSE alarmed_at END
        WHERE calibration_id = %s
        """,
        (new.n, new.log_wealth, list(new.weights), new.alarmed, calibration_id),
    )
    return new


async def drift_monitor(conn: AsyncConnection, calibration_id: str) -> DriftMonitor | None:
    cur = await conn.execute(
        "SELECT * FROM drift_monitors WHERE calibration_id = %s", (calibration_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return DriftMonitor(
        state=_state_from(row), alarm_level=float(row["alarm_level"]), jump=float(row["jump"])
    )
