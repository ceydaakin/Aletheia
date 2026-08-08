# Aletheia: a risk-controlled RAG gateway

**Ceyda Akın** · Technical report, draft · August 2026

---

## Abstract

Retrieval-augmented generation systems answer fluently and leave verification to
the reader. Aletheia is a serving layer that instead treats correctness as a
parameter: each answer is decomposed into atomic claims, each claim is checked
against the passages it cites with a natural-language-inference model, and the
whole response is gated by a threshold selected under distribution-free risk
control. When the threshold cannot be met the system abstains and says why.

This report describes the design, the engineering, and what has and has not been
established empirically. The system runs end to end; the calibration machinery is
verified against synthetic data where ground truth is known; and on the real
evaluation set it **correctly certifies nothing**, because 27 labelled queries are
far below the sample-size floor the procedure requires. That negative result is
reported here as the primary empirical finding, alongside three defects that only
measurement exposed — including one where a bound was arithmetically valid and
empirically false in 56% of runs.

---

## 1. Problem

RAG deployments sit at two extremes. Demonstrations retrieve, stuff a prompt, and
generate; the answer is fluent, sources are listed underneath, and which sentence
rests on which source is anyone's guess. Regulated deployments, where a single
fabricated sentence carries real cost, respond by putting a human in front of
every output — which removes the reason to automate.

The gap is not better prompting. It is that no component answers the question
*"how often is this wrong?"* with a number that means something. Existing
evaluation and guardrail tooling (RAGAS, TruLens, Guardrails) measures; none
decides, at request time, whether returning a given answer stays inside a stated
error budget.

Conformal prediction and distribution-free risk control supply exactly that
machinery: choose a threshold on a calibration set, and the error rate on
exchangeable future data is bounded with high probability. Aletheia's contribution
is to put that machinery inside a serving path and report honestly on what it
costs.

---

## 2. What the guarantee says

Two choices define the claim, and both were made deliberately because both have a
flattering alternative.

### 2.1 The unit is the response, not the claim

A per-claim bound is easier to satisfy and reads almost identically. It is also
systematically misread. Under a per-claim 5% bound, a ten-claim answer has roughly
a 40% chance of containing an unsupported claim — assuming an independence that
does not hold. A reader who sees "≤5% error" and receives that answer has been
misled by the choice of unit rather than by the statistics.

Aletheia controls

> P(the returned response contains ≥ 1 unsupported claim) ≤ α, with confidence 1−δ

The cost is real: long answers are penalised most, and answer rate is under
genuine pressure. That is the correct direction to fail in. (ADR-0004)

### 2.2 The risk is selective, not marginal

The second choice is the denominator. *Marginal* risk is P(wrong **and**
answered) — every abstention counts as a success, so the number falls simply by
answering less. *Selective* risk is P(wrong **given** answered).

Aletheia controls the selective risk. Someone holding an answer wants to know the
chance that answer is wrong; the system having declined ten other questions does
not make theirs safer. Selective is also the harder target, so it cannot be gamed
by abstaining more — under the marginal definition a system that answers nothing
has perfect risk.

This was not an abstract preference. The first implementation certified the
marginal risk while the evaluation reported the selective one. Both halves were
individually correct, nothing in the code looked wrong, and held-out risk exceeded
α in **56% of certified runs**. See §6.1. (ADR-0007)

### 2.3 Procedure

For each candidate threshold λ on a grid Λ, the loss is binary per response, so
given `m_λ` answered calibration responses the failure count is Binomial(m_λ,
R(λ)) and the p-value for `H_λ: R(λ) > α` is the exact binomial tail
`P(Bin(m_λ, α) ≤ k_λ)`. No concentration inequality is needed and no slack is
given away. Bonferroni at δ/|Λ| corrects for testing the grid; it is valid under
arbitrary dependence, and thresholds across a grid are strongly dependent. Among
certified λ, the one answering the most calibration queries is selected: the
guarantee is the constraint, coverage is the objective.

The confidence statistic is `1 − min support_score` over retained claims — the
weakest link, which is what a per-response bound concerns — and it is computed
**after** the action policy edits the answer. A bound computed before removing a
bad claim and quoted for the answer after removal would be circular.

---

## 3. System

