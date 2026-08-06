"""Retrieval evaluation: load a dataset, run the arms, report the numbers.

    python -m aletheia.eval.retrieval --dataset ../eval/datasets/bootstrap-en --ingest

Runs against the search functions directly rather than over HTTP. The measurement
is of retrieval quality, and putting a network hop and a reranker in the way would
only add variance to a number that is supposed to isolate the arms.

Every configuration in the ablation table is produced by the same code path, so a
difference between rows is a difference in retrieval and not in harness plumbing
(PRD §7.3).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aletheia.db import Database, configure_event_loop
from aletheia.embedding import NullEmbedder, get_embedder
from aletheia.eval import metrics
from aletheia.eval.dataset import Dataset, Query, load
from aletheia.ingestion import pipeline
from aletheia.ingestion.messages import IngestMessage
from aletheia.retrieval.search import (
    Fused,
    dense_search,
    lexical_search,
    reciprocal_rank_fusion,
)
from aletheia.service import configure_logging
from aletheia.settings import Settings, get_settings

# Each dataset gets its own tenant so that evaluating one corpus can never
# retrieve from another — tenant isolation doubles as run isolation.
TENANT_PREFIX = "eval_"


@dataclass(frozen=True)
class Config:
    """One row of the ablation table."""

    name: str
    lexical: bool = True
    dense: bool = True

    @property
    def runnable(self) -> bool:
        return self.lexical or self.dense


ABLATIONS = (
    Config("lexical-only", lexical=True, dense=False),
    Config("dense-only", lexical=False, dense=True),
    Config("hybrid-rrf", lexical=True, dense=True),
)


async def ensure_tenant(db: Database, tenant_id: str) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, api_key_hash)
            VALUES (%s, %s, 'eval') ON CONFLICT (tenant_id) DO NOTHING
            """,
            (tenant_id, tenant_id),
        )


async def ingest_corpus(db: Database, dataset: Dataset, tenant_id: str, settings: Settings) -> int:
    """Load the dataset's corpus. Idempotent — re-running is a no-op (ADR-0005)."""
    embedder = get_embedder(settings)
    count = 0
    for path in sorted(p for p in dataset.corpus_dir.rglob("*") if p.is_file()):
        doc_id = path.relative_to(dataset.corpus_dir).as_posix()
        message = IngestMessage(
            job_id=f"eval_{count}",
            tenant_id=tenant_id,
            doc_id=doc_id,
            filename=path.name,
            lang=dataset.lang,
            source_uri=path.as_uri(),
        )
        await pipeline.run(db, message, settings=settings, embedder=embedder)
        count += 1
    return count


async def doc_chunks(db: Database, tenant_id: str) -> dict[str, set[str]]:
    """Map each document to the chunk ids currently in force.

    This is what turns document-level gold labels into the chunk ids the metrics
    compare against, so labels survive a change to chunk size or the parser.
    """
    async with db.connection() as conn:
        cur = await conn.execute(
            """
            SELECT doc_id, chunk_id FROM chunks
            WHERE tenant_id = %s
              AND valid_to = forever() AND superseded_at = forever()
            """,
            (tenant_id,),
        )
        rows = await cur.fetchall()

    mapping: dict[str, set[str]] = {}
    for row in rows:
        mapping.setdefault(row["doc_id"], set()).add(row["chunk_id"])
    return mapping


async def run_query(
    db: Database,
    tenant_id: str,
    query: Query,
    config: Config,
    settings: Settings,
    embedder,
) -> list[Fused]:
    arms: dict[str, list] = {}

    # Temporal bounds left to the database, as in the service.
    async with db.connection() as conn:
        if config.lexical:
            arms["lexical"] = await lexical_search(
                conn, tenant_id, query.text,
                k=settings.retrieval_lexical_k, lang=query.lang,
            )
        if config.dense and not isinstance(embedder, NullEmbedder):
            vectors = embedder.embed([query.text])
            if vectors and vectors[0] is not None:
                arms["dense"] = await dense_search(
                    conn, tenant_id, vectors[0], k=settings.retrieval_dense_k,
                )

    return reciprocal_rank_fusion(arms, k=settings.retrieval_rrf_k)


