"""Calibration: fit a threshold, certify it, store it.

    python -m aletheia.eval.calibrate --dataset ../eval/datasets/bootstrap-tr --alpha 0.05

Lives under ``eval`` rather than ``risk`` because it drives the entire pipeline —
retrieval, generation, verification — and a service may not import its siblings
(ADR-0003). The risk *service* only reads what this writes, through
:mod:`aletheia.risk.store`.

Runs the whole pipeline over a labelled query set, collects one
``(statistic, loss)`` pair per response, then sweeps a lambda grid and certifies
with Learn-then-Test (:mod:`aletheia.risk.ltt`).

**The split is leak-free and that is not negotiable.** Half the queries choose the
threshold, the other half never touch it and are used only to report what the
bound actually delivered. A threshold selected on the data it is then evaluated
on produces a guarantee that is arithmetically valid and empirically worthless.

**What the resulting bound covers depends on the backends it ran with**, and the
run records them. Calibrated against extractive generation, the loss can only
come from retrieval and verifier strictness, because extractive answers cannot
hallucinate. Calibrated against the overlap verifier, the labels themselves are
close to noise. Neither is a reason not to run it — the machinery has to be
correct before the inputs are — but the report must not quote a number without
the configuration that produced it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from aletheia.contracts import Claim, ClaimStatus, Mode
from aletheia.db import Database, configure_event_loop
from aletheia.embedding import NullEmbedder, get_embedder
from aletheia.eval.dataset import Dataset, Query, load
from aletheia.eval.retrieval import TENANT_PREFIX, doc_chunks, ensure_tenant, ingest_corpus
from aletheia.generation.providers import build_answer, get_generator
from aletheia.retrieval.search import dense_search, lexical_search, reciprocal_rank_fusion
from aletheia.risk import ltt, store
from aletheia.risk.statistic import (
    STATISTIC_NAME,
    apply_action_policy,
    response_loss,
    risk_statistic,
)
from aletheia.service import configure_logging
from aletheia.settings import Settings, get_settings
from aletheia.verifier.nli import get_scorer

# Lambda grid. Fine enough that the chosen threshold is not an artefact of the
# spacing, coarse enough that the Bonferroni correction stays affordable — every
# extra point raises the bar each candidate has to clear.
GRID = tuple(round(0.02 * i, 2) for i in range(1, 51))


@dataclass(frozen=True)
class Observation:
    """One labelled response."""

    query_id: str
    query: str
    answer: str
    statistic: float
    loss: bool
    """True if the response contains an unsupported claim."""
    answered: bool
    """False when the pipeline produced nothing to return regardless of threshold."""


async def observe(
    db: Database,
    tenant_id: str,
    query: Query,
    *,
    settings: Settings,
    generator,
    scorer,
    embedder,
    mode: Mode,
) -> Observation:
    """Run one query end to end and label the response."""
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
    chunks = [f.candidate for f in fused[: settings.retrieval_top_n]]
    from aletheia.contracts import Chunk

    retrieved = [
        Chunk(
            chunk_id=c.chunk_id, doc_id=c.doc_id, version=c.version,
            title=c.title, text=c.text, score=0.0,
        )
        for c in chunks
    ]

    answer, drafts = build_answer(
        generator, query.text, retrieved, max_claims=settings.generation_max_claims
    )
    if not drafts:
        return Observation(query.id, query.text, "", 1.0, False, answered=False)

    by_id = {c.chunk_id: c for c in retrieved}
    pairs, indices = [], []
    for index, draft in enumerate(drafts):
        premise = "\n\n".join(by_id[c].text for c in draft.citations if c in by_id)
        if premise:
            pairs.append((premise, draft.text))
            indices.append(index)

    scores = [0.0] * len(drafts)
    for index, score in zip(indices, scorer.score(pairs) if pairs else [], strict=True):
        scores[index] = float(score)

    verified = [
        Claim(
            text=d.text,
            citations=d.citations,
            support_score=round(s, 4),
            status=(
                ClaimStatus.SUPPORTED
                if s >= settings.support_threshold
                else ClaimStatus.UNSUPPORTED
            ),
        )
        for d, s in zip(drafts, scores, strict=True)
    ]

    edited = apply_action_policy(verified, mode)
    return Observation(
        query_id=query.id,
        query=query.text,
        answer=answer,
        statistic=risk_statistic(edited),
        loss=response_loss(edited),
        answered=True,
    )


def candidates(observations: list[Observation], grid=GRID) -> list[ltt.LambdaCandidate]:
    """Evaluate the lambda grid on a labelled set.

    At threshold lambda we answer when ``statistic <= lambda``. An abstention
    carries no loss — declining to answer is never wrong — so only answered
    responses can contribute failures. That asymmetry is what makes a very small
    lambda trivially safe and useless, and the grid search's job is to find the
    largest one that is still certifiable.
    """
    return [
        ltt.LambdaCandidate(
            value=lam,
            failures=sum(1 for o in observations if o.answered and o.statistic <= lam and o.loss),
            answered=sum(1 for o in observations if o.answered and o.statistic <= lam),
        )
        for lam in grid
    ]


def evaluate_at(observations: list[Observation], threshold: float) -> dict[str, float]:
    """What a threshold actually delivered on held-out data."""
    answered = [o for o in observations if o.answered and o.statistic <= threshold]
    failures = sum(1 for o in answered if o.loss)
    return {
        "n": float(len(observations)),
        "answer_rate": len(answered) / len(observations) if observations else 0.0,
        "empirical_risk": failures / len(answered) if answered else 0.0,
        "failures": float(failures),
    }


def risk_coverage_curve(observations: list[Observation], grid=GRID) -> list[dict[str, float]]:
    """The risk–coverage curve: the plot that shows what the guarantee costs.

    Reporting a single (alpha, answer rate) pair hides whether the system is near
    a cliff or on a plateau, which is the difference between a usable product and
    a lucky threshold.
    """
    return [
        {"threshold": lam, **evaluate_at(observations, lam)} for lam in grid
    ]


def split(
    observations: list[Observation], fraction: float = 0.5
) -> tuple[list[Observation], list[Observation]]:
    """Deterministic, leak-free split. ``fraction`` goes to calibration.

    Strided rather than randomised: no seed to record, no shuffle to reproduce,
    and the two parts stay balanced across whatever order the dataset happens to
    be in.

    Weighting towards calibration is a real lever, not a fudge. Certification has
    a hard sample-size floor (:func:`aletheia.risk.ltt.minimum_certifiable_n`)
    while the test part only has to sanity-check the resulting threshold, so a
    60/40 split can certify where 50/50 cannot. The cost is a noisier held-out
    estimate, which is why the calibration job prints the test size next to the
    number.
    """
    if not 0.0 < fraction < 1.0:
        raise ValueError("fraction must lie in (0, 1)")

    # Place every 1/(1-fraction)-th item into test, so the two parts interleave
    # instead of test being a contiguous tail of the dataset.
    calibration: list[Observation] = []
    test: list[Observation] = []
    accumulated = 0.0
    for observation in observations:
        accumulated += 1.0 - fraction
        if accumulated >= 1.0:
            accumulated -= 1.0
            test.append(observation)
        else:
            calibration.append(observation)
    return calibration, test


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("warning")

    dataset: Dataset = load(Path(args.dataset))
    tenant_id = TENANT_PREFIX + dataset.name
    mode = Mode(args.mode)

    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        await ensure_tenant(db, tenant_id)
        if args.ingest:
            count = await ingest_corpus(db, dataset, tenant_id, settings)
            print(f"corpus: {count} document(s)")
        if not await doc_chunks(db, tenant_id):
            print(f"no chunks for {tenant_id!r}; run with --ingest", file=sys.stderr)
            return 2

        generator = get_generator(settings)
        scorer = get_scorer(settings)
        embedder = get_embedder(settings)

        print(
            f"\nbackends: generation={generator.name} verifier={scorer.name} "
            f"embedding={embedder.name} mode={mode.value}"
        )
        if generator.name == "extractive":
            print(
                "NOTE: extractive generation cannot hallucinate, so the loss below\n"
                "      reflects retrieval quality and verifier strictness, not\n"
                "      unsupported generation. This bound does not transfer to a\n"
                "      generative system."
            )

        queries = list(dataset.queries)
        print(f"\nrunning {len(queries)} queries...")
        observations = []
        for index, query in enumerate(queries, start=1):
            observations.append(
                await observe(
                    db, tenant_id, query,
                    settings=settings, generator=generator, scorer=scorer,
                    embedder=embedder, mode=mode,
                )
            )
            if index % 10 == 0:
                print(f"  {index}/{len(queries)}", flush=True)

        calibration_set, test_set = split(observations, args.calibration_fraction)
        selection = ltt.select(
            candidates(calibration_set), n=len(calibration_set),
            alpha=args.alpha, delta=args.delta,
        )

        calibration_id = args.calibration_id or f"cal_{dataset.name}_{uuid.uuid4().hex[:8]}"
        async with db.transaction() as conn:
            cur = await conn.execute("SELECT now() AS t")
            corpus_known_at = (await cur.fetchone())["t"]
            record = await store.save(
                conn,
                calibration_id=calibration_id,
                tenant_id=tenant_id,
                selection=selection,
                corpus_known_at=corpus_known_at,
                statistic_name=STATISTIC_NAME,
                lang=dataset.lang,
            )
            await store.save_examples(
                conn, calibration_id,
                [
                    (o.query, o.answer, o.statistic, o.loss, "verifier",
                     "calibration" if o in calibration_set else "test")
                    for o in observations
                ],
            )

        held_out = evaluate_at(test_set, selection.lambda_value)
        print(f"\ncalibration_id={calibration_id}")
        print(f"  alpha={args.alpha}  delta={args.delta}  statistic={STATISTIC_NAME}")
        print(f"  calibration n={len(calibration_set)}   test n={len(test_set)}")
        print(f"\n  certified: {selection.certified}")
        if selection.certified:
            print(f"  threshold (lambda):     {selection.lambda_value:.2f}")
            print(f"  coverage on calibration:{selection.coverage:>7.3f}")
            print(f"  risk on calibration:    {selection.empirical_risk:>7.3f}")
            print(f"\n  HELD OUT — answer rate: {held_out['answer_rate']:>7.3f}   "
                  f"empirical risk: {held_out['empirical_risk']:.3f}")
            if held_out["empirical_risk"] > args.alpha:
                print(
                    f"  WARNING: held-out risk exceeds alpha={args.alpha}. With "
                    f"n={len(test_set)} this is within sampling noise, but it is "
                    "reported rather than hidden."
                )
        else:
            floor = ltt.minimum_certifiable_n(args.alpha, args.delta, len(GRID))
            print(
                "  No threshold on the grid could be certified at this alpha.\n"
                "  The honest outcome is to abstain on everything, not to ship the\n"
                "  least-bad threshold."
            )
            answered = max((c.answered for c in candidates(calibration_set)), default=0)
            if answered < floor:
                print(
                    f"\n  This is a sample-size floor, not a quality verdict: at "
                    f"alpha={args.alpha:g}, delta={args.delta:g} and a {len(GRID)}-point\n"
                    f"  grid, certification needs at least {floor} answered calibration\n"
                    f"  responses even with zero observed failures. The best threshold\n"
                    f"  here answered {answered}."
                )
            else:
                print(
                    f"\n  The sample is large enough ({answered} answered, floor is "
                    f"{floor}), so this is a quality result: the verifier is finding\n"
                    "  unsupported claims too often to bound at this alpha."
                )
        print(f"\n  {record.guarantee()}")

        if args.curve:
            Path(args.curve).parent.mkdir(parents=True, exist_ok=True)
            Path(args.curve).write_text(
                json.dumps(
                    {
                        "calibration_id": calibration_id,
                        "alpha": args.alpha,
                        "backends": {
                            "generation": generator.name,
                            "verifier": scorer.name,
                            "embedding": embedder.name,
                        },
                        "calibration": risk_coverage_curve(calibration_set),
                        "test": risk_coverage_curve(test_set),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\nwrote {args.curve}")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-calibrate", description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--alpha", type=float, default=0.05, help="tolerated risk")
    parser.add_argument("--delta", type=float, default=0.05, help="1 - confidence")
    parser.add_argument("--mode", default="strict", choices=[m.value for m in Mode])
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--calibration-id", default="")
    parser.add_argument(
        "--calibration-fraction", type=float, default=0.5,
        help="share of queries used to choose the threshold; the rest is held out",
    )
    parser.add_argument("--curve", default="", help="write the risk-coverage curve here")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args(argv)

    configure_event_loop()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
