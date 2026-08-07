"""The confidence statistic and the action policy.

Shared by the runtime service and the calibration job, and that sharing is not
incidental: if calibration fitted a threshold against one statistic and serving
applied it to another, the bound would be arithmetically valid and empirically
meaningless. One definition, both callers.

The statistic is a **risk** score — lower is better, and we abstain when it
exceeds the threshold. The PRD's worked example (§6) and its prose (§5.2 step 4)
disagree on the direction; this is the resolved convention.

It is computed **after** the action policy runs. The bound applies to the text
the user actually receives, so removing a bad claim and then quoting a bound
computed before the removal would be circular (ADR-0004).
"""

from __future__ import annotations

from collections.abc import Sequence

from aletheia.contracts import Claim, ClaimAction, ClaimStatus, Mode

STATISTIC_NAME = "one_minus_min_support"
"""Recorded on every calibration row so a threshold can never be applied to a
statistic it was not fitted against."""


def apply_action_policy(claims: Sequence[Claim], mode: Mode) -> list[Claim]:
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


def retained(claims: Sequence[Claim]) -> list[Claim]:
    return [c for c in claims if c.action != ClaimAction.REMOVED]


def risk_statistic(claims: Sequence[Claim]) -> float:
    """Scalar risk of a response, in [0, 1]. Lower is better.

    ``1 − min(support_score)`` over retained claims: the weakest link, which is
    what a per-response bound is about (ADR-0004). A response with nothing left
    to say scores 1.0 — maximum risk — so it can never clear a threshold.

    PRD open question 1 lists the alternatives (mean support, unsupported
    fraction, a learned scorer). Whichever wins must stay a single scalar,
    because that is what the threshold is applied to, and it must be recorded
    under :data:`STATISTIC_NAME` so old calibrations are not silently reused.
    """
    kept = retained(claims)
    if not kept:
        return 1.0
    return 1.0 - min(c.support_score for c in kept)


def response_loss(claims: Sequence[Claim]) -> bool:
    """The binary loss the guarantee is defined over.

    True when the response we would return contains at least one unsupported
    claim. Per response, not per claim: a ten-claim answer under a per-claim 5%
    bound has roughly a 40% chance of containing an unsupported claim, and a user
    reading "≤5% error" has been misled by the choice of unit (ADR-0004).
    """
    return any(c.status == ClaimStatus.UNSUPPORTED for c in retained(claims))
