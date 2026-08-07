"""Risk controller against real calibration storage.

The rule these tests exist to pin down: **no calibration, no answer.** Every
route that could produce a response without a certified, fresh threshold behind
it must produce an abstention instead. A guessed threshold would be a
guarantee-shaped string with nothing behind it, which is the one failure this
project cannot afford.

Requires Postgres. Set ALETHEIA_TEST_DATABASE_URL, or these skip.
"""

from __future__ import annotations

import httpx
import pytest
from tests.conftest import TEST_TENANT

from aletheia.contracts import Claim, ClaimAction, ClaimStatus, Decision, Mode
from aletheia.db import set_db
from aletheia.risk import store
from aletheia.risk.ltt import Selection
from aletheia.risk.statistic import (
    apply_action_policy,
    response_loss,
    risk_statistic,
)

pytestmark = pytest.mark.db


@pytest.fixture
async def client(db):
    from aletheia.risk.app import app

    set_db(db)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                yield c
    finally:
        set_db(None)


async def save_calibration(
    db, *, alpha=0.05, threshold=0.4, certified=True, calibration_id="cal_test", age_hours=0
):
    selection = Selection(
        lambda_value=threshold, alpha=alpha, delta=0.05, n=200,
        coverage=0.8, empirical_risk=0.02, certified=certified,
    )
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT now() AS t")
        now = (await cur.fetchone())["t"]
        record = await store.save(
            conn, calibration_id=calibration_id, tenant_id=TEST_TENANT,
            selection=selection, corpus_known_at=now,
            statistic_name="one_minus_min_support",
        )
        if age_hours:
            await conn.execute(
                "UPDATE calibrations SET created_at = now() - %s * interval '1 hour' "
                "WHERE calibration_id = %s",
                (age_hours, calibration_id),
            )
    return record


def claim(text: str, score: float) -> dict:
    return Claim(
        text=text,
        citations=["c1"] if score > 0 else [],
        support_score=score,
        status=ClaimStatus.SUPPORTED if score >= 0.5 else ClaimStatus.UNSUPPORTED,
    ).model_dump()


