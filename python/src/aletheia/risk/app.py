"""Risk controller — the component the whole project is about.

It answers one question per request: *given this tenant's risk budget and the
verifier's scores, may we return this response at all?*

**Scaffold.** The runtime decision logic below is real; the calibration behind it
is not. :class:`StubCalibrationStore` returns a fixed threshold that was not
fitted to anything. Week 7 replaces it with thresholds selected by
:mod:`aletheia.risk.ltt` over a held-out calibration set, stored per tenant in
Postgres alongside the corpus snapshot they were fitted on.

**Statistic orientation.** The statistic is a *risk* score: lower is better, and
we abstain when it exceeds the threshold λ. The PRD's worked example (§6) and its
prose (§5.2 step 4) disagree on the direction; this is the resolved convention
and the PRD should be corrected to match.

**The statistic is computed after the action policy runs**, not before. The bound
applies to the text the user actually receives, so removing a bad claim and then
quoting a bound computed before the removal would be circular (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aletheia.contracts import (
    AbstainReason,
    Claim,
    ClaimAction,
    ClaimStatus,
    DecideRequest,
    DecideResponse,
    Decision,
    Mode,
)
from aletheia.service import create_app
from aletheia.settings import get_settings

app = create_app("risk")


@dataclass(frozen=True)
class CalibrationRecord:
    calibration_id: str
    threshold: float
    alpha: float
    delta: float
    n: int
    created_at: datetime

    def is_stale(self, max_age_hours: int) -> bool:
        age = datetime.now(UTC) - self.created_at
        return age.total_seconds() > max_age_hours * 3600


class StubCalibrationStore:
    """Placeholder for the per-tenant calibration table.

    Week 7 replaces this with a Postgres-backed store keyed by
    ``(tenant_id, alpha)``, holding thresholds produced by
    :func:`aletheia.risk.ltt.select` and the transaction-time corpus snapshot they
    were fitted on. Small tenants will not have enough calibration data of their
    own — hierarchical or pooled calibration is PRD open question 3.
    """

    def get(self, tenant_id: str, alpha: float) -> CalibrationRecord | None:
        return CalibrationRecord(
            calibration_id="cal_stub_v0",
            # Not fitted to anything. A threshold that has never seen data is a
            # placeholder, and the guarantee string below says so.
            threshold=0.5,
            alpha=alpha,
            delta=0.05,
            n=0,
            created_at=datetime.now(UTC),
        )


_store = StubCalibrationStore()


def apply_action_policy(claims: list[Claim], mode: Mode) -> list[Claim]:
    """Mark each claim according to the tenant's mode."""
    out: list[Claim] = []
    for claim in claims:
        if claim.status == ClaimStatus.SUPPORTED:
            action = ClaimAction.KEPT
        elif mode == Mode.STRICT:
            action = ClaimAction.REMOVED
        elif mode == Mode.FLAGGED:
            action = ClaimAction.FLAGGED
        else:  # permissive
            action = ClaimAction.KEPT
        out.append(claim.model_copy(update={"action": action}))
    return out


def risk_statistic(claims: list[Claim]) -> float:
    """Scalar risk of the response, in [0, 1]. Lower is better.

    Currently ``1 − min(support_score)`` over retained claims: the weakest link,
    which is what a per-response bound is about. PRD open question 1 lists the
    alternatives (mean support, unsupported fraction, a learned scorer); week 6
    picks between them empirically. Whatever wins must stay a single scalar,
    because that is what the threshold is applied to.
    """
    retained = [c for c in claims if c.action != ClaimAction.REMOVED]
    if not retained:
        return 1.0
    return 1.0 - min(c.support_score for c in retained)


@app.post("/decide", response_model=DecideResponse)
async def decide(req: DecideRequest) -> DecideResponse:
    settings = get_settings()
    calibration = _store.get(req.tenant_id, req.risk_budget)

    if calibration is None or calibration.is_stale(settings.calibration_max_age_hours):
        # No valid threshold means no guarantee, and an answer without a
        # guarantee is not the product.
        return DecideResponse(
            decision=Decision.ABSTAIN,
            abstain_reason=AbstainReason.STALE_CALIBRATION,
            degraded=True,
            guarantee="no guarantee issued: calibration missing or stale",
            claims=[],
        )

    claims = apply_action_policy(list(req.claims), req.mode)
    retained = [c for c in claims if c.action != ClaimAction.REMOVED]
    statistic = risk_statistic(claims)

    guarantee = (
        f"P(unsupported_claim) <= {req.risk_budget:g} with "
        f"{round((1 - calibration.delta) * 100)}% confidence, "
        f"calibration_id={calibration.calibration_id}"
    )
    # n == 0 means this threshold was never fitted. Say so rather than quoting a
    # bound we have not earned.
    unfitted = calibration.n == 0
    if unfitted:
        guarantee = f"UNCALIBRATED SCAFFOLD — no bound is in force (calibration_id={calibration.calibration_id})"

    if not retained or statistic > calibration.threshold:
        return DecideResponse(
            decision=Decision.ABSTAIN,
            statistic=round(statistic, 4),
            threshold=calibration.threshold,
            calibration_id=calibration.calibration_id,
            guarantee=guarantee,
            abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            degraded=unfitted,
            claims=[],
        )

    has_flags = any(c.action == ClaimAction.FLAGGED for c in claims) or any(
        c.action == ClaimAction.REMOVED for c in claims
    )
    return DecideResponse(
        decision=Decision.ANSWER_WITH_FLAGS if has_flags else Decision.ANSWER,
        statistic=round(statistic, 4),
        threshold=calibration.threshold,
        calibration_id=calibration.calibration_id,
        guarantee=guarantee,
        degraded=unfitted,
        claims=claims,
    )