async def evaluate(
    db: Database, dataset: Dataset, tenant_id: str, config: Config, settings: Settings
) -> dict:
    embedder = get_embedder(settings)
    chunks_by_doc = await doc_chunks(db, tenant_id)

    graded: list[tuple[Sequence[str], set[str]]] = []
    for query in dataset.answerable():
        gold = set().union(*(chunks_by_doc.get(doc, set()) for doc in query.relevant_docs)) \
            if query.relevant_docs else set()
        fused = await run_query(db, tenant_id, query, config, settings, embedder)
        graded.append(([f.candidate.chunk_id for f in fused], gold))

    summary = metrics.summarise(graded)

    # Unanswerable queries are scored separately and inverted: retrieving nothing
    # is the correct outcome, so a high number here would be a failure. Averaging
    # them into recall would let a system that retrieves everything look good.
    unanswerable = dataset.unanswerable()
    if unanswerable:
        empty = 0
        for query in unanswerable:
            fused = await run_query(db, tenant_id, query, config, settings, embedder)
            empty += 1 if not fused else 0
        summary["unanswerable_empty_rate"] = empty / len(unanswerable)
        summary["unanswerable_queries"] = float(len(unanswerable))

    return summary


def format_table(rows: dict[str, dict]) -> str:
    # recall@1 and @3 are here because they are the columns that discriminate on a
    # small corpus: when the corpus has 12 chunks, a top-10 cut returns most of it
    # and recall@10 approaches 1.0 for any system that returns anything.
    columns = ["recall@1", "recall@3", "recall@5", "recall@10", "ndcg@10", "mrr"]
    header = f"{'configuration':<16}" + "".join(f"{c:>11}" for c in columns)
    lines = [header, "-" * len(header)]
    for name, summary in rows.items():
        cells = "".join(f"{summary.get(c, float('nan')):>11.3f}" for c in columns)
        lines.append(f"{name:<16}{cells}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("warning")

    dataset = load(Path(args.dataset))
    tenant_id = TENANT_PREFIX + dataset.name

    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        await ensure_tenant(db, tenant_id)
        if args.ingest:
            count = await ingest_corpus(db, dataset, tenant_id, settings)
            print(f"corpus: {count} document(s) under tenant {tenant_id!r}\n")

        chunks_by_doc = await doc_chunks(db, tenant_id)
        if not chunks_by_doc:
            print(
                f"no chunks for tenant {tenant_id!r}; run again with --ingest",
                file=sys.stderr,
            )
            return 2

        embedder = get_embedder(settings)
        dense_available = not isinstance(embedder, NullEmbedder)

        results: dict[str, dict] = {}
        for config in ABLATIONS:
            if config.dense and not config.lexical and not dense_available:
                # Reporting a dense-only row of zeros when there are no embeddings
                # would read as "dense retrieval is useless" rather than "dense
                # retrieval was not run".
                print(f"skipping {config.name}: EMBEDDING_BACKEND is 'null'")
                continue
            results[config.name] = await evaluate(db, dataset, tenant_id, config, settings)

        print(f"\n{dataset.name} — {len(dataset.answerable())} answerable, "
              f"{len(dataset.unanswerable())} unanswerable, "
              f"{sum(len(v) for v in chunks_by_doc.values())} chunks\n")
        print(format_table(results))

        if any("unanswerable_empty_rate" in s for s in results.values()):
            print("\nunanswerable queries returning nothing at all:")
            for name, summary in results.items():
                if "unanswerable_empty_rate" in summary:
                    print(f"  {name:<16}{summary['unanswerable_empty_rate']:.3f}")
            print(
                "  (a low number is expected, not a defect: disjunctive matching\n"
                "   retrieves anything sharing a term, so retrieval alone cannot\n"
                "   identify out-of-corpus questions. That is the verifier's and\n"
                "   the risk controller's job — PRD F5/F6.)"
            )

        chunk_total = sum(len(v) for v in chunks_by_doc.values())
        if chunk_total < 200:
            print(
                f"\nNOTE: {chunk_total} chunks. At this size a top-10 cut returns most\n"
                "of the corpus, so recall@10 is close to uninformative — read recall@1\n"
                "and recall@3 instead. Week 4 replaces this with 400 QA triples."
            )

        if args.json:
            Path(args.json).write_text(
                json.dumps(
                    {
                        "dataset": dataset.name,
                        "embedder": embedder.name,
                        "embedding_model": settings.embedding_model if dense_available else "",
                        "results": results,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\nwrote {args.json}")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-eval-retrieval", description=__doc__)
    parser.add_argument("--dataset", required=True, help="path to a dataset directory")
    parser.add_argument("--ingest", action="store_true", help="load the corpus first")
    parser.add_argument("--json", default="", help="also write results to this file")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args(argv)

    configure_event_loop()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
