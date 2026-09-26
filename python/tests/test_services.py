from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aletheia.contracts import Chunk, ClaimStatus, DraftClaim
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


@pytest.mark.parametrize("name", ["generation", "verifier"])
def test_stateless_services_are_ready_without_dependencies(name: str) -> None:
    """Generation and verification hold their models in-process and touch nothing else."""
    with TestClient(ALL_APPS[name]) as client:
        assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize("name", ["retrieval", "risk"])
def test_stateful_services_report_not_ready_without_a_database(name: str) -> None:
    """Degraded, not dead: /readyz stops traffic, /healthz keeps the container.

    Risk joined this list when its threshold stopped being a hardcoded constant
    and started coming from a stored calibration run.
    """
    with TestClient(ALL_APPS[name]) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503



def test_extractive_generation_cites_everything_it_says() -> None:
    """Extractive generation copies sentences from evidence, so it cannot produce
    an uncited claim — and therefore cannot hallucinate.

    That is convenient for reproducibility and load-bearing when reading any
    number calibrated against it: the loss such a calibration measures comes from
    retrieval and verifier strictness, never from unsupported generation.
    """
    with TestClient(generation_app) as client:
        resp = client.post(
            "/generate",
            json={
                "tenant_id": "acme",
                "query": "notice period days",
                "chunks": [
                    Chunk(
                        chunk_id="c1", doc_id="d1",
                        text="The notice period is 30 days. Unrelated sentence here.",
                    ).model_dump()
                ],
            },
        )
        claims = resp.json()["claims"]
        assert claims, "expected at least one claim for an on-topic query"
        assert all(c["citations"] == ["c1"] for c in claims)


def test_extractive_sentences_survive_thousands_separators_and_headings() -> None:
    """A claim is a whole sentence. Splitting "10.000" at its dot produced the
    fragment "000) Türk Lirası ..." — a claim nobody wrote and nothing entails —
    and a markdown heading glued itself onto the next sentence."""
    from aletheia.generation.providers import ExtractiveGenerator

    chunk = Chunk(
        chunk_id="c1", doc_id="d1",
        text="## Harcama Limitleri\n\n10.000 (on bin) Türk Lirasına kadar harcamalar "
             "için müdür onayı gerekir. Fazlası için direktör onayı gerekir.",
    )
    answer = ExtractiveGenerator().generate("harcama onayı müdür", [chunk], max_claims=4)
    assert "10.000 (on bin) Türk Lirasına kadar" in answer
    assert "\n000)" not in answer and not answer.startswith("000")
    assert "##" not in answer


def test_generation_with_no_chunks_produces_nothing() -> None:
    """Nothing retrieved means nothing to ground an answer in. Returning empty
    lets the gateway abstain rather than inviting a model to fill the gap."""
    with TestClient(generation_app) as client:
        resp = client.post(
            "/generate",
            json={"tenant_id": "acme", "query": "anything", "chunks": []},
        )
        assert resp.json() == {"answer": "", "claims": []}


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


def test_unknown_fields_are_rejected() -> None:
    """A silently ignored field is how two services drift apart while both look fine."""
    with TestClient(verifier_app) as client:
        resp = client.post(
            "/verify",
            json={"tenant_id": "acme", "claims": [], "chunks": [], "temperature": 0.7},
        )
        assert resp.status_code == 422


def test_extractive_generation_skips_fragments_cut_at_a_chunk_boundary() -> None:
    """A chunk can end mid-sentence. The unfinished tail is not a claim anyone
    made, nothing entails it, and it cost answer rate on every query."""
    from aletheia.generation.providers import ExtractiveGenerator

    chunk = Chunk(chunk_id="c1", doc_id="d1",
                  text="Başvuru otuz gün içinde sonuçlandırılır. İlgili kişi başvuru sonucunda kişisel verilerin")
    answer = ExtractiveGenerator().generate("başvuru kişisel veri", [chunk], max_claims=4)
    assert "otuz gün" in answer
    assert "verilerin [" not in answer
