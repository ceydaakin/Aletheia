"""Dataset loading: the checks that stop a bad label from looking like a result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia.eval.dataset import load, normalise


def _write(root: Path, queries: list[dict], docs: dict[str, str]) -> Path:
    (root / "corpus").mkdir(parents=True)
    for name, text in docs.items():
        (root / "corpus" / name).write_text(text, encoding="utf-8")
    (root / "dataset.json").write_text(
        json.dumps({"name": "t", "lang": "en", "queries": queries}), encoding="utf-8"
    )
    return root


DOC = {"a.md": "# A\n\nNotice must be given\nthirty (30) days in advance.\n"}


def test_evidence_matches_across_line_breaks(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        [{"id": "q1", "text": "?", "relevant_docs": ["a.md"],
          "evidence": ["Notice must be given thirty (30) days in advance."]}],
        DOC,
    )
    dataset = load(root)
    assert dataset.queries[0].evidence == ("Notice must be given thirty (30) days in advance.",)


def test_paraphrased_evidence_fails_loudly(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        [{"id": "q1", "text": "?", "relevant_docs": ["a.md"],
          "evidence": ["Notice is due thirty days ahead."]}],
        DOC,
    )
    with pytest.raises(ValueError, match="not found verbatim"):
        load(root)


def test_evidence_must_come_from_a_relevant_document(tmp_path: Path) -> None:
    docs = {**DOC, "b.md": "Something else entirely."}
    root = _write(
        tmp_path,
        [{"id": "q1", "text": "?", "relevant_docs": ["b.md"],
          "evidence": ["thirty (30) days in advance"]}],
        docs,
    )
    with pytest.raises(ValueError, match="not found verbatim"):
        load(root)


def test_status_filtering(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        [
            {"id": "d", "text": "?", "relevant_docs": ["a.md"], "status": "draft"},
            {"id": "v", "text": "?", "relevant_docs": ["a.md"], "status": "verified"},
            {"id": "r", "text": "?", "relevant_docs": ["a.md"], "status": "rejected"},
            {"id": "legacy", "text": "?", "relevant_docs": ["a.md"]},
        ],
        DOC,
    )
    assert [q.id for q in load(root).queries] == ["d", "v", "legacy"]
    assert [q.id for q in load(root, verified_only=True).queries] == ["v", "legacy"]


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    root = _write(
        tmp_path, [{"id": "x", "text": "?", "relevant_docs": ["a.md"], "status": "ok"}], DOC
    )
    with pytest.raises(ValueError, match="unknown status"):
        load(root)


def test_normalise_is_whitespace_and_case_insensitive() -> None:
    assert normalise("  Otuz\n(30)   GÜN ") == normalise("otuz (30) gün")


def test_bootstrap_dataset_still_loads() -> None:
    root = Path(__file__).resolve().parents[2] / "eval" / "datasets" / "bootstrap-tr"
    dataset = load(root)
    assert len(dataset.queries) == 27