async def decide(client, claims, *, mode=Mode.STRICT, budget=0.05):
    response = await client.post(
        "/decide",
        json={
            "tenant_id": TEST_TENANT,
            "risk_budget": budget,
            "mode": mode.value,
            "claims": claims,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- No calibration, no answer ---------------------------------------------


async def test_without_a_calibration_it_abstains(db, client) -> None:
    body = await decide(client, [claim("Perfectly supported.", 0.99)])

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "stale_calibration"
    assert body["degraded"] is True
    assert "no guarantee in force" in body["guarantee"]


async def test_an_uncertified_run_is_not_a_threshold(db, client) -> None:
    """select() records the case where nothing could be certified. That row is
    evidence, never a threshold."""
    await save_calibration(db, certified=False)
    body = await decide(client, [claim("Perfectly supported.", 0.99)])

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "stale_calibration"


async def test_a_stale_calibration_is_not_used(db, client) -> None:
    """Age is a crude proxy for exchangeability having lapsed — but a lapsed
    assumption invalidates the bound (PRD §5.2)."""
    await save_calibration(db, age_hours=1000)
    body = await decide(client, [claim("Perfectly supported.", 0.99)])

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "stale_calibration"
    assert "older than" in body["guarantee"]


async def test_a_threshold_for_another_alpha_is_not_borrowed(db, client) -> None:
    """A threshold certified for 0.10 says nothing about 0.05."""
    await save_calibration(db, alpha=0.10)
    body = await decide(client, [claim("Supported.", 0.99)], budget=0.05)

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "stale_calibration"


# --- With a calibration ----------------------------------------------------


async def test_answers_below_the_threshold(db, client) -> None:
    await save_calibration(db, threshold=0.4)
    body = await decide(client, [claim("Supported.", 0.9)])

    assert body["decision"] == Decision.ANSWER
    # statistic = 1 - min(support) = 0.1, under the 0.4 threshold.
    assert body["statistic"] == pytest.approx(0.1, abs=1e-4)
    assert body["calibration_id"] == "cal_test"
    assert "P(unsupported_claim) <= 0.05 with 95% confidence" in body["guarantee"]


async def test_abstains_above_the_threshold(db, client) -> None:
    await save_calibration(db, threshold=0.4)
    body = await decide(client, [claim("Weakly supported.", 0.5)], mode=Mode.FLAGGED)

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "insufficient_evidence"
    assert body["claims"] == []


async def test_strict_mode_removes_unsupported_claims(db, client) -> None:
    await save_calibration(db, threshold=0.4)
    body = await decide(
        client, [claim("Supported.", 0.95), claim("Unsupported.", 0.05)]
    )

    assert body["decision"] == Decision.ANSWER_WITH_FLAGS
    assert [c["action"] for c in body["claims"]] == [
        ClaimAction.KEPT,
        ClaimAction.REMOVED,
    ]


async def test_statistic_is_computed_after_the_action_policy(db, client) -> None:
    """ADR-0004: the bound is about the text the user receives.

    In strict mode the weak claim is removed, so it must not drag the statistic
    down and force an abstention on an answer that no longer contains it.
    """
    await save_calibration(db, threshold=0.4)
    claims = [claim("Supported.", 0.95), claim("Unsupported.", 0.01)]

    strict = await decide(client, claims, mode=Mode.STRICT)
    flagged = await decide(client, claims, mode=Mode.FLAGGED)

    assert strict["statistic"] == pytest.approx(0.05, abs=1e-4)
    assert strict["decision"] != Decision.ABSTAIN
    assert flagged["statistic"] == pytest.approx(0.99, abs=1e-4)
    assert flagged["decision"] == Decision.ABSTAIN


async def test_abstains_when_every_claim_is_removed(db, client) -> None:
    await save_calibration(db, threshold=0.9)
    body = await decide(client, [claim("Unsupported.", 0.02)])

    assert body["decision"] == Decision.ABSTAIN
    assert body["abstain_reason"] == "insufficient_evidence"
    assert body["claims"] == []


async def test_permissive_mode_withdraws_the_guarantee(db, client) -> None:
    """Permissive returns unsupported claims, so the bound does not describe what
    is being returned. Saying so is the difference between a caveat and a false
    statement."""
    await save_calibration(db, threshold=0.99)
    body = await decide(
        client, [claim("Supported.", 0.95), claim("Unsupported.", 0.02)],
        mode=Mode.PERMISSIVE,
    )

    assert body["decision"] != Decision.ABSTAIN
    assert "no guarantee in force" in body["guarantee"]
    assert all(c["action"] == ClaimAction.KEPT for c in body["claims"])


# --- Operator view ---------------------------------------------------------


async def test_calibration_endpoint_reports_what_is_in_force(db, client) -> None:
    await save_calibration(db, threshold=0.42)
    body = (await client.get(f"/calibration/{TEST_TENANT}?alpha=0.05")).json()

    assert body["in_force"] is True
    assert body["threshold"] == 0.42
    assert body["statistic"] == "one_minus_min_support"
    assert "cal_test" in body["guarantee"]


async def test_calibration_endpoint_with_nothing_stored(db, client) -> None:
    body = (await client.get(f"/calibration/{TEST_TENANT}")).json()
    assert body == {"in_force": False, "reason": "no certified calibration"}


async def test_latest_certified_run_wins(db, client) -> None:
    await save_calibration(db, calibration_id="cal_old", threshold=0.2)
    await save_calibration(db, calibration_id="cal_new", threshold=0.6)

    body = (await client.get(f"/calibration/{TEST_TENANT}")).json()
    assert body["calibration_id"] == "cal_new"


# --- The statistic itself (pure) -------------------------------------------


def test_risk_statistic_is_the_weakest_link() -> None:
    claims = [
        Claim(text="a", support_score=0.9, status=ClaimStatus.SUPPORTED, action=ClaimAction.KEPT),
        Claim(text="b", support_score=0.3, status=ClaimStatus.UNSUPPORTED, action=ClaimAction.FLAGGED),
    ]
    assert risk_statistic(claims) == pytest.approx(0.7)


def test_an_empty_response_is_maximum_risk() -> None:
    """Nothing left to say can never clear a threshold."""
    assert risk_statistic([]) == 1.0


def test_response_loss_ignores_removed_claims() -> None:
    """The loss is about the response the user receives (ADR-0004)."""
    claims = apply_action_policy(
        [
            Claim(text="a", support_score=0.9, status=ClaimStatus.SUPPORTED),
            Claim(text="b", support_score=0.1, status=ClaimStatus.UNSUPPORTED),
        ],
        Mode.STRICT,
    )
    assert response_loss(claims) is False

    flagged = apply_action_policy(
        [
            Claim(text="a", support_score=0.9, status=ClaimStatus.SUPPORTED),
            Claim(text="b", support_score=0.1, status=ClaimStatus.UNSUPPORTED),
        ],
        Mode.FLAGGED,
    )
    assert response_loss(flagged) is True