```
Client ──POST /v1/answer──▶ Gateway (Go)
                              │  auth, tenancy, admission control,
                              │  deadline propagation, failure policy
                              ▼
              ┌──────────┬───────────┬──────────┬──────────┐
              │Retrieval │Generation │ Verifier │   Risk   │  (Python/FastAPI)
              │ BM25 +   │ citation- │   NLI    │Controller│
              │ pgvector │constrained│entailment│ conformal│
              │ + rerank │  decoding │          │ threshold│
              └────┬─────┴───────────┴──────────┴─────┬────┘
                   │                                  │
            Postgres + pgvector                Calibration job
          (bitemporal chunk store)                 (CronJob)
```

**Split runtime.** Latency-critical orchestration with deadlines and partial
failure in Go; model execution in Python (ADR-0001). The cost is duplicated
contract types, kept honest by a golden-fixture test both languages parse.

**Every failure lands on abstention.** Retrieval failure → `out_of_corpus`.
Verifier failure → `insufficient_evidence`. Risk controller failure →
`stale_calibration`. Only generation returns 5xx, because there is nothing to
degrade to. A system whose premise is "we do not emit unverified answers" cannot
respond to a verifier timeout by emitting one.

**Bitemporal corpus.** Chunks carry valid time (when the fact held) and
transaction time (when we knew it), and nothing is deleted. Amendments close a
validity interval; corrections retract knowledge while keeping the record of the
mistake. `as_of` selects the historical slice, so the same question returns
*thirty days* for 2025 and *sixty days* for 2027 (ADR-0002, ADR-0005).

**Hybrid retrieval.** Lexical (Postgres FTS with per-language stemming and
`unaccent`) and dense (pgvector HNSW) arms fuse with Reciprocal Rank Fusion. RRF
rather than weighted score fusion because BM25 scores are unbounded and
corpus-dependent, and any normalisation introduces a parameter that must be
retuned as the corpus shifts — which is the one thing this project already cannot
rely on staying still (ADR-0006).

**No fallback threshold.** With no certified, fresh calibration for the requested
α, the risk controller abstains. A guessed threshold would be a guarantee-shaped
string with nothing behind it.

---

## 4. Experimental setup

**Corpus and queries.** `bootstrap-tr`: 8 hand-written Turkish policy documents
(service agreement, data retention, remote work, procurement authority, annual
leave, information security, travel expenses, supplier audit) with deliberate
distractors — several documents mention notice periods, payment terms, and
six-monthly reviews, but only one states each specific figure. 27 queries: 24
answerable, 3 deliberately unanswerable. Gold labels name **documents**, not chunk
ids, so they survive a change to chunking parameters.

**Models.** Embeddings `intfloat/multilingual-e5-base`; entailment
`MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`; generation is
extractive by default (see §7.2). CPU only.

**Splits.** Calibration and test are disjoint and interleaved deterministically —
no seed to record, no shuffle to reproduce. The calibration half chooses λ and is
never used to report what λ delivered.

---

## 5. Results

### 5.1 Retrieval

Lexical arm only (no embeddings), 24 answerable queries, 12 chunks:

| configuration | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|
| lexical-only | 0.576 | 0.806 | 0.840 | 0.924 | 0.831 | 0.910 |

**Read recall@1 and recall@3.** With 12 chunks in the corpus, a top-10 cut returns
most of it, so recall@10 flatters any system that returns anything. The harness
prints this caveat itself rather than trusting a reader to supply it.

Turkish morphology is handled: the query `sözleşme` matches `sözleşmeyi` and
`sözleşmelerde`, and the unaccented `sozlesme` matches all three. Snowball's
Turkish stemmer is a suffix stripper rather than a morphological analyser and
will under-stem long derivational chains; Zemberek is the upgrade path, deferred
until a measurement justifies running a JVM.

Retrieval returns results for all three unanswerable queries. This is expected,
not a defect: disjunctive matching retrieves anything sharing a term, so retrieval
alone cannot identify out-of-corpus questions. It is the first measured evidence
for why the verifier and risk controller exist.

### 5.2 Entailment discrimination

The single measurement the whole thesis rests on. Premise: *"Taraflardan her biri,
otuz (30) gün önceden yazılı ihbarda bulunmak suretiyle sözleşmeyi feshedebilir."*

