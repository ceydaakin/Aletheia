"""The Python half of the contract check. See testdata/contracts/README.md.

Every model here is configured with ``extra="forbid"``, so parsing a fixture that
carries a field this side does not know about is a failure — which is exactly the
drift we want CI to catch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia.contracts import (
    ClaimAction,
    ClaimStatus,
    DecideResponse,
    Decision,
    GenerateResponse,
    RetrieveResponse,
    VerifyResponse,
)

GOLDEN = Path(__file__).resolve().parents[2] / "testdata" / "contracts"


def load(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def test_retrieve_response() -> None:
    got = RetrieveResponse.model_validate(load("retrieve_response.json"))
    assert len(got.chunks) == 2
    assert got.chunks[0].chunk_id == "doc_412:v3:chunk_18"
    assert got.chunks[0].version == 3
    # Turkish text must survive the round trip intact — this is the language the
    # cross-lingual calibration experiment depends on.
    assert "feshedebilir" in got.chunks[0].text


def test_generate_response() -> None:
    got = GenerateResponse.model_validate(load("generate_response.json"))
    assert len(got.claims) == 2
    assert got.claims[1].citations == []


def test_verify_response() -> None:
    got = VerifyResponse.model_validate(load("verify_response.json"))
    assert got.claims[0].status is ClaimStatus.SUPPORTED
    assert got.claims[1].status is ClaimStatus.UNSUPPORTED
    assert got.claims[0].support_score == pytest.approx(0.94)


def test_decide_response() -> None:
    got = DecideResponse.model_validate(load("decide_response.json"))
    assert got.decision is Decision.ANSWER_WITH_FLAGS
    assert got.claims[1].action is ClaimAction.REMOVED
    assert got.calibration_id == "cal_2026_07_tr"


def test_unknown_fields_are_rejected() -> None:
    payload = load("verify_response.json")
    payload["claims"][0]["confidence"] = 0.9
    with pytest.raises(ValueError):
        VerifyResponse.model_validate(payload)


def test_round_trip_is_lossless() -> None:
    """Serialising what we parsed must reproduce the fixture."""
    for name, model in (
        ("retrieve_response.json", RetrieveResponse),
        ("generate_response.json", GenerateResponse),
        ("verify_response.json", VerifyResponse),
        ("decide_response.json", DecideResponse),
    ):
        original = load(name)
        parsed = model.model_validate(original)
        emitted = json.loads(parsed.model_dump_json(exclude_none=True))
        for key, value in original.items():
            assert emitted[key] == value, f"{name}: field {key!r} did not round-trip"
