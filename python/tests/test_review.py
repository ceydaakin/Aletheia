"""The review tool's pure parts: decisions and evidence context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia.eval.review import context, read_manifest, with_decision, write_manifest

MANIFEST = {
    "name": "t",
    "queries": [
        {"id": "a", "text": "?", "status": "draft"},
        {"id": "b", "text": "?", "status": "draft"},
    ],
}


def test_decision_returns_a_new_manifest() -> None:
    updated = with_decision(MANIFEST, "a", "verified")
    assert updated["queries"][0]["status"] == "verified"
    assert MANIFEST["queries"][0]["status"] == "draft", "input must not be mutated"
    assert updated["queries"][1] == MANIFEST["queries"][1]


def test_edit_changes_text_and_keeps_draft() -> None:
    updated = with_decision(MANIFEST, "b", "draft", text="better question?")
    assert updated["queries"][1] == {"id": "b", "text": "better question?", "status": "draft"}


def test_unknown_id_and_status() -> None:
    with pytest.raises(KeyError):
        with_decision(MANIFEST, "zzz", "verified")
    with pytest.raises(ValueError):
        with_decision(MANIFEST, "a", "approved")


def test_context_marks_the_quote_across_line_breaks() -> None:
    doc = "Intro text.\nNotice must be given\nthirty (30) days in advance. Tail."
    shown = context(doc, "Notice must be given thirty (30) days in advance.", width=12)
    assert "»Notice must be given thirty (30) days in advance.«" in shown
    assert shown.startswith("Intro text. ")


def test_context_reports_a_missing_quote() -> None:
    assert context("abc", "xyz").startswith("[quote not found")


def test_write_is_atomic_and_round_trips(tmp_path: Path) -> None:
    write_manifest(tmp_path, MANIFEST)
    assert read_manifest(tmp_path) == MANIFEST
    assert not list(tmp_path.glob(".dataset.*")), "temporary file left behind"
    assert json.loads((tmp_path / "dataset.json").read_text(encoding="utf-8"))["name"] == "t"
