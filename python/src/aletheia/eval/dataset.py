"""Evaluation datasets: a corpus plus labelled queries.

A dataset is a directory:

    <name>/
      dataset.json      metadata and the query list
      corpus/           the documents, ingested under a dedicated tenant

Gold labels are **document-level**, not chunk-level, and that is deliberate.
Chunk ids embed a version and an ordinal (``policy.md:v1:chunk_0003``), so any
change to chunk size, overlap, or the parser would invalidate every hand-written
label — which would make the labels a hostage to tuning decisions that are
supposed to be measured against them. Labels name documents; the harness resolves
them to the chunk ids that exist at evaluation time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Query:
    id: str
    text: str
    lang: str = ""
    relevant_docs: tuple[str, ...] = ()
    """Document ids that answer this query. Empty means deliberately unanswerable
    — the corpus does not contain the answer, and retrieval returning nothing is
    the correct outcome (PRD §7.1, TR-Adversarial)."""
    category: str = "answerable"
    note: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.relevant_docs)


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    lang: str
    root: Path
    queries: tuple[Query, ...] = field(default_factory=tuple)

    @property
    def corpus_dir(self) -> Path:
        return self.root / "corpus"

    def answerable(self) -> list[Query]:
        return [q for q in self.queries if q.answerable]

    def unanswerable(self) -> list[Query]:
        return [q for q in self.queries if not q.answerable]


def load(root: Path) -> Dataset:
    # Resolved so that corpus paths can become file:// URIs regardless of where
    # the harness was invoked from.
    root = Path(root).resolve()
    manifest = json.loads((root / "dataset.json").read_text(encoding="utf-8"))

    queries = tuple(
        Query(
            id=q["id"],
            text=q["text"],
            lang=q.get("lang", manifest.get("lang", "")),
            relevant_docs=tuple(q.get("relevant_docs", ())),
            category=q.get("category", "answerable" if q.get("relevant_docs") else "unanswerable"),
            note=q.get("note", ""),
        )
        for q in manifest["queries"]
    )

    seen: set[str] = set()
    for query in queries:
        if query.id in seen:
            raise ValueError(f"{root}: duplicate query id {query.id!r}")
        seen.add(query.id)

    dataset = Dataset(
        name=manifest["name"],
        description=manifest.get("description", ""),
        lang=manifest.get("lang", ""),
        root=root,
        queries=queries,
    )
    _check_labels_resolve(dataset)
    return dataset


def _check_labels_resolve(dataset: Dataset) -> None:
    """Every labelled document must exist in the corpus.

    A typo in a gold label is invisible in the results — it just looks like a
    retrieval failure — so it has to fail at load time instead.
    """
    if not dataset.corpus_dir.is_dir():
        return
    available = {
        p.relative_to(dataset.corpus_dir).as_posix()
        for p in dataset.corpus_dir.rglob("*")
        if p.is_file()
    }
    missing = {
        doc
        for query in dataset.queries
        for doc in query.relevant_docs
        if doc not in available
    }
    if missing:
        raise ValueError(
            f"{dataset.name}: gold labels name documents that are not in the corpus: "
            + ", ".join(sorted(missing))
        )
