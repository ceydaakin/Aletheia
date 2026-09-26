"""From a recorded response to the (statistic, loss) pair a threshold is fitted on.

Everything that varies between rows of the ablation table is a parameter here —
which verifier variant scores the claims, the tenant's action mode, the
per-claim support threshold, and where the loss comes from — so the pipeline
runs once and every configuration is derived from the same responses.

**Two sources of loss, and only one of them is the guarantee.**

``gold`` reads the loss from ground truth: a response fails if any claim it
would return was corrupted on purpose (:mod:`aletheia.eval.hallucinate`). The
verifier produces the *statistic* and never the label, so its own mistakes —
a corrupted claim it scores as supported — land in the loss and are therefore
inside the bound. This closes the gap in report §7.3 for the error types that
are simulated.

``verifier`` is the original, circular definition: the loss is whatever the
verifier says. Kept because it is what a deployment without labels can compute
and the contrast between the two is itself a result.
"""

from __future__ import annotations

from dataclasses import dataclass

from aletheia.contracts import Claim, ClaimAction, ClaimStatus, Mode
from aletheia.eval.records import ResponseRecord
from aletheia.risk.statistic import apply_action_policy, response_loss, risk_statistic

LOSS_SOURCES = ("gold", "verifier")


@dataclass(frozen=True)
class Observation:
    """One labelled response."""

    query_id: str
    query: str
    answer: str
    statistic: float
    loss: bool
    """True if the response contains an unsupported claim."""
    answered: bool
    """False when the pipeline had nothing to return regardless of threshold —
    no claims at all, or none left after the action policy."""
    useful: bool = False
    """A returned, uncorrupted claim restates the gold evidence. Answer rate
    alone rewards answering *something*; this is answering the question."""
    answerable: bool = True
    category: str = ""


def to_observation(
    record: ResponseRecord,
    *,
    variant: str,
    mode: Mode,
    support_threshold: float,
    loss: str = "gold",
) -> Observation:
    if loss not in LOSS_SOURCES:
        raise ValueError(f"loss must be one of {LOSS_SOURCES}, got {loss!r}")

    base = {
        "query_id": record.query_id,
        "query": record.query,
        "answer": record.answer,
        "answerable": record.answerable,
        "category": record.category,
    }
    if not record.claims:
        return Observation(**base, statistic=1.0, loss=False, answered=False)

    missing = [i for i, c in enumerate(record.claims) if variant not in c.scores]
    if missing:
        raise ValueError(
            f"{record.query_id}: no {variant!r} scores recorded "
            f"(have {sorted(record.variants)}); re-run collect with that verifier"
        )

    claims = [
        Claim(
            text=c.text,
            citations=list(c.citations),
            support_score=round(c.scores[variant], 4),
            status=(
                ClaimStatus.SUPPORTED
                if c.scores[variant] >= support_threshold
                else ClaimStatus.UNSUPPORTED
            ),
        )
        for c in record.claims
    ]
    edited = apply_action_policy(claims, mode)
    kept = [i for i, c in enumerate(edited) if c.action != ClaimAction.REMOVED]

    # Mirrors the service: with nothing left to say the controller abstains
    # (insufficient_evidence) whatever the threshold, so this is not a response
    # any threshold could gate.
    if not kept:
        return Observation(**base, statistic=1.0, loss=False, answered=False)

    if loss == "gold":
        failed = any(record.claims[i].corrupted for i in kept)
    else:
        failed = response_loss(edited)

    return Observation(
        **base,
        statistic=risk_statistic(edited),
        loss=failed,
        answered=True,
        useful=any(
            record.claims[i].on_evidence and not record.claims[i].corrupted for i in kept
        ),
    )


def observe_all(records: list[ResponseRecord], **kwargs) -> list[Observation]:
    return [to_observation(r, **kwargs) for r in records]
