# ADR-0008 — Calibrate against injected hallucinations, not the verifier's labels

- **Status:** Accepted
- **Date:** 2026-09-26
- **Relates to:** ADR-0004, ADR-0007, PRD G1/G4, open question 4, report §7.2–7.3

## Context

Two limitations had the same root and blocked every headline number.

**The generator could not be wrong.** Extractive generation copies sentences from
retrieved chunks, so every claim is supported by construction. A bound calibrated
on it bounds nothing about hallucination; the only possible losses came from
verifier strictness.

**The labels came from the component under test.** The calibration loss was
"the verifier marked a retained claim unsupported". The verifier also produces
the statistic. So a claim the verifier wrongly accepts is, by definition, not a
loss — its errors sat *beneath* the bound, and the guarantee was conditional on
the verifier being right (PRD open question 4).

No API key was available for a generative backend, and even with one, labelling
its output would need either human annotation at a scale this project cannot
afford or an LLM judge — a second noisy labeller.

## Decision

**Inject hallucinations with known locations, and read the loss from that ground
truth.** `aletheia.eval.hallucinate` rewrites generated claims at a chosen
per-claim rate in the three ways retrieval-augmented generators actually fail —
a changed number or date, flipped polarity, a swapped domain term — with rules
for Turkish and English. A corrupted claim keeps its citation, so it points at
the evidence that contradicts it, as a real hallucination does.

The loss becomes: *does the response we would return contain a claim we
corrupted?* (`loss=gold`). The verifier still produces the statistic and the
per-claim action, but it no longer produces the label. A corrupted claim the
verifier accepts is now a loss — the verifier's false-accept rate is inside the
bound.

The pipeline runs once per dataset and records every claim's scores under every
verifier variant plus its ground truth (`aletheia.eval.collect`); certification
and every ablation are derived from that file (`aletheia.eval.observations`).
The old definition is kept as `loss=verifier` and reported alongside, because
"certified on verifier labels, scored on the truth" is itself a result.

## Consequences

- **The bound is conditional on the error model, not on the verifier.** It holds
  for number, negation and term errors at the injected rate. It says nothing about
  fabricated content with no counterpart in the evidence, paraphrase drift, or
  omissions, and nothing about any particular LLM's error mix. This replaces an
  unmeasurable condition (verifier accuracy) with a stated, controllable one — a
  better trade, but a trade. Every report quotes the rate and the error types.
- **A rate is a parameter of the guarantee.** A threshold certified at 15% says
  nothing about a generator that hallucinates twice as often. The rate is stored
  on every calibration row (migration 0004) and swept in the experiments.
- **Rules can miss.** A corruption that happens to leave the claim true would be
  mislabelled a loss (conservative direction). The rules are narrow on purpose —
  "bir" is never negated, "May 27" is a date not a modal — and tested.
- **With a generative backend the same machinery applies**, with the gold loss
  coming from human or judge labels instead; `label_source` already distinguishes
  them.
