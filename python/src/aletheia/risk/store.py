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
  available proxy for that having stopped being true. A corpus change is the
  sharper signal — ``corpus_events`` — and week 9 wires it in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import AsyncConnection

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
) -> CalibrationRecord:
    """Persist one Learn-then-Test run, certified or not."""
    cur = await conn.execute(
        """
        INSERT INTO calibrations (
            calibration_id, tenant_id, alpha, delta, threshold, n,
            coverage, empirical_risk, certified, corpus_known_at,
            statistic_name, lang
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING created_at
        """,
        (
            calibration_id, tenant_id, selection.alpha, selection.delta,
            selection.lambda_value, selection.n, selection.coverage,
            selection.empirical_risk, selection.certified, corpus_known_at,
            statistic_name, lang,
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
