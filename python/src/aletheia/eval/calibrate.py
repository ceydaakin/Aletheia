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
from pathlib import Path

from aletheia.contracts import Mode
from aletheia.db import Database, configure_event_loop
from aletheia.embedding import get_embedder
from aletheia.eval import records as records_io
from aletheia.eval.collect import collect_one
from aletheia.eval.dataset import Dataset, load
from aletheia.eval.observations import LOSS_SOURCES, Observation, observe_all
from aletheia.eval.retrieval import TENANT_PREFIX, doc_chunks, ensure_tenant, ingest_corpus
from aletheia.generation.providers import get_generator
from aletheia.retrieval.rerank import get_reranker
from aletheia.risk import ltt, store
from aletheia.risk.statistic import STATISTIC_NAME
from aletheia.service import configure_logging
from aletheia.settings import get_settings
from aletheia.verifier.nli import NLIScorer, get_scorer

__all__ = [
    "GRID",
    "Observation",
    "candidates",
    "evaluate_at",
    "risk_coverage_curve",
    "split",
]

# Lambda grid. Fine enough that the chosen threshold is not an artefact of the
# spacing, coarse enough that the Bonferroni correction stays affordable — every
# extra point raises the bar each candidate has to clear.
GRID = tuple(round(0.02 * i, 2) for i in range(1, 51))

# How each loss source is recorded in calibration_examples.label_source.
LABEL_SOURCE = {"gold": "synthetic", "verifier": "verifier"}


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


async def _collect(args, dataset: Dataset, db: Database, tenant_id: str, settings) -> list:
    """Run the pipeline now, the same way :mod:`aletheia.eval.collect` does."""
    generator = get_generator(settings)
    embedder = get_embedder(settings)
    reranker = get_reranker(settings)
    nli = None
    if args.variant.startswith("nli"):
        scorer = get_scorer(settings.model_copy(update={"verifier_backend": "nli"}))
        assert isinstance(scorer, NLIScorer)
        nli = scorer
    print(
        f"\nbackends: generation={generator.name} verifier={args.variant} "
        f"embedding={embedder.name} hallucination_rate={args.rate}"
    )
    out = []
    for index, query in enumerate(dataset.queries, start=1):
        out.append(
            await collect_one(
                db, tenant_id, query, settings=settings, generator=generator,
                embedder=embedder, reranker=reranker, nli=nli, variants=(args.variant,),
                rate=args.rate, seed=args.seed,
            )
        )
        if index % 25 == 0:
            print(f"  {index}/{len(dataset.queries)}", flush=True)
    return out


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("warning")

    dataset: Dataset = load(Path(args.dataset), verified_only=args.verified_only)
    tenant_id = args.tenant or TENANT_PREFIX + dataset.name
    mode = Mode(args.mode)

    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        await ensure_tenant(db, tenant_id)
        if args.records:
            meta, responses = records_io.read(Path(args.records))
            if meta["dataset"] != dataset.name:
                print(f"{args.records} is for {meta['dataset']!r}, not {dataset.name!r}",
                      file=sys.stderr)
                return 2
            print(f"\nrecords: {args.records} ({len(responses)} responses, "
                  f"hallucination_rate={meta['hallucination_rate']}, "
                  f"generation={meta['generation']})")
        else:
            if args.ingest:
                count = await ingest_corpus(db, dataset, tenant_id, settings)
                print(f"corpus: {count} document(s)")
            if not await doc_chunks(db, tenant_id):
                print(f"no chunks for {tenant_id!r}; run with --ingest", file=sys.stderr)
                return 2
            responses = await _collect(args, dataset, db, tenant_id, settings)
            meta = {"hallucination_rate": args.rate, "generation": settings.generation_backend}

        if args.loss == "gold" and not meta["hallucination_rate"]:
            print(
                "NOTE: gold loss with no injected hallucinations. Extractive claims are\n"
                "      supported by construction, so every loss is zero and the bound\n"
                "      says nothing. Use --rate > 0."
            )
        if args.loss == "verifier":
            print(
                "NOTE: loss from the verifier itself — the bound is conditional on the\n"
                "      verifier being right (report §7.3)."
            )

        observations = observe_all(
            responses, variant=args.variant, mode=mode,
            support_threshold=settings.support_threshold, loss=args.loss,
        )
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
                loss_source=args.loss,
                hallucination_rate=float(meta["hallucination_rate"] or 0.0),
            )
            await store.save_examples(
                conn, calibration_id,
                [
                    (o.query, o.answer, o.statistic, o.loss, LABEL_SOURCE[args.loss],
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
                        "loss": args.loss,
                        "variant": args.variant,
                        "mode": mode.value,
                        "records_meta": meta,
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
    parser.add_argument(
        "--records", default="",
        help="read responses from an aletheia.eval.collect file instead of running the pipeline",
    )
    parser.add_argument(
        "--loss", default="gold", choices=LOSS_SOURCES,
        help="gold: injected hallucinations (verifier-independent); verifier: the verifier's own label",
    )
    parser.add_argument("--variant", default="nli", help="verifier variant that scores claims")
    parser.add_argument("--rate", type=float, default=0.15, help="per-claim corruption rate")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--tenant", default="", help="store the calibration for this tenant")
    parser.add_argument("--verified-only", action="store_true")
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
