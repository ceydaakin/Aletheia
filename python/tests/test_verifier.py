"""Verifier service and entailment backends.

The model-backed tests are marked ``slow``: they download roughly a gigabyte on
first run and take minutes on CPU. Run them with ``pytest -m slow``. Everything
that is policy rather than modelling is tested with a stub scorer, so the rules
that must hold for *every* backend are checked on every run.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from aletheia.contracts import Chunk, ClaimStatus, DraftClaim
from aletheia.settings import Settings
from aletheia.verifier.nli import NLIScorer, OverlapScorer, get_scorer

PREMISE_TR = (
    "Taraflardan her biri, otuz (30) gün önceden yazılı ihbarda bulunmak "
    "suretiyle sözleşmeyi feshedebilir."
)
PREMISE_EN = (
    "Either party may terminate this agreement by giving thirty (30) days written notice."
)


class StubScorer:
    """Returns a fixed score, and records what it was asked.

    Lets the policy tests assert *which* pairs reached the model, which is the
    part that must not change with the backend.
    """

    name = "stub"

    def __init__(self, value: float = 0.9) -> None:
        self.value = value
        self.seen: list[tuple[str, str]] = []

    def score(self, pairs):
        self.seen.extend(pairs)
        return [self.value] * len(pairs)


@pytest.fixture
def client():
    from aletheia.verifier.app import app

    with TestClient(app) as test_client:
        test_client.app.state.scorer = StubScorer()
        yield test_client


def verify(client, claims, chunks):
    response = client.post(
        "/verify",
        json={
            "tenant_id": "t",
            "claims": [c.model_dump() for c in claims],
            "chunks": [c.model_dump() for c in chunks],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["claims"]


CHUNK = Chunk(chunk_id="c1", doc_id="d1", text=PREMISE_TR)


# --- Rules that hold for every backend -------------------------------------


def test_uncited_claim_is_unsupported_and_never_reaches_the_model(client) -> None:
    """Plausibility is not evidence. A model given an empty premise still returns
    a number, so the claim must be settled before it gets there."""
    scorer = client.app.state.scorer
    claims = verify(client, [DraftClaim(text=PREMISE_TR, citations=[])], [CHUNK])

    assert claims[0]["support_score"] == 0.0
    assert claims[0]["status"] == ClaimStatus.UNSUPPORTED
    assert scorer.seen == []


def test_dangling_citation_scores_zero(client) -> None:
    """Citing something that was not retrieved looks like evidence and is not."""
    scorer = client.app.state.scorer
    claims = verify(client, [DraftClaim(text="X.", citations=["ghost"])], [CHUNK])

    assert claims[0]["support_score"] == 0.0
    assert scorer.seen == []


def test_only_cited_chunks_become_the_premise(client) -> None:
    """A claim must not borrow support from a passage it never pointed at, or the
    citation is decorative and the guarantee unverifiable."""
    other = Chunk(chunk_id="c2", doc_id="d2", text="Tamamen alakasız bir metin.")
    scorer = client.app.state.scorer

    verify(client, [DraftClaim(text="X.", citations=["c1"])], [CHUNK, other])

    assert len(scorer.seen) == 1
    premise, hypothesis = scorer.seen[0]
    assert premise == PREMISE_TR
    assert "alakasız" not in premise
    assert hypothesis == "X."


def test_multiple_citations_are_concatenated(client) -> None:
    second = Chunk(chunk_id="c2", doc_id="d1", text="İkinci kanıt parçası.")
    scorer = client.app.state.scorer

    verify(client, [DraftClaim(text="X.", citations=["c1", "c2"])], [CHUNK, second])

    premise, _ = scorer.seen[0]
    assert PREMISE_TR in premise and "İkinci kanıt" in premise


def test_threshold_decides_the_label(client) -> None:
    client.app.state.scorer = StubScorer(0.49)
    low = verify(client, [DraftClaim(text="X.", citations=["c1"])], [CHUNK])
    client.app.state.scorer = StubScorer(0.51)
    high = verify(client, [DraftClaim(text="X.", citations=["c1"])], [CHUNK])

    assert low[0]["status"] == ClaimStatus.UNSUPPORTED
    assert high[0]["status"] == ClaimStatus.SUPPORTED


def test_claim_order_is_preserved(client) -> None:
    """Scores are matched back by position; a reordering would attach each score
    to the wrong claim."""
    claims = verify(
        client,
        [
            DraftClaim(text="first", citations=[]),
            DraftClaim(text="second", citations=["c1"]),
            DraftClaim(text="third", citations=[]),
        ],
        [CHUNK],
    )
    assert [c["text"] for c in claims] == ["first", "second", "third"]
    assert [c["support_score"] > 0 for c in claims] == [False, True, False]


def test_no_claims_is_not_an_error(client) -> None:
    assert verify(client, [], [CHUNK]) == []


# --- Overlap baseline ------------------------------------------------------


def test_overlap_accepts_a_claim_that_contradicts_its_evidence() -> None:
    """The documented reason it is a baseline and not a verifier.

    'sixty days' contradicts a premise that says thirty, and overlap still scores
    it at or above the default support threshold — so the false claim ships as
    supported. The NLI backend scores the same pair at 0.005 (see the slow test
    below). This is the gap the ablation table exists to quantify.
    """
    scorer = OverlapScorer()
    entailed, contradicted = scorer.score(
        [
            (PREMISE_EN, "The notice period is thirty days."),
            (PREMISE_EN, "The notice period is sixty days."),
        ]
    )
    assert entailed > contradicted
    assert contradicted >= Settings().support_threshold, (
        "if this ever fails, overlap has become discriminating enough to be worth "
        "more than a baseline — measure it before believing it"
    )


def test_overlap_handles_empty_input() -> None:
    assert OverlapScorer().score([]) == []
    assert OverlapScorer().score([("", "x")]) == [0.0]
    assert OverlapScorer().score([("x", "")]) == [0.0]


# --- Backend selection -----------------------------------------------------


def test_get_scorer_selects_by_configuration() -> None:
    assert get_scorer(Settings(verifier_backend="overlap")).name == "overlap"
    assert get_scorer(Settings(verifier_backend="nli")).name == "nli"
    with pytest.raises(ValueError):
        get_scorer(Settings(verifier_backend="magic"))


def test_nli_scorer_does_not_load_a_model_on_construction() -> None:
    """Importing must never pull a gigabyte into a process serving health checks."""
    scorer = NLIScorer("does-not-exist")
    assert scorer._pipeline is None


# --- The real model --------------------------------------------------------

slow = pytest.mark.skipif(
    os.environ.get("ALETHEIA_SLOW_TESTS") != "1",
    reason="downloads ~1GB and runs on CPU; set ALETHEIA_SLOW_TESTS=1",
)


@slow
def test_nli_separates_entailment_from_contradiction() -> None:
    """The claim this project rests on: entailment can tell 'otuz' from 'altmış'.

    If this ever stops holding, the guarantee is measuring nothing and the
    honest response is to say so rather than to lower the threshold.
    """
    scorer = NLIScorer("MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7")
    entailed_tr, contradicted_tr, negated_tr, entailed_en, contradicted_en = scorer.score(
        [
            (PREMISE_TR, "Fesih ihbar süresi otuz gündür."),
            (PREMISE_TR, "Fesih ihbar süresi altmış gündür."),
            (PREMISE_TR, "Sözleşme hiçbir şekilde feshedilemez."),
            (PREMISE_EN, "The notice period is thirty days."),
            (PREMISE_EN, "The notice period is sixty days."),
        ]
    )

    assert entailed_tr > 0.9
    assert entailed_en > 0.9
    # A single changed number must move the score across the threshold.
    assert contradicted_tr < 0.5
    assert contradicted_en < 0.5
    assert negated_tr < 0.1


@slow
def test_nli_rejects_a_checkpoint_without_an_entailment_label() -> None:
    """Assuming index 0 would silently score contradiction as support."""
    scorer = NLIScorer("distilbert-base-uncased-finetuned-sst-2-english")
    with pytest.raises(ValueError, match="entailment"):
        scorer.score([("a", "b")])
