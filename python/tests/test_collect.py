"""The pure parts of the collection harness."""

from __future__ import annotations

import pytest

from aletheia.contracts import Chunk, DraftClaim
from aletheia.eval.collect import evidence_retrieved, on_evidence, score_claims


def chunk(ident: str, text: str) -> Chunk:
    return Chunk(chunk_id=ident, doc_id=f"{ident}.md", version=1, title="", text=text, score=0.0)


class FakeNLI:
    """Entails exactly when the hypothesis appears in the premise."""

    def score(self, pairs):
        return [1.0 if h.rstrip(".") in p else 0.0 for p, h in pairs]


EVIDENCE = ("Notice must be given thirty (30) days in advance.",)


def test_on_evidence_accepts_the_evidence_sentence() -> None:
    assert on_evidence("Notice must be given thirty (30) days in advance.", EVIDENCE)


def test_on_evidence_accepts_a_reflowed_or_partial_restatement() -> None:
    assert on_evidence("notice must be given thirty (30) days", EVIDENCE)


def test_on_evidence_rejects_a_different_sentence() -> None:
    assert not on_evidence("The agreement is governed by Turkish law.", EVIDENCE)


def test_on_evidence_with_no_labels_is_false() -> None:
    assert not on_evidence("anything", ())


def test_evidence_retrieved() -> None:
    hit = [chunk("a", "Clause 4.\nNotice must be given\nthirty (30) days in advance. More text.")]
    miss = [chunk("b", "Unrelated.")]
    assert evidence_retrieved(hit, EVIDENCE) is True
    assert evidence_retrieved(miss, EVIDENCE) is False
    assert evidence_retrieved(miss, ()) is None


def test_citation_forcing_is_what_the_variants_differ_on() -> None:
    """A claim citing the wrong chunk is unsupported under ``nli`` but borrows
    support from another retrieved chunk under ``nli_any`` — ablation (c)."""
    chunks = [chunk("a", "The sky is blue."), chunk("b", "Grass is green.")]
    claims = [DraftClaim(text="Grass is green.", citations=["a"])]
    (scores,) = score_claims(claims, chunks, variants=("nli", "nli_any", "overlap"), nli=FakeNLI())
    assert scores["nli"] == 0.0
    assert scores["nli_any"] == 1.0
    assert 0.0 <= scores["overlap"] <= 1.0


def test_uncited_claims_score_zero_under_cited_variants() -> None:
    chunks = [chunk("a", "Grass is green.")]
    claims = [DraftClaim(text="Grass is green.", citations=[])]
    (scores,) = score_claims(claims, chunks, variants=("nli", "overlap"), nli=FakeNLI())
    assert scores == {"nli": 0.0, "overlap": 0.0}


def test_only_requested_variants_are_scored() -> None:
    chunks = [chunk("a", "Grass is green.")]
    claims = [DraftClaim(text="Grass is green.", citations=["a"])]
    (scores,) = score_claims(claims, chunks, variants=("overlap",), nli=None)
    assert set(scores) == {"overlap"}
    assert scores["overlap"] == pytest.approx(1.0)


def test_window_variant_scores_against_cited_spans() -> None:
    long_chunk = chunk("a", "Filler sentence one here. Grass is green. " + "More filler text follows. " * 10)
    claims = [DraftClaim(text="Grass is green.", citations=["a"])]
    (scores,) = score_claims(claims, [long_chunk], variants=("nli", "nli_window"), nli=FakeNLI())
    assert scores["nli_window"] == 1.0
