"""What one pipeline run over a dataset leaves on disk.

Running retrieval, generation and three verifier variants over a thousand
queries takes minutes; certifying a threshold from the result takes
milliseconds. Keeping the two apart means every ablation, every alpha, every
random split and the cross-lingual experiment read the *same* responses — a
difference between two rows of a table is then a difference in the method, not
in which run happened to produce the inputs (PRD §9: "eval cache on disk").

A records file is JSON Lines. The first line is ``{"meta": {...}}`` — dataset,
backends, hallucination rate, seed — and every other line is one
:class:`ResponseRecord`. Nothing in a record depends on the risk threshold, the
action mode, or the support threshold: those are applied afterwards by
:mod:`aletheia.eval.observations`, which is what makes them cheap to vary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1


@dataclass(frozen=True)
class ClaimRecord:
    text: str
    citations: tuple[str, ...]
    corrupted: bool
    """Ground truth: this claim was rewritten so that its evidence no longer
    supports it (:mod:`aletheia.eval.hallucinate`). Uncorrupted claims are copied
    from their cited chunk and supported by construction."""
    kind: str = ""
    on_evidence: bool = False
    """The claim (before any corruption) restates the query's gold evidence —
    i.e. it would actually answer the question."""
    scores: dict[str, float] = field(default_factory=dict)
    """Support score per verifier variant, e.g. ``{"nli": 0.98, "overlap": 0.7}``."""


@dataclass(frozen=True)
class ResponseRecord:
    query_id: str
    query: str
    lang: str
    category: str
    answerable: bool
    retrieved: tuple[str, ...]
    retrieved_docs: tuple[str, ...]
    evidence_retrieved: bool | None
    """None when the query carries no evidence labels."""
    answer: str
    claims: tuple[ClaimRecord, ...]
    status: str = "verified"
    relevant_docs: tuple[str, ...] = ()

    @property
    def variants(self) -> set[str]:
        return set().union(*(c.scores for c in self.claims)) if self.claims else set()


def _claim_from(data: dict[str, Any]) -> ClaimRecord:
    return ClaimRecord(
        text=data["text"],
        citations=tuple(data.get("citations", ())),
        corrupted=bool(data["corrupted"]),
        kind=data.get("kind", ""),
        on_evidence=bool(data.get("on_evidence", False)),
        scores={k: float(v) for k, v in data.get("scores", {}).items()},
    )


def record_from(data: dict[str, Any]) -> ResponseRecord:
    return ResponseRecord(
        query_id=data["query_id"],
        query=data.get("query", ""),
        lang=data["lang"],
        category=data["category"],
        answerable=bool(data["answerable"]),
        retrieved=tuple(data.get("retrieved", ())),
        retrieved_docs=tuple(data.get("retrieved_docs", ())),
        evidence_retrieved=data.get("evidence_retrieved"),
        answer=data.get("answer", ""),
        claims=tuple(_claim_from(c) for c in data.get("claims", ())),
        status=data.get("status", "verified"),
        relevant_docs=tuple(data.get("relevant_docs", ())),
    )


def write(path: Path, meta: dict[str, Any], records: list[ResponseRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"meta": {**meta, "format": FORMAT_VERSION}}, ensure_ascii=False))
        handle.write("\n")
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False))
            handle.write("\n")


def read(path: Path) -> tuple[dict[str, Any], list[ResponseRecord]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path}: empty records file")
    header = json.loads(lines[0])
    if "meta" not in header:
        raise ValueError(f"{path}: first line must be a {{'meta': ...}} header")
    meta = header["meta"]
    if meta.get("format") != FORMAT_VERSION:
        raise ValueError(
            f"{path}: records format {meta.get('format')!r}, expected {FORMAT_VERSION}; "
            "re-run aletheia.eval.collect"
        )
    return meta, [record_from(json.loads(line)) for line in lines[1:] if line.strip()]


# ---------------------------------------------------------------------------
# Incremental writing, so a crash mid-run costs one query rather than the run
# ---------------------------------------------------------------------------

# Meta keys that must match for a partial run to be resumed: anything that
# changes what a record would contain.
RESUME_KEYS = (
    "dataset", "generation", "embedding", "reranker", "nli_model", "verifier_max_length",
    "variants", "hallucination_rate", "seed", "max_claims", "top_n",
)


def start_partial(path: Path, meta: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"meta": {**meta, "format": FORMAT_VERSION}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_partial(path: Path, record: ResponseRecord) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        handle.flush()


def read_partial(path: Path, meta: dict[str, Any]) -> list[ResponseRecord]:
    """Records already collected by an interrupted run with the same settings.

    A torn final line — the process died mid-write — is dropped, not trusted.
    A partial file from different settings is refused: mixing two
    configurations in one records file would make every table a blend.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    header = json.loads(lines[0]).get("meta", {})
    mismatched = [k for k in RESUME_KEYS if header.get(k) != meta.get(k)]
    if mismatched:
        raise ValueError(f"{path}: cannot resume, settings differ in {mismatched}")
    out = []
    for line in lines[1:]:
        try:
            out.append(record_from(json.loads(line)))
        except (json.JSONDecodeError, KeyError):
            break
    return out