| hypothesis | NLI | lexical overlap |
|---|---|---|
| "Fesih ihbar süresi **otuz** gündür." (entailed) | **0.985** | 0.75 |
| "Fesih ihbar süresi **altmış** gündür." (contradicted) | **0.139** | 0.50 |
| "Sözleşme hiçbir şekilde feshedilemez." (negation) | 0.000 | — |
| English: "notice period is thirty days" | 0.992 | — |
| English: "notice period is sixty days" | 0.005 | — |

One changed word — the number carrying the entire meaning — moves the NLI score
across the threshold. Lexical overlap scores the contradiction at 0.50, **at the
default support threshold**, so it ships the false claim as supported. That is why
overlap is reported as an ablation baseline and never as a verifier.

A false negative worth recording: *"Notice must be given in writing"* against a
premise stating *"giving thirty (30) days written notice"* scored **0.029** — a
genuinely entailed claim judged unsupported. The verifier is conservative, which
costs answer rate rather than the guarantee. That is the right direction, and it
belongs in any account of the answer-rate numbers.

### 5.3 Calibration

Run over `bootstrap-tr` with the NLI verifier, α = 0.05, δ = 0.05, 50-point grid:

```
calibration n=14   test n=13
certified: False
```

**Nothing was certified, and this is the machinery working.** With zero observed
failures the p-value is (1−α)^m, so Bonferroni over a 50-point grid requires

    m ≥ ln(δ / |Λ|) / ln(1 − α) = 135

answered calibration responses at α=0.05 — regardless of system quality. The best
threshold here answered 14. The calibration job computes this floor and reports it,
because "the verifier is bad" and "the sample cannot say anything" look identical
from outside and have opposite fixes.

### 5.4 Does the bound hold?

Since the real dataset cannot certify, the procedure is validated where ground
truth is known by construction: responses whose statistic is informative about
their loss, overall failure rate 0.25, α = 0.10, 60 independent runs of 4,000
responses each, split 50/50.

Across all certified runs, held-out selective risk stayed at or below α in the
overwhelming majority — well inside what sampling noise on the held-out half
explains. Under the marginal formulation (§2.2) the same test fails 56% of the
time. The test is retained in the suite precisely because it distinguishes those
two cases, and would also catch a leaked split, an off-by-one in the grid, or a
missing multiplicity correction.

### 5.5 Latency

Full stack, 20 concurrent, 400 requests, overlap verifier:

| | p50 | p95 | throughput |
|---|---|---|---|
| end to end | 84 ms | 182 ms | 207 req/s |

Against the targets (p50 ≤ 1.8 s, p95 ≤ 3.5 s, ≥ 20 concurrent): comfortable — but
only with the baseline verifier.

With the NLI verifier, per response:

| claims | median |
|---|---|
| 1 | 4.1 s |
| 4 | 13.3 s |
| 8 | 27.5 s |

**A four-claim answer costs ~13 s in the verifier alone, roughly 4× the entire p95
budget.** Truncating premises bought 9%. int8 dynamic quantization does not work
on this stack at all: DeBERTa-v2 on torch 2.13 with ONEDNN raises at *inference*
time, so the scorer validates quantization with a probe forward pass at startup
and falls back to full precision. The fallback direction is deliberate — the cost
of that failure must be latency, never a wrong support score.

This is the project's largest unresolved engineering problem. The obvious cheap
cascade does not work: gating on lexical overlap would let exactly the wrong
claims skip verification, since overlap scores contradictions above threshold.

---

## 6. What measurement caught that review did not

Three defects, recorded because the pattern is more informative than the fixes.

### 6.1 A bound that was valid and false

Certifying marginal risk while evaluating selective risk. Both halves correct in
isolation; held-out risk exceeded α in 56% of certified runs; no code looked
wrong. Only a synthetic test with known ground truth exposed it. **A statistical
guarantee needs a test that checks the guarantee, not the arithmetic.**

### 6.2 A recall floor of 0.02

Postgres's built-in query parsers join terms with AND, so a chunk had to contain
every word of the question — and questions are longer than the passages that
answer them. Disjunctive matching plus `ts_rank_cd`, which is what BM25 actually
does, took recall@1 from 0.02 to 0.58. Visible immediately in an evaluation
harness; invisible in code review.

