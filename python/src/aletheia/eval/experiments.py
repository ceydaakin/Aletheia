"""Every result table in the report, from records files, in one command.

    python -m aletheia.eval.experiments --records-dir ../eval/results/records \\
        --out-dir ../eval/results

Reads records written by :mod:`aletheia.eval.collect` and never runs a model,
so every table below is derived from the same responses and re-running it is
cheap. File naming inside ``--records-dir``:

    <dataset>.jsonl                 the full system (the headline records)
    <dataset>__no-rerank.jsonl      ablation (a): reranker off
    <dataset>__rate<r>.jsonl        sensitivity to the injected hallucination rate

Tables produced (PRD goals in brackets):

1. The guarantee across ten alphas, repeated random splits          [G1]
2. Answer rate and useful answer rate at alpha = 0.05               [G2]
3. Baselines and ablations at matched answer rate                   [G4, §7.3–7.4]
4. Cross-lingual transfer on the parallel KVKK sets                 [G5]
5. Verifier discrimination by corruption type and language          [week 6]
6. Retrieval: evidence recall of what generation saw                [week 3]
7. Sensitivity to the hallucination rate

A configuration whose records are absent is reported as "not run", never
silently dropped: a missing row in an ablation table reads as "no effect".
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from aletheia.contracts import Mode
from aletheia.eval import analysis, records
from aletheia.eval.observations import observe_all

ALPHAS = (0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30, 0.35)
PARALLEL = ("kvkk-en", "kvkk-tr")


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.{digits}f}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cache: dict[Path, tuple[dict, list]] = {}

    def load(self, name: str):
        path = Path(self.args.records_dir) / f"{name}.jsonl"
        if not path.exists():
            return None
        if path not in self.cache:
            self.cache[path] = records.read(path)
        return self.cache[path]

    def observations(self, name: str, *, variant=None, mode=Mode.STRICT, loss="gold"):
        loaded = self.load(name)
        if loaded is None:
            return None
        variant = variant or self.args.variant
        if variant not in loaded[1][0].variants and any(r.claims for r in loaded[1]):
            return None
        return observe_all(
            loaded[1], variant=variant, mode=mode,
            support_threshold=self.args.support_threshold, loss=loss,
        )

    def repeated(self, cal, test=None, *, alpha: float) -> dict[str, float]:
        return analysis.repeated(
            cal, test, alpha=alpha, delta=self.args.delta,
            fraction=self.args.fraction, trials=self.args.trials, seed=self.args.seed,
        )


def guarantee_table(run: Runner, dataset: str) -> tuple[dict, str]:
    obs = run.observations(dataset)
    rows, data = [], {}
    for alpha in ALPHAS:
        s = run.repeated(obs, alpha=alpha)
        data[str(alpha)] = s
        held = not math.isnan(s["mean_risk"]) and s["mean_risk"] <= alpha
        rows.append([
            f"{alpha:g}", _fmt(s["certified_rate"], 2), _fmt(s["mean_threshold"], 2),
            f"{_fmt(s['mean_risk'])} ± {_fmt(s['risk_ci95'])}", _fmt(s["exceedance_rate"], 2),
            _fmt(s["mean_answer_rate"]), _fmt(s["mean_useful_rate"]),
            "yes" if held else ("n/a" if s["certified_rate"] == 0 else "**NO**"),
        ])
    table = _table(
        ["α", "certified", "λ̄", "held-out risk (95% CI)", "P(test risk>α)",
         "answer rate", "useful rate", "risk ≤ α"],
        rows,
    )
    return data, table


def ablation_table(run: Runner, dataset: str) -> tuple[dict, str]:
    alpha = run.args.ablation_alpha
    full = run.observations(dataset)
    aletheia = run.repeated(full, alpha=alpha)
    post_filter = analysis.operating_point(full, 1.0)
    # Every configuration with a statistic is also compared at one shared
    # coverage — Aletheia's if it certified, else the post-filter's — because a
    # lower risk means nothing at a lower answer rate (PRD §7.4).
    reference = aletheia["mean_answer_rate"] or post_filter["answer_rate"]
    rows, data = [], {"reference_coverage": reference}

    def add(name: str, summary: dict | None, curve=None, note: str = "") -> None:
        if summary is None:
            rows.append([name, "not run", "—", "—", "—", "—", note])
            data[name] = None
            return
        matched = analysis.risk_at_coverage(curve, reference) if curve else math.nan
        entry = {**summary, "aurc": analysis.aurc(curve) if curve else math.nan,
                 "risk_at_reference": matched}
        data[name] = entry
        rows.append([
            name, _fmt(summary.get("mean_answer_rate")), _fmt(summary.get("mean_risk")),
            _fmt(entry["aurc"]), _fmt(summary.get("mean_useful_rate")), _fmt(matched), note,
        ])

    def point(obs) -> dict:
        p = analysis.operating_point(obs, 1.0)
        return {"mean_answer_rate": p["answer_rate"], "mean_risk": p["risk"],
                "mean_useful_rate": p["useful_rate"], "certified_rate": math.nan}

    naive = run.observations(dataset, mode=Mode.PERMISSIVE)
    add("naive RAG (no verifier)", point(naive), None, "base rate at every answer rate")
    add(f"post-filter, {run.args.variant} ≥ {run.args.support_threshold}", point(full), None,
        "no calibrated gate (stand-in for RAG + judge)")
    add("self-consistency (n=5)", None, note="needs a sampling LLM; no API key in this run")
    add(f"**Aletheia ({run.args.variant}, strict, LTT)**", aletheia, full,
        f"certified in {aletheia['certified_rate']:.0%} of splits")

    for label, variant, note in (
        ("verifier: whole-chunk NLI", "nli", ""),
        ("verifier: windowed NLI", "nli_window", "SummaC-style"),
        ("(b) verifier: token overlap", "overlap", "model-free"),
        ("(c) no citation forcing", "nli_any", "premise = best retrieved chunk"),
    ):
        if variant == run.args.variant:
            continue
        obs = run.observations(dataset, variant=variant)
        add(label, run.repeated(obs, alpha=alpha) if obs else None, obs, note)
    flagged = run.observations(dataset, mode=Mode.FLAGGED)
    add("flagged instead of strict", run.repeated(flagged, alpha=alpha), flagged, "")
    norerank = run.observations(f"{dataset}__no-rerank")
    add("(a) no reranker", run.repeated(norerank, alpha=alpha) if norerank else None, norerank, "")

    # The circular definition: certify on verifier-derived labels, then
    # measure what that threshold delivers against the truth.
    circular = run.observations(dataset, loss="verifier")
    add("loss from the verifier (circular)", run.repeated(circular, full, alpha=alpha), None,
        "certified on verifier labels, scored on gold")

    table = _table(
        ["configuration", "answer rate", "held-out risk", "AURC", "useful rate",
         f"risk @ {_fmt(reference, 2)} coverage", "note"],
        rows,
    )
    return data, table


def crosslingual_table(run: Runner) -> tuple[dict, str]:
    en, tr = (run.observations(name) for name in PARALLEL)
    if en is None or tr is None:
        return {}, "_not run: needs records for both kvkk-en and kvkk-tr_"
    rows, data = [], {}
    for alpha in (0.05, 0.10, 0.15, 0.20):
        for label, cal, test in (
            ("EN → EN", en, en), ("TR → TR", tr, tr), ("EN → TR", en, tr), ("TR → EN", tr, en)
        ):
            s = run.repeated(cal, test, alpha=alpha)
            data[f"{label}@{alpha}"] = s
            rows.append([f"{alpha:g}", label, _fmt(s["mean_threshold"], 2), _fmt(s["mean_risk"]),
                         _fmt(s["exceedance_rate"], 2), _fmt(s["mean_answer_rate"])])
    table = _table(["α", "calibrate → test", "λ̄", "held-out risk", "P(test risk>α)", "answer rate"], rows)

    # The proposed correction: certify in EN, then *verify* the transferred
    # threshold on a small TR sample with a single-hypothesis test.
    notes = []
    for alpha in (0.05, 0.10):
        selection = analysis.certify(en, alpha=alpha, delta=run.args.delta)
        if not selection.certified:
            notes.append(f"α={alpha:g}: EN did not certify on the full set")
            continue
        test = analysis.transfer_test(tr, selection.lambda_value, alpha=alpha)
        data[f"transfer_test@{alpha}"] = {**test, "threshold": selection.lambda_value}
        notes.append(
            f"α={alpha:g}: EN λ={selection.lambda_value:.2f} applied to all TR — answered "
            f"{int(test['answered'])}, failures {int(test['failures'])}, p={test['pvalue']:.3g} "
            f"({'transfer verified' if test['pvalue'] <= run.args.delta else 'transfer NOT verified'}); "
            f"single-hypothesis floor {analysis.transfer_floor(alpha, run.args.delta)} vs grid floor "
            f"{analysis.grid_floor(alpha, run.args.delta)}"
        )
    return data, table + "\n\n" + "\n".join(f"- {n}" for n in notes)


def verifier_table(run: Runner, datasets: list[str]) -> tuple[dict, str]:
    rows, data = [], {}
    for dataset in datasets:
        loaded = run.load(dataset)
        if loaded is None:
            continue
        claims = [c for r in loaded[1] for c in r.claims]
        for variant in ("nli", "nli_window", "nli_any", "overlap"):
            clean = [c.scores[variant] for c in claims if not c.corrupted and variant in c.scores]
            if not clean:
                continue
            for kind in ("number", "negation", "term", "all"):
                bad = [c.scores[variant] for c in claims if c.corrupted and variant in c.scores
                       and (kind == "all" or c.kind == kind)]
                if not bad:
                    continue
                t = run.args.support_threshold
                entry = {
                    "auroc": analysis.auroc(clean, bad),
                    "n_corrupted": len(bad),
                    "false_accept_rate": sum(s >= t for s in bad) / len(bad),
                    "false_reject_rate": sum(s < t for s in clean) / len(clean),
                }
                data[f"{dataset}/{variant}/{kind}"] = entry
                rows.append([dataset, variant, kind, str(len(bad)), _fmt(entry["auroc"]),
                             _fmt(entry["false_accept_rate"]), _fmt(entry["false_reject_rate"])])
    table = _table(["dataset", "verifier", "corruption", "n", "AUROC", "false accept @0.5",
                    "false reject @0.5"], rows)
    return data, table


def retrieval_table(run: Runner, datasets: list[str]) -> tuple[dict, str]:
    rows, data = [], {}
    for dataset in datasets:
        for suffix in ("", "__no-rerank"):
            loaded = run.load(dataset + suffix)
            if loaded is None:
                continue
            answerable = [r for r in loaded[1] if r.answerable]
            with_ev = [r for r in answerable if r.evidence_retrieved is not None]
            entry = {
                "doc_recall": sum(any(d in r.retrieved_docs for d in r.relevant_docs) for r in answerable)
                / max(1, len(answerable)),
                "evidence_recall": sum(bool(r.evidence_retrieved) for r in with_ev) / max(1, len(with_ev)),
                "n": len(answerable),
            }
            data[dataset + suffix] = entry
            rows.append([dataset + suffix, str(entry["n"]), _fmt(entry["doc_recall"]),
                         _fmt(entry["evidence_recall"])])
    return data, _table(["records", "answerable n", "relevant doc in top-n", "evidence in top-n"], rows)


def sensitivity_table(run: Runner, datasets: list[str]) -> tuple[dict, str]:
    rows, data = [], {}
    for dataset in datasets:
        for path in [*sorted(Path(run.args.records_dir).glob(f"{dataset}__rate*.jsonl")), None]:
            name = path.stem if path else dataset
            obs = run.observations(name)
            if obs is None:
                continue
            rate = run.load(name)[0]["hallucination_rate"]
            s = run.repeated(obs, alpha=run.args.ablation_alpha)
            base = analysis.operating_point(run.observations(name, mode=Mode.PERMISSIVE), 1.0)["risk"]
            data[name] = {**s, "base_rate": base}
            rows.append([dataset, f"{rate:g}", _fmt(base), _fmt(s["certified_rate"], 2),
                         _fmt(s["mean_risk"]), _fmt(s["mean_answer_rate"])])
    return data, _table(["dataset", "claim corruption rate", "naive response risk", "certified",
                         f"held-out risk @α={run.args.ablation_alpha}", "answer rate"], rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-experiments", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--datasets", default="kvkk-tr,kvkk-en,en-public")
    parser.add_argument("--delta", type=float, default=0.05)
    parser.add_argument("--fraction", type=float, default=0.6)
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--support-threshold", type=float, default=0.5)
    parser.add_argument("--variant", default="nli",
                        help="verifier variant of the headline system")
    parser.add_argument("--ablation-alpha", type=float, default=0.15)
    args = parser.parse_args(argv)

    run = Runner(args)
    datasets = [d for d in args.datasets.split(",") if run.load(d) is not None]
    missing = [d for d in args.datasets.split(",") if d not in datasets]

    results: dict = {"settings": vars(args), "missing": missing}
    sections = [
        "# Aletheia — experiment results",
        "",
        f"Generated by `python -m aletheia.eval.experiments` from `{args.records_dir}`. "
        f"Headline verifier `{args.variant}`. δ={args.delta}, calibration fraction {args.fraction}, {args.trials} random splits per cell, "
        f"support threshold {args.support_threshold}. Loss = injected hallucinations (gold) unless stated. "
        + (f"Not run (no records): {', '.join(missing)}." if missing else ""),
    ]
    for dataset in datasets:
        meta = run.load(dataset)[0]
        results.setdefault("meta", {})[dataset] = meta
        data, table = guarantee_table(run, dataset)
        results.setdefault("guarantee", {})[dataset] = data
        sections += [f"\n## 1. Guarantee across α — {dataset}",
                     f"\n{meta['queries']} queries ({meta['status_counts']}), claim corruption rate "
                     f"{meta['hallucination_rate']}, generation {meta['generation']}, reranker {meta['reranker']}, "
                     f"embedding {meta['embedding']}.\n", table]
        data, table = ablation_table(run, dataset)
        results.setdefault("ablations", {})[dataset] = data
        sections += [f"\n## 3. Baselines and ablations at α={args.ablation_alpha} — {dataset}\n", table]

    data, table = crosslingual_table(run)
    results["crosslingual"] = data
    sections += ["\n## 4. Cross-lingual transfer (parallel KVKK)\n", table]
    data, table = verifier_table(run, datasets)
    results["verifier"] = data
    sections += ["\n## 5. Verifier discrimination (clean vs corrupted claims)\n", table]
    data, table = retrieval_table(run, datasets)
    results["retrieval"] = data
    sections += ["\n## 6. Retrieval — what generation saw (top-n after rerank)\n", table]
    data, table = sensitivity_table(run, datasets)
    results["sensitivity"] = data
    sections += ["\n## 7. Sensitivity to the hallucination rate\n", table]

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "experiments.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / "experiments.md").write_text("\n".join(sections) + "\n", encoding="utf-8")
    print("\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
