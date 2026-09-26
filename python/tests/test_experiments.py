"""The experiments CLI end to end, on synthetic records — no models, no database."""

from __future__ import annotations

import json
import random
from pathlib import Path

from aletheia.eval import experiments, records
from aletheia.eval.records import ClaimRecord, ResponseRecord


def _records(lang: str, n: int, seed: int) -> list[ResponseRecord]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        answerable = i % 5 != 0
        claims = []
        for _ in range(rng.randint(1, 4)):
            corrupted = rng.random() < 0.15
            nli = rng.betavariate(1, 6) if corrupted else rng.betavariate(8, 1)
            claims.append(ClaimRecord(
                text="c", citations=("x",), corrupted=corrupted,
                kind="number" if corrupted else "", on_evidence=answerable and rng.random() < 0.5,
                scores={"nli": nli, "nli_any": min(1.0, nli + 0.1), "overlap": rng.random()},
            ))
        out.append(ResponseRecord(
            query_id=f"q{i:04d}", query="?", lang=lang,
            category="answerable" if answerable else "unanswerable", answerable=answerable,
            retrieved=("a:v1:chunk_0000",), retrieved_docs=("a.md",),
            evidence_retrieved=answerable, answer="a", claims=tuple(claims),
            relevant_docs=("a.md",) if answerable else (),
        ))
    return out


def test_every_table_is_produced(tmp_path: Path) -> None:
    rec_dir = tmp_path / "records"
    for name, lang, seed in (("kvkk-en", "en", 1), ("kvkk-tr", "tr", 2)):
        meta = {"dataset": name, "queries": 1500, "status_counts": {"draft": 1500},
                "hallucination_rate": 0.15, "generation": "extractive",
                "reranker": "cross-encoder", "embedding": "e5"}
        records.write(rec_dir / f"{name}.jsonl", meta, _records(lang, 1500, seed))
    records.write(rec_dir / "kvkk-tr__rate0.3.jsonl",
                  {"dataset": "kvkk-tr", "queries": 1500, "status_counts": {}, "hallucination_rate": 0.3,
                   "generation": "extractive", "reranker": "x", "embedding": "x"},
                  _records("tr", 1500, 3))

    out = tmp_path / "out"
    code = experiments.main([
        "--records-dir", str(rec_dir), "--out-dir", str(out),
        "--datasets", "kvkk-tr,kvkk-en,en-public", "--trials", "3",
    ])
    assert code == 0
    report = (out / "experiments.md").read_text(encoding="utf-8")
    for heading in ("## 1. Guarantee", "## 3. Baselines", "## 4. Cross-lingual",
                    "## 5. Verifier", "## 6. Retrieval", "## 7. Sensitivity"):
        assert heading in report
    assert "Not run (no records): en-public" in report
    assert "not run" in report  # the no-rerank ablation and self-consistency
    data = json.loads((out / "experiments.json").read_text(encoding="utf-8"))
    assert set(data["guarantee"]) == {"kvkk-tr", "kvkk-en"}
    assert "EN → TR@0.1" in data["crosslingual"]
