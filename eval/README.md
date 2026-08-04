# Evaluation

Empty by design in week 1. This directory is the other half of the project's
claim: without a leak-free eval set and a seeded, cached harness, "≤ 5% unsupported
claims with 95% confidence" is a sentence rather than a result.

## Planned layout

```
eval/
  configs/           run configurations (model, corpus, split, seed)
  datasets/          QA triples — question, answer, evidence spans
  aletheia_eval/     the harness (run.py, metrics.py, baselines.py)
  runs/              outputs, gitignored; results promoted to results/
  results/           frozen tables and figures that the report cites
```

## Corpora (PRD §7.1)

| Corpus | Source | Why |
|---|---|---|
| EN-Public | Wikipedia subset + arXiv abstracts | Comparability with published work |
| EN-Domain | Public SEC 10-K filings | Long, tabular, realistically hard |
| TR-Domain | Resmî Gazete / KVKK / legislation (public) | Turkish, terminology-dense, versioned |
| TR-Adversarial | 150 hand-written trap questions | Answers absent from the corpus; tests abstention |

## Non-negotiables

**The split is leak-free.** The calibration set is used for choosing λ and for
nothing else. A threshold selected on data it is then evaluated on produces a
guarantee that is arithmetically valid and empirically meaningless.

**Baselines are compared at equal answer rate.** "Fewer errors" is trivially
achievable by abstaining more, so a comparison that does not hold coverage fixed
says nothing. Baselines: naive RAG; RAG + LLM-as-judge post-filter with a
hand-picked threshold; self-consistency (n=5); Aletheia.

**Runs are seeded and cached.** Determinism is a requirement (PRD §4.2), and the
LLM cache is what keeps the ablation matrix inside budget.

**Every reported number names its calibration run.** Same rule as the API.

## Metrics

- *Retrieval:* Recall@k, nDCG@10, MRR.
- *Generation:* citation precision/recall, attributable-claim ratio.
- *Risk:* empirical unsupported-claim rate against α, answer rate, risk–coverage
  curve and its area, count of calibration violations.
- *System:* p50/p95 latency broken down by stage, cost per query, cache hit rate.

## Ablations

(a) no reranker, (b) no verifier — self-consistency only, (c) no citation
constraint, (d) calibrated in one language, tested in the other.

Ablation (d) is the research contribution: does a threshold calibrated on English
still hold on Turkish? The hypothesis is no — morphology, tokenisation, and NLI
model quality all shift the support-score distribution, and a shifted statistic
breaks exchangeability. A negative result here is still a publishable one,
provided it is measured rather than asserted.
