from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aletheia.contracts import (
    Chunk,
    Claim,
    ClaimAction,
    ClaimStatus,
    Decision,
    DraftClaim,
    Mode,
)
from aletheia.generation.app import app as generation_app
from aletheia.retrieval.app import app as retrieval_app
from aletheia.risk.app import app as risk_app
from aletheia.verifier.app import app as verifier_app

ALL_APPS = {
    "retrieval": retrieval_app,
    "generation": generation_app,
    "verifier": verifier_app,
    "risk": risk_app,
}


@pytest.mark.parametrize("name", sorted(ALL_APPS))
def test_healthz_does_not_depend_on_anything(name: str) -> None:
    """Liveness must stay green even with no database.

    Retrieval reaches Postgres now; if a slow database could fail its liveness
    probe, the orchestrator would restart-loop a process whose only problem is
    that a dependency is late.
    """
    with TestClient(ALL_APPS[name]) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": name}
        assert client.get("/metrics").status_code == 200


@pytest.mark.parametrize("name", ["generation", "verifier", "risk"])
def test_stateless_services_are_ready_without_dependencies(name: str) -> None:
    with TestClient(ALL_APPS[name]) as client:
        assert client.get("/readyz").status_code == 200


def test_retrieval_reports_not_ready_without_a_database() -> None:
    """Degraded, not dead: /readyz stops traffic, /healthz keeps the container."""
    with TestClient(retrieval_app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503


def test_generation_emits_an_uncited_claim() -> None:
    """The stub must not be perfectly cited, or a broken verifier looks correct."""
    with TestClient(generation_app) as client:
        resp = client.post(
            "/generate",
            json={
                "tenant_id": "acme",
                "query": "notice period?",
                "chunks": [
                    Chunk(chunk_id="c1", doc_id="d1", text="Notice is 30 days.").model_dump()
                ],
            },
        )
        claims = resp.json()["claims"]
        assert any(not c["citations"] for c in claims)


def test_verifier_treats_uncited_claims_as_unsupported() -> None:
    """Plausibility is not evidence — this rule outlives the stub scorer."""
    with TestClient(verifier_app) as client:
        resp = client.post(
            "/verify",
            json={
                "tenant_id": "acme",
                "chunks": [
                    Chunk(
                        chunk_id="c1", doc_id="d1", text="Either party may terminate on 30 days notice."
                    ).model_dump()
                ],
                "claims": [
                    DraftClaim(text="Either party may terminate on 30 days notice.", citations=["c1"]).model_dump(),
                    DraftClaim(text="Either party may terminate on 30 days notice.", citations=[]).model_dump(),
                    DraftClaim(text="A claim citing something never retrieved.", citations=["ghost"]).model_dump(),
                ],
            },
        )
        claims = resp.json()["claims"]
        assert claims[0]["status"] == ClaimStatus.SUPPORTED
        # Identical text, no citation: must still be unsupported.
        assert claims[1]["status"] == ClaimStatus.UNSUPPORTED
        assert claims[1]["support_score"] == 0.0
        # A dangling citation looks like evidence and must score zero.
        assert claims[2]["support_score"] == 0.0


def _claim(text: str, score: float, status: ClaimStatus) -> dict:
    return Claim(
        text=text, citations=["c1"] if score > 0 else [], support_score=score, status=status
    ).model_dump()


def test_risk_strict_mode_removes_unsupported_claims() -> None:
    with TestClient(risk_app) as client:
        resp = client.post(
            "/decide",
            json={
                "tenant_id": "acme",
                "risk_budget": 0.05,
                "mode": Mode.STRICT.value,
                "claims": [
                    _claim("Supported.", 0.95, ClaimStatus.SUPPORTED),
                    _claim("Unsupported.", 0.05, ClaimStatus.UNSUPPORTED),
                ],
            },
        )
        body = resp.json()
        assert body["decision"] == Decision.ANSWER_WITH_FLAGS
        actions = [c["action"] for c in body["claims"]]
        assert actions == [ClaimAction.KEPT, ClaimAction.REMOVED]


def test_risk_statistic_is_computed_after_the_action_policy() -> None:
    """ADR-0004: the bound is about the text the user receives.

    In strict mode the weak claim is removed, so it must not drag the statistic
    down and force an abstention on an answer that no longer contains it.
    """
    payload = {
        "tenant_id": "acme",
        "risk_budget": 0.05,
        "claims": [
            _claim("Supported.", 0.95, ClaimStatus.SUPPORTED),
            _claim("Unsupported.", 0.01, ClaimStatus.UNSUPPORTED),
        ],
    }
    with TestClient(risk_app) as client:
        strict = client.post("/decide", json={**payload, "mode": Mode.STRICT.value}).json()
        flagged = client.post("/decide", json={**payload, "mode": Mode.FLAGGED.value}).json()

    # Strict: statistic reflects only the surviving claim.
    assert strict["statistic"] == pytest.approx(1 - 0.95, abs=1e-4)
    assert strict["decision"] != Decision.ABSTAIN
    # Flagged: the weak claim is still in the answer, so it still counts.
    assert flagged["statistic"] == pytest.approx(1 - 0.01, abs=1e-4)
    assert flagged["decision"] == Decision.ABSTAIN


def test_risk_abstains_when_every_claim_is_removed() -> None:
    with TestClient(risk_app) as client:
        body = client.post(
            "/decide",
            json={
                "tenant_id": "acme",
                "risk_budget": 0.05,
                "mode": Mode.STRICT.value,
                "claims": [_claim("Unsupported.", 0.02, ClaimStatus.UNSUPPORTED)],
            },
        ).json()
        assert body["decision"] == Decision.ABSTAIN
        assert body["abstain_reason"] == "insufficient_evidence"
        assert body["claims"] == []


def test_uncalibrated_scaffold_never_claims_a_bound() -> None:
    """The stub threshold was fitted to nothing, and the response must admit it."""
    with TestClient(risk_app) as client:
        body = client.post(
            "/decide",
            json={
                "tenant_id": "acme",
                "risk_budget": 0.05,
                "claims": [_claim("Supported.", 0.99, ClaimStatus.SUPPORTED)],
            },
        ).json()
        assert body["degraded"] is True
        assert "UNCALIBRATED" in body["guarantee"]


def test_unknown_fields_are_rejected() -> None:
    """A silently ignored field is how two services drift apart while both look fine."""
    with TestClient(verifier_app) as client:
        resp = client.post(
            "/verify",
            json={"tenant_id": "acme", "claims": [], "chunks": [], "temperature": 0.7},
        )
        assert resp.status_code == 422
