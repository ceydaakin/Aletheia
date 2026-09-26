"""Records → observations: where the loss definition actually lives."""

from __future__ import annotations

from pathlib import Path

import pytest

from aletheia.contracts import Mode
from aletheia.eval import records
from aletheia.eval.observations import to_observation
from aletheia.eval.records import ClaimRecord, ResponseRecord


def claim(nli: float, *, corrupted: bool = False, on_evidence: bool = False) -> ClaimRecord:
    return ClaimRecord(
        text="c", citations=("x",), corrupted=corrupted, on_evidence=on_evidence,
        scores={"nli": nli, "overlap": 0.9},
    )


def record(*claims: ClaimRecord, answerable: bool = True) -> ResponseRecord:
    return ResponseRecord(
        query_id="q", query="?", lang="en", category="answerable", answerable=answerable,
        retrieved=("x",), retrieved_docs=("a.md",), evidence_retrieved=True,
        answer="a", claims=tuple(claims),
    )


def observe(rec: ResponseRecord, *, mode: Mode = Mode.STRICT, loss: str = "gold", variant="nli"):
    return to_observation(rec, variant=variant, mode=mode, support_threshold=0.5, loss=loss)


def test_gold_loss_counts_a_corrupted_claim_the_verifier_accepted() -> None:
    """The verifier's false accept is exactly what the gold loss exists to catch:
    under the verifier-derived loss this response looks clean."""
    rec = record(claim(0.99), claim(0.97, corrupted=True))
    assert observe(rec, loss="gold").loss is True
    assert observe(rec, loss="verifier").loss is False


def test_strict_mode_removes_a_caught_corruption_before_the_loss() -> None:
    """A corrupted claim the verifier rejects is removed in strict mode, so the
    returned text is clean and the loss is correctly False."""
    rec = record(claim(0.99), claim(0.05, corrupted=True))
    result = observe(rec)
    assert result.loss is False
    assert result.answered is True
    assert result.statistic == pytest.approx(0.01)


def test_flagged_mode_keeps_the_caught_corruption_in_the_loss() -> None:
    """Flagged claims are still returned to the user, so they still count."""
    rec = record(claim(0.99), claim(0.05, corrupted=True))
    assert observe(rec, mode=Mode.FLAGGED).loss is True


def test_nothing_left_after_the_policy_is_not_an_answer() -> None:
    rec = record(claim(0.1), claim(0.2))
    result = observe(rec)
    assert result.answered is False
    assert result.statistic == 1.0


def test_no_claims_is_not_an_answer() -> None:
    assert observe(record()).answered is False


def test_useful_requires_an_uncorrupted_on_evidence_claim() -> None:
    assert observe(record(claim(0.99, on_evidence=True))).useful is True
    assert observe(record(claim(0.99, on_evidence=True, corrupted=True))).useful is False
    assert observe(record(claim(0.99))).useful is False


def test_missing_variant_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError, match="no 'nli_any' scores"):
        observe(record(claim(0.9)), variant="nli_any")


def test_unknown_loss_source_is_rejected() -> None:
    with pytest.raises(ValueError):
        observe(record(claim(0.9)), loss="vibes")


def test_records_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    original = [record(claim(0.9, corrupted=True, on_evidence=True)), record()]
    records.write(path, {"dataset": "t"}, original)
    meta, loaded = records.read(path)
    assert meta["dataset"] == "t"
    assert loaded == original


def test_records_reject_an_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"meta": {"format": 0}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="re-run"):
        records.read(path)


def test_partial_records_resume_and_drop_a_torn_line(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl.partial"
    meta = {"dataset": "t", "seed": 0}
    records.start_partial(path, meta)
    records.append_partial(path, record(claim(0.9)))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"query_id": "torn", "que')
    assert records.read_partial(path, meta) == [record(claim(0.9))]


def test_partial_records_refuse_different_settings(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl.partial"
    records.start_partial(path, {"dataset": "t", "seed": 0})
    with pytest.raises(ValueError, match="cannot resume"):
        records.read_partial(path, {"dataset": "t", "seed": 1})
