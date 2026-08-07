"""Risk controller — the component the whole project is about.

It answers one question per request: *given this tenant's risk budget and the
verifier's scores, may we return this response at all?*

The threshold comes from a stored Learn-then-Test run
(:mod:`aletheia.eval.calibrate`). There is no fallback threshold and there is
deliberately no default: with no certified, fresh calibration for the requested
alpha, the answer is `abstain(stale_calibration)`. An answer without a guarantee
is not the product, and a guessed threshold would be a guarantee-shaped string
with nothing behind it.

The statistic and the action policy live in :mod:`aletheia.risk.statistic`,
shared with the calibration job — a threshold fitted against one statistic and
applied to another is arithmetically valid and empirically meaningless.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI

from aletheia.contracts import (
    AbstainReason,
    DecideRequest,
    DecideResponse,
    Decision,
)
from aletheia.db import get_db
from aletheia.risk import store
from aletheia.risk.statistic import (
    apply_action_policy,
    response_loss,
    retained,
    risk_statistic,
)
from aletheia.service import create_app
from aletheia.settings import get_settings

log = logging.getLogger("risk")

_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    db = get_db()
    try:
        await db.open()
        _ready = await db.healthy()
    except Exception:
        log.exception("could not open the database; running degraded")
        _ready = False
    yield
    await db.close()


app = create_app("risk", ready=lambda: _ready, lifespan=lifespan)


def _abstain(reason: AbstainReason, guarantee: str, *, degraded: bool) -> DecideResponse:
    return DecideResponse(
        decision=Decision.ABSTAIN,
        abstain_reason=reason,
        guarantee=guarantee,
        degraded=degraded,
        claims=[],
    )


@app.post("/decide", response_model=DecideResponse)
async def decide(req: DecideRequest) -> DecideResponse:
    settings = get_settings()

    async with get_db().connection() as conn:
        record = await store.latest(conn, req.tenant_id, req.risk_budget)
        cur = await conn.execute("SELECT now() AS t")
        now = (await cur.fetchone())["t"]

    if record is None:
        return _abstain(
            AbstainReason.STALE_CALIBRATION,
            f"no guarantee in force: no certified calibration for alpha={req.risk_budget:g} "
            f"and tenant {req.tenant_id!r}",
            degraded=True,
        )
    if record.is_stale(settings.calibration_max_age_hours, now=now):
        # Age is a crude proxy for exchangeability having lapsed, but a lapsed
        # assumption invalidates the bound, and continuing to quote it would be
        # the one dishonesty this system cannot afford (PRD §5.2).
        return _abstain(
            AbstainReason.STALE_CALIBRATION,
            f"no guarantee in force: calibration {record.calibration_id} is older "
            f"than {settings.calibration_max_age_hours}h",
            degraded=True,
        )

    claims = apply_action_policy(req.claims, req.mode)
    kept = retained(claims)
    statistic = risk_statistic(claims)
    guarantee = record.guarantee()

    if not kept:
        return DecideResponse(
            decision=Decision.ABSTAIN,
            statistic=round(statistic, 4),
            threshold=record.threshold,
            calibration_id=record.calibration_id,
            guarantee=guarantee,
            abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            claims=[],
        )

    if statistic > record.threshold:
        return DecideResponse(
            decision=Decision.ABSTAIN,
            statistic=round(statistic, 4),
            threshold=record.threshold,
            calibration_id=record.calibration_id,
            guarantee=guarantee,
            abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            claims=[],
        )

    # In permissive mode unsupported claims are kept as generated, so the bound
    # does not describe what is being returned. Saying so is the difference
    # between a caveat and a false statement.
    if response_loss(claims):
        guarantee = (
            "no guarantee in force: permissive mode returns unsupported claims "
            f"(threshold {record.threshold:g} from {record.calibration_id})"
        )

    edited = any(c.action != "kept" for c in claims)
    return DecideResponse(
        decision=Decision.ANSWER_WITH_FLAGS if edited else Decision.ANSWER,
        statistic=round(statistic, 4),
        threshold=record.threshold,
        calibration_id=record.calibration_id,
        guarantee=guarantee,
        claims=claims,
    )


@app.get("/calibration/{tenant_id}")
async def calibration(tenant_id: str, alpha: float = 0.05) -> dict:
    """What guarantee is currently in force, if any. For operators and the demo."""
    async with get_db().connection() as conn:
        record = await store.latest(conn, tenant_id, alpha)
        cur = await conn.execute("SELECT now() AS t")
        now = (await cur.fetchone())["t"]

    if record is None:
        return {"in_force": False, "reason": "no certified calibration"}

    settings = get_settings()
    stale = record.is_stale(settings.calibration_max_age_hours, now=now)
    return {
        "in_force": not stale,
        "reason": "stale" if stale else "",
        "calibration_id": record.calibration_id,
        "alpha": record.alpha,
        "delta": record.delta,
        "threshold": record.threshold,
        "n": record.n,
        "coverage": record.coverage,
        "empirical_risk": record.empirical_risk,
        "statistic": record.statistic_name,
        "created_at": record.created_at.astimezone(UTC).isoformat(),
        "guarantee": record.guarantee(),
    }
