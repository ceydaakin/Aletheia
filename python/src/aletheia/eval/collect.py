"""Run the pipeline over a dataset once and record everything a threshold needs.

    python -m aletheia.eval.collect --dataset ../eval/datasets/kvkk-tr --ingest \\
        --out ../eval/results/records/kvkk-tr.jsonl --rate 0.15 --verifiers nli,overlap

Per query: hybrid retrieval (lexical + dense when embeddings exist, fused with
RRF, then reranked — the same order of operations as the retrieval service),
generation, decomposition, controlled hallucination at ``--rate``, and a support
score for every claim under every requested verifier variant:

``nli``      entailment against the chunks the claim cites, whole.
``nli_window`` the same evidence, scored as overlapping two-sentence windows and
             maxed (SummaC-style): what the NLI model was trained on.
``nli_any``  entailment against the best of *all* retrieved chunks, citations
             ignored — ablation (c), "no citation forcing". A claim may borrow
             support from any passage, which is the failure citations prevent.
``overlap``  lexical overlap against the cited chunks. The model-free baseline.

Nothing here depends on the risk threshold, the mode or the support threshold,
so :mod:`aletheia.eval.experiments` derives every configuration from one file.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import sys
import time
from pathlib import Path

from aletheia.contracts import Chunk
from aletheia.db import Database, configure_event_loop
from aletheia.embedding import NullEmbedder, get_embedder
from aletheia.eval import records
from aletheia.eval.dataset import Dataset, Query, load, normalise
from aletheia.eval.hallucinate import inject
from aletheia.eval.records import ClaimRecord, ResponseRecord
from aletheia.eval.retrieval import TENANT_PREFIX, doc_chunks, ensure_tenant, ingest_corpus
from aletheia.generation.providers import build_answer, get_generator
from aletheia.retrieval.rerank import get_reranker
from aletheia.retrieval.search import dense_search, lexical_search, reciprocal_rank_fusion
from aletheia.service import configure_logging
from aletheia.settings import Settings, get_settings
from aletheia.verifier.nli import NLIScorer, OverlapScorer, WindowedNLIScorer, get_scorer

VARIANTS = ("nli", "nli_window", "nli_any", "overlap")

_WORD = re.compile(r"\w+", re.UNICODE)

# How much of a claim must restate a gold evidence passage for the claim to
# count as answering the question. Word-set Jaccard, so reordering and
# reflowing do not matter but a different sentence from the same article does.
ON_EVIDENCE_JACCARD = 0.6


async def retrieve(
    db: Database, tenant_id: str, query: Query, *, settings: Settings, embedder, reranker
) -> list[Chunk]:
    async with db.connection() as conn:
        arms = {
            "lexical": await lexical_search(
                conn, tenant_id, query.text, k=settings.retrieval_lexical_k, lang=query.lang
            )
        }
        if not isinstance(embedder, NullEmbedder):
            vectors = embedder.embed([query.text])
            if vectors and vectors[0] is not None:
                arms["dense"] = await dense_search(
                    conn, tenant_id, vectors[0], k=settings.retrieval_dense_k
                )
    fused = reciprocal_rank_fusion(arms, k=settings.retrieval_rrf_k)
    head = reranker.rerank(query.text, fused, top_n=settings.retrieval_top_n)
    return [
        Chunk(
            chunk_id=f.candidate.chunk_id, doc_id=f.candidate.doc_id,
            version=f.candidate.version, title=f.candidate.title,
            text=f.candidate.text, score=round(float(f.score), 6),
        )
        for f in head
    ]


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text)}


def on_evidence(claim: str, evidence: tuple[str, ...]) -> bool:
    """Does this claim restate one of the gold evidence passages?"""
    claim_norm, claim_words = normalise(claim).rstrip("."), _words(claim)
    if not claim_words:
        return False
    for quote in evidence:
        quote_norm = normalise(quote)
        if claim_norm in quote_norm or quote_norm.rstrip(".") in claim_norm:
            return True
        quote_words = _words(quote)
        if len(claim_words & quote_words) / len(claim_words | quote_words) >= ON_EVIDENCE_JACCARD:
            return True
    return False


def evidence_retrieved(chunks: list[Chunk], evidence: tuple[str, ...]) -> bool | None:
    """Did any retrieved chunk contain a gold evidence passage?

    A passage that straddles a chunk boundary is matched on its first 80
    characters: requiring the whole quote would score a boundary as a miss for a
    reason that has nothing to do with retrieval.
    """
    if not evidence:
        return None
    texts = [normalise(c.text) for c in chunks]
    for quote in evidence:
        probe = normalise(quote)[:80]
        if any(probe in text for text in texts):
            return True
    return False


def score_claims(
    claims, chunks: list[Chunk], *, variants: tuple[str, ...], nli: NLIScorer | None,
    window: int = 2,
) -> list[dict[str, float]]:
    """Support score per claim per variant. Uncited claims score zero under the
    cited variants — there is no evidence they claim to rest on."""
    by_id = {c.chunk_id: c for c in chunks}
    cited = ["\n\n".join(by_id[i].text for i in c.citations if i in by_id) for c in claims]
    out: list[dict[str, float]] = [{} for _ in claims]

    if "overlap" in variants:
        pairs = [(p, c.text) for p, c in zip(cited, claims, strict=True)]
        for slot, score in zip(out, OverlapScorer().score(pairs), strict=True):
            slot["overlap"] = round(float(score), 6)

    if nli is not None and "nli" in variants:
        index = [i for i, p in enumerate(cited) if p]
        scores = nli.score([(cited[i], claims[i].text) for i in index])
        for slot in out:
            slot["nli"] = 0.0
        for i, score in zip(index, scores, strict=True):
            out[i]["nli"] = round(float(score), 6)

    if nli is not None and "nli_window" in variants:
        windowed = WindowedNLIScorer(nli, size=window)
        index = [i for i, p in enumerate(cited) if p]
        scores = windowed.score([(cited[i], claims[i].text) for i in index])
        for slot in out:
            slot["nli_window"] = 0.0
        for i, score in zip(index, scores, strict=True):
            out[i]["nli_window"] = round(float(score), 6)

    if nli is not None and "nli_any" in variants:
        pairs = [(chunk.text, c.text) for c in claims for chunk in chunks]
        scores = nli.score(pairs) if pairs else []
        width = len(chunks)
        for i, slot in enumerate(out):
            row = scores[i * width : (i + 1) * width]
            slot["nli_any"] = round(float(max(row)), 6) if row else 0.0
    return out


async def collect_one(
    db: Database, tenant_id: str, query: Query, *, settings: Settings, generator,
    embedder, reranker, nli: NLIScorer | None, variants: tuple[str, ...],
    rate: float, seed: int,
) -> ResponseRecord:
    chunks = await retrieve(
        db, tenant_id, query, settings=settings, embedder=embedder, reranker=reranker
    )
    _, drafts = build_answer(
        generator, query.text, chunks, max_claims=settings.generation_max_claims
    )
    # Seeded per query so a record does not depend on the queries before it:
    # dropping a rejected query leaves every other response unchanged.
    injected = inject(drafts, lang=query.lang or "en", rate=rate, rng=random.Random(f"{seed}:{query.id}"))
    scores = score_claims(
        injected.claims, chunks, variants=variants, nli=nli,
        window=settings.verifier_window_sentences,
    )

    claims = tuple(
        ClaimRecord(
            text=c.text,
            citations=tuple(c.citations),
            corrupted=label.corrupted,
            kind=label.kind,
            on_evidence=on_evidence(label.original or c.text, query.evidence),
            scores=s,
        )
        for c, label, s in zip(injected.claims, injected.labels, scores, strict=True)
    )
    answer = " ".join(c.text + "".join(f" [{i}]" for i in c.citations) for c in claims)
    return ResponseRecord(
        query_id=query.id,
        query=query.text,
        lang=query.lang,
        category=query.category,
        answerable=query.answerable,
        retrieved=tuple(c.chunk_id for c in chunks),
        retrieved_docs=tuple(dict.fromkeys(c.doc_id for c in chunks)),
        evidence_retrieved=evidence_retrieved(chunks, query.evidence),
        answer=answer,
        claims=claims,
        status=query.status,
        relevant_docs=query.relevant_docs,
    )


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("warning")
    variants = tuple(v.strip() for v in args.verifiers.split(",") if v.strip())
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        print(f"unknown verifier variants: {sorted(unknown)}", file=sys.stderr)
        return 2

    dataset: Dataset = load(Path(args.dataset), verified_only=args.verified_only)
    tenant_id = TENANT_PREFIX + dataset.name
    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        await ensure_tenant(db, tenant_id)
        if args.ingest:
            print(f"corpus: {await ingest_corpus(db, dataset, tenant_id, settings)} document(s)")
        if not await doc_chunks(db, tenant_id):
            print(f"no chunks for {tenant_id!r}; run with --ingest", file=sys.stderr)
            return 2

        generator = get_generator(settings)
        embedder = get_embedder(settings)
        reranker = get_reranker(settings)
        nli = None
        if {"nli", "nli_window", "nli_any"} & set(variants):
            scorer = get_scorer(settings.model_copy(update={"verifier_backend": "nli"}))
            assert isinstance(scorer, NLIScorer)
            nli = scorer

        meta = {
            "dataset": dataset.name,
            "lang": dataset.lang,
            "queries": len(dataset.queries),
            "status_counts": dataset.status_counts(),
            "verified_only": args.verified_only,
            "generation": generator.name,
            "embedding": embedder.name,
            "reranker": getattr(reranker, "name", type(reranker).__name__),
            "nli_model": settings.nli_model if nli else "",
            "verifier_max_length": settings.verifier_max_length,
            "verifier_window_sentences": settings.verifier_window_sentences,
            "variants": list(variants),
            "hallucination_rate": args.rate,
            "seed": args.seed,
            "max_claims": settings.generation_max_claims,
            "top_n": settings.retrieval_top_n,
        }
        print(f"collecting {len(dataset.queries)} queries: {meta}")

        partial = Path(str(args.out) + ".partial")
        done: dict[str, ResponseRecord] = {}
        if args.resume and partial.exists():
            done = {r.query_id: r for r in records.read_partial(partial, meta)}
            print(f"resuming: {len(done)} responses already collected")
        else:
            records.start_partial(partial, meta)

        started = time.monotonic()
        for index, query in enumerate(dataset.queries, start=1):
            if query.id not in done:
                record = await collect_one(
                    db, tenant_id, query, settings=settings, generator=generator,
                    embedder=embedder, reranker=reranker, nli=nli, variants=variants,
                    rate=args.rate, seed=args.seed,
                )
                records.append_partial(partial, record)
                done[query.id] = record
            if index % 25 == 0:
                print(f"  {index}/{len(dataset.queries)}  {time.monotonic() - started:.0f}s", flush=True)

        # Final file in dataset order, whatever order a resumed run filled it in.
        out = [done[q.id] for q in dataset.queries]
        records.write(Path(args.out), meta, out)
        partial.unlink(missing_ok=True)
        corrupted = sum(c.corrupted for r in out for c in r.claims)
        total = sum(len(r.claims) for r in out)
        print(f"wrote {args.out}: {len(out)} responses, {total} claims, {corrupted} corrupted")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-collect", description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True, help="records file (JSON Lines)")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--rate", type=float, default=0.15, help="per-claim corruption rate")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verifiers", default="nli,nli_window,nli_any,overlap")
    parser.add_argument("--verified-only", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted run from <out>.partial")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args(argv)
    configure_event_loop()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