### 6.3 An abstention that was really a timeout

Retrieval held one connection for a language lookup while fanning out to one
connection per search arm. At concurrency 20, requests starved each other on a
10-connection pool and every retrieval hit its 1.2 s deadline — surfacing as
`abstain(out_of_corpus)`, a plausible-looking product outcome rather than an
error. Fixing it took p50 from 1274 ms to 84 ms and throughput from 15.7 to
207 req/s.

**A system designed to fail gracefully can fail invisibly.** Every abstention path
that exists to protect users also hides infrastructure problems, which is why
`degraded` is a first-class field in the response and on the trace span.

A fourth, in the instrument rather than the system: the first load tester reported
*PASS at 5 ms p95* while the gateway rejected 97% of requests with 429. It was
timing the rejection path. It now excludes non-2xx from the sample and exits
non-zero below 90% served.

---

## 7. Limitations

Stated plainly, because a project about honest uncertainty cannot be vague here.

**7.1 The evaluation set is far too small.** 27 queries against a requirement of
~400 hand-verified triples per corpus. This is the binding constraint on every
number in §5 and the reason §5.3 certifies nothing.

**7.2 The default generator cannot hallucinate.** Extractive generation copies
sentences from retrieved chunks. A bound calibrated against it measures retrieval
quality and verifier strictness, **not** unsupported generation, and does not
transfer to a generative system. This is stated in the module, the settings, the
calibration job's output, and the README, because it is the single easiest thing
to forget when quoting a number.

**7.3 The guarantee is conditional on the verifier.** Calibration labels come from
the verifier itself, so its error rate is not folded into the bound. Risk control
with noisy labels is an open research problem, not a solved one.

**7.4 Exchangeability is assumed and will be violated.** Corpus updates and
query-distribution shift break it. `corpus_events` records every change and the
controller refuses calibrations past a maximum age, but age is a crude proxy;
proper drift detection is not implemented.

**7.5 Cross-lingual transfer is unmeasured.** The headline research question —
does an English-calibrated threshold hold in Turkish? — needs both languages to
certify first, which needs §7.1 resolved.

**7.6 Latency with the real verifier misses the SLO** by roughly 4× (§5.5).

**7.7 Single-node, single-region, no erasure path.** The append-only store cannot
satisfy a KVKK/GDPR deletion request; "delete" closes a validity interval and
retains the rows.

---

## 8. Conclusion

The engineering claim holds: risk control can be embedded in a RAG serving path
with per-stage tracing, per-tenant admission control, and a failure policy where
no path produces an unverified answer. The statistical machinery is implemented
exactly, validated where ground truth is known, and refuses to issue a bound it
has not earned.

The scientific claim does not hold yet, and the gap is data rather than design.
Until the evaluation sets reach the sample-size floor derived in §5.3, and until
generation is genuinely capable of hallucinating, no number here should be read as
a statement about a deployed system's error rate.

The most transferable result may be §6: a guarantee, a retrieval stack, and an
abstention policy each failed in a way that looked correct from the inside. In a
system built to be honest about uncertainty, the measurement apparatus deserves
the same suspicion as the thing it measures.

---

## References

- Angelopoulos & Bates. *A Gentle Introduction to Conformal Prediction and
  Distribution-Free Uncertainty Quantification.*
- Angelopoulos et al. *Learn then Test* / *Risk-Controlling Prediction Sets.*
- Quach et al. *Conformal Language Modeling.*
- Gao et al. *RARR: Attributed text generation via post-hoc research and revision.*
- Bohnet et al. *Attributed Question Answering.*
- Es et al. *RAGAS.*
- Cormack et al. *Reciprocal Rank Fusion.*

## Reproducing

```bash
docker compose up --build
cd python
python -m aletheia.eval.retrieval --dataset ../eval/datasets/bootstrap-tr --ingest
VERIFIER_BACKEND=nli python -m aletheia.eval.calibrate \
  --dataset ../eval/datasets/bootstrap-tr --alpha 0.05
cd ../gateway && go run ./cmd/loadtest -c 20 -n 400
```
