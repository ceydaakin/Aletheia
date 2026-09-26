# Aletheia: a risk-controlled RAG gateway

**Ceyda Akın** · Technical report, draft v2 · September 2026

---

## Abstract

Retrieval-augmented generation systems answer fluently and leave verification to
the reader. Aletheia is a serving layer that instead treats correctness as a
parameter: each answer is decomposed into atomic claims, each claim is checked
against the passages it cites with a natural-language-inference model, and the
whole response is gated by a threshold selected under distribution-free risk
control (Learn-then-Test). When the threshold cannot be met the system abstains
and says why, and when live traffic stops being exchangeable with the
calibration data — tested on-line with a conformal test martingale — it stops
quoting the bound.

We evaluate on a parallel Turkish–English legal corpus (the Turkish data
protection law and three regulations, article-aligned with the regulator's
English translation, 550 questions per language) and an English public-domain
set (400 questions). Because the generator is extractive, hallucinations are
injected at known locations and the loss is read from that ground truth, not
from the verifier — so the verifier's own errors fall inside the bound. Three
results. (1) Wherever a threshold was certified — three datasets, ten risk
levels, 200 random splits each — its held-out risk stayed at or below α. (2) The
system cuts the rate of responses containing an unsupported claim from 38% to
6.5% in Turkish and from 46% to 0.2% in English at a 92% and 88% answer rate.
(3) **A threshold certified in English does not transfer to Turkish**: at
α = 0.05 the transferred threshold's held-out Turkish risk is 0.065, above α in
84% of splits, because the entailment model accepts 31% of Turkish domain-term
swaps against 1.3% of English ones. A single-hypothesis transfer test detects
this with 59 labelled target-language answers instead of the 135 a full
recalibration needs. Every question is still a machine-drafted draft awaiting
human verification, and the report says what that does and does not change.

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

**Drift withdraws the guarantee.** The bound holds only while live traffic is
exchangeable with the calibration set. Two signals end it: any document change
after the calibration's corpus snapshot, and a conformal test martingale over
live risk statistics (Vovk's Simple Jumper) crossing its alarm level. By Ville's
inequality the chance of *ever* alarming on exchangeable traffic is at most
1/level — over the calibration's whole lifetime, however often it is checked,
which a repeated two-sample test cannot offer. Either signal turns every
response into `abstain(drift_detected)` with `degraded: true` until the
calibration job runs again (ADR-0009).

---

## 4. Experimental setup

**Corpora.** Three, all public and fetched by committed scripts:

- **kvkk-tr / kvkk-en** — Law No. 6698 on the Protection of Personal Data,
  consolidated with the 2024 amendments, plus three KVKK regulations (erasure
  and anonymisation, the data controllers' registry, the 2024 transfer-abroad
  regulation): 90 provisions, one file per article, in Turkish and in the
  Authority's English translation. The two corpora are **article-aligned**: the
  same filename is the same provision.
- **en-public** — 30 English Wikipedia articles in three clusters with
  deliberate near-duplicate facts (privacy law, space missions, bridges) and 120
  arXiv abstracts on the topics this report cites.

**Queries.** 550 per KVKK language — 400 answerable (42 needing two provisions)
and 150 adversarial questions whose answer is absent (near-miss figures, false
premises, adjacent law) — and 400 for en-public (340 answerable, 60
unanswerable). The KVKK sets are **parallel**: the same id is the same question
in both languages, so a cross-lingual comparison changes nothing but language.
Every answerable query carries a verbatim evidence quote, checked mechanically
against the corpus at load time.

All queries were drafted by a language model from the corpus and are **drafts**:
the PRD requires hand verification, and it has not been done yet. The mechanical
checks catch fabricated evidence and dangling labels; they cannot catch an
unnatural question or a wrong "unanswerable". Every number below is on drafts,
and the harness can restrict any run to verified queries once review is done.

**Losses: injected hallucinations with known locations (ADR-0008).** The
generator is extractive and cannot hallucinate. Each generated claim is instead
corrupted with probability 0.15 in one of the three ways retrieval-augmented
generators fail — a changed number, date or duration; flipped polarity; a
swapped domain term ("data controller" → "data processor", "Kurul" → "Başkan")
— by rules written for Turkish and English morphology. The corrupted claim keeps
its citation, so it points at the passage that contradicts it. A response's
loss is whether any claim it would *return* was corrupted.

The verifier produces the statistic and the per-claim action; **it no longer
produces the label**. A corrupted claim the verifier accepts is a loss, so the
verifier's false-accept rate is inside the bound rather than beneath it — the
condition the previous draft could only state (§7.3) is now measured. The
price is that the bound is conditional on the error model instead: these error
types, at this rate.

The first full run found the error model itself wrong in a way worth recording:
renumbering a paragraph label — "(2)" to "(1)" — was being counted as a
hallucination although the provision stays true, as was changing a law number
inside an amendment annotation, and a Turkish aorist ("yürütür") was being
"negated" as if it were a copula, producing ungrammatical text any verifier
rejects. The first made the verifier look worse than it is; the last made it
look better. Both were found by reading corrupted claims, not by any metric.

**Models.** Embeddings `intfloat/multilingual-e5-base`; reranker
`BAAI/bge-reranker-v2-m3`; entailment
`MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` at 512 tokens.
Retrieval is hybrid (BM25 + dense, RRF, reranked to the top 6); generation takes
up to four sentences. Apple-silicon GPU (MPS) for evaluation.

**Protocol.** The pipeline runs once per dataset and every claim's scores under
every verifier variant are recorded with their ground truth; every table is
derived from those records. Each cell is 200 random splits by query id — 60% to
choose and certify the threshold, 40% held out — with δ = 0.05, Bonferroni over
a 50-point λ grid. "Held-out risk" is the mean selective risk on the held-out
part over the splits that certified; answer rate is averaged over *all* splits,
since an uncertified split answers nothing.

**Choosing the verifier without looking at the test sets.** Two ways of scoring
a claim against its cited chunk were compared: the whole chunk as premise, and
overlapping two-sentence windows with the maximum taken (as SummaC does). The
choice was made on en-public, treated as a development set, before any KVKK
result was computed; the KVKK tables report the other one as an ablation.

---

## 5. Results

Full tables, including every cell omitted here: `eval/results/experiments.md`,
regenerated from the committed records by `make eval`.

### 5.1 Retrieval

What generation actually saw — the top six chunks after hybrid retrieval and
reranking:

| dataset | answerable queries | relevant document in top 6 | gold evidence passage in top 6 |
|---|---|---|---|
| kvkk-tr | 400 | 0.970 | 0.960 |
| kvkk-en | 400 | 0.978 | 0.968 |
| en-public | 340 | 1.000 | 0.997 |

Retrieval is not the bottleneck on these corpora: the passage that answers the
question is in front of the generator 96–100% of the time. The reranker
ablation was not run (see §7).

### 5.2 Entailment discrimination

**Per claim, on the evaluation sets.** Clean claims are copied from their cited
chunk; corrupted claims are the same claims with one number, polarity or domain
term changed. At the support threshold of 0.5:

| dataset | corruption | n | AUROC | false accept | false reject |
|---|---|---|---|---|---|
| kvkk-en | number | 22 | 0.986 | 0.000 | 0.440 |
| kvkk-en | negation | 215 | 0.980 | 0.000 | 0.440 |
| kvkk-en | term | 80 | 0.955 | **0.013** | 0.440 |
| kvkk-tr | number | 24 | 0.970 | 0.000 | 0.357 |
| kvkk-tr | negation | 122 | 0.961 | 0.008 | 0.357 |
| kvkk-tr | term | 107 | 0.733 | **0.308** | 0.357 |
| en-public | all | 189 | 0.970 | 0.000 | 0.609 |

The model is conservative everywhere — it rejects 36–61% of claims that are
verbatim copies of their evidence — and in English it almost never accepts a
corrupted claim. The exception is the one that decides §5.4: **it accepts 31%
of Turkish domain-term swaps** ("Kurul" → "Başkan", "veri sorumlusu" → "veri
işleyen") against 1.3% of the same swaps in English. Numbers and negation it
handles in both languages.

**Ablations of the verifier** (AUROC, all corruption types): whole-chunk NLI
0.865 (kvkk-tr) / 0.970 (en-public); token overlap 0.923 / 0.887 but it accepts
**every** corrupted claim at the support threshold; NLI without citation forcing
— the claim scored against the best of all retrieved chunks — 0.769 / 0.670,
with 41% / 34% of corrupted claims accepted. Citation forcing is worth more than
the choice of scorer.

**Windowed premises trade the wrong way.** Scoring against overlapping
two-sentence windows of the cited chunk (SummaC-style) cuts false rejects on
en-public from 61% to 9% — and raises false accepts from 0% to 23%, 35% for
negation. Chosen against on the development set before any KVKK number was
computed (§4).

The illustrative pair from the first draft still holds and shows the mechanism
in one line:

| hypothesis vs. *"…otuz (30) gün önceden yazılı ihbarda bulunmak suretiyle…"* | NLI | lexical overlap |
|---|---|---|
| "Fesih ihbar süresi **otuz** gündür." (entailed) | **0.985** | 0.75 |
| "Fesih ihbar süresi **altmış** gündür." (contradicted) | **0.139** | 0.50 |

### 5.3 The guarantee across α  [G1, G2]

Held-out selective risk (mean over certified splits, 95% CI), how often a single
held-out part exceeded α, and answer rate (mean over all splits):

| α | kvkk-en: risk / P(>α) / answer | kvkk-tr: certified / risk / P(>α) / answer | en-public: risk / answer |
|---|---|---|---|
| 0.05 | 0.002 ± 0.000 / 0.00 / **0.884** | 0% / — / — / 0.000 | 0.000 / **0.768** |
| 0.10 | 0.002 / 0.00 / 0.884 | 17% / 0.086 ± 0.002 / 0.06 / 0.146 | 0.000 / 0.768 |
| 0.125 | 0.002 / 0.00 / 0.884 | 82% / 0.070 ± 0.002 / 0.00 / 0.749 | 0.000 / 0.768 |
| 0.15–0.35 | 0.002 / 0.00 / 0.884 | 100% / 0.065 ± 0.002 / 0.00 / 0.915 | 0.000 / 0.768 |

**G1 holds.** Across three datasets and ten α ∈ {0.05, …, 0.35}, every cell that
certified had mean held-out risk ≤ α. The single-split exceedance of 6% at
α = 0.10 in Turkish is sampling noise on a 220-query test part, within δ.

**G2 is met in English and missed in Turkish.** At α = 0.05 the English sets
answer 88% and 77% of queries. Turkish certifies nothing below α = 0.10 and only
becomes usable at 0.125: its floor of about 6.5% unsupported responses, set by
accepted term swaps, is above the budget. That is the procedure refusing to
quote a bound the data cannot support — the same behaviour the first draft
reported on 27 queries, now on 550 and for a measured reason.

**The threshold does little; the certificate does a lot.** The certified λ is
almost always 1.0: in strict mode the per-claim filter already removes what the
verifier rejects, and the response-level statistic (1 − weakest retained
support) separates the remaining failures only weakly (AURC 0.061 against a
flat 0.065 in Turkish). The calibrated gate's contribution is the *certificate* —
knowing which α the system can honour — rather than extra filtering. A
statistic that ranks residual failures better is the most direct lever on G2.

**Answer rate is not usefulness.** Of all queries, the share answered with a
claim that restates the gold evidence is 26% (kvkk-tr), 17% (kvkk-en) and 15%
(en-public), against 34–47% for unfiltered generation. The verifier's
conservatism removes correct claims along with corrupted ones. And the system
answers 81–91% of the adversarial questions whose answer is not in the corpus —
with supported, irrelevant sentences. The guarantee bounds unsupported claims;
it says nothing about relevance, and out-of-corpus detection is absent (§7).

### 5.4 Cross-lingual transfer  [G5]

The parallel sets differ only in language. Splits are by query id, so a
question calibrated in one language is never tested in the other:

| α | calibrate → test | held-out risk | P(test risk > α) | answer rate |
|---|---|---|---|---|
| 0.05 | EN → EN | 0.002 | 0.00 | 0.884 |
| 0.05 | **EN → TR** | **0.065** | **0.84** | 0.915 |
| 0.05 | TR → TR | not certified | — | 0.000 |
| 0.10 | EN → TR | 0.065 | 0.01 | 0.915 |
| 0.10 | TR → EN | 0.002 | 0.00 | 0.138 |
| 0.15 | EN → TR | 0.065 | 0.00 | 0.915 |

**The hypothesis holds: an English-calibrated threshold does not carry its
guarantee into Turkish.** At α = 0.05 it certifies in English, is applied
unchanged to Turkish, and delivers 6.5% — above budget in 84% of splits. The
mechanism is §5.2: the same verifier, on the same provisions, is far more
willing to accept a swapped legal term in Turkish. In the other direction the
Turkish threshold is simply conservative in English.

**Proposed correction: certify where the data is, verify where it is going.**
Recalibrating from scratch in the target language needs 135 answered,
labelled responses at α = 0.05 (Bonferroni over the grid). Testing whether one
*given* threshold holds needs no multiplicity correction, so its floor is
ln δ / ln(1 − α) = 59. Applied to all Turkish answers under the English
threshold (503 answered, 32 failures): at α = 0.05 the transfer test refuses
(p = 0.93) — correctly; at α = 0.10 it verifies (p = 0.003). The procedure
catches exactly the failure the naive transfer ships, with under half the
target-language labels.

### 5.5 Baselines and ablations at matched answer rate  [G4]

At α = 0.15, where every configuration with a model can certify; "risk @
coverage" is each configuration's risk when answering the same share of queries
as Aletheia:

| configuration | kvkk-tr: answer / risk / risk @ 0.92 | en-public: answer / risk / risk @ 0.77 |
|---|---|---|
| naive RAG, no verifier | 0.996 / 0.380 / 0.380 | 1.000 / 0.388 / 0.388 |
| post-filter, NLI ≥ 0.5, no gate | 0.915 / 0.064 / — | 0.767 / 0.000 / — |
| self-consistency (n = 5) | not run | not run |
| **Aletheia** (NLI, strict, LTT) | **0.915 / 0.065 / 0.064** | **0.768 / 0.000 / 0.000** |
| (b) token-overlap verifier | 0.267 / 0.096 / 0.324 | 0.000 / — / 0.212 |
| (c) no citation forcing | 0.001 / 0.191 / 0.181 | 0.000 / — / 0.182 |
| flagged instead of strict | 0.008 / 0.158 / 0.344 | 0.312 / 0.007 / 0.231 |
| loss from the verifier (circular) | 0.915 / 0.065 | 0.768 / 0.000 |

**G4 holds** against the baseline that can be run: at the same answer rate the
naive system's risk is its base rate, 31.5 points higher in Turkish and 38.8 in
English. The uncalibrated post-filter lands at the same operating point as
Aletheia, which is what §5.3 predicts — the gain over it is the certificate,
not the risk. Self-consistency and an LLM judge need a generative model and were
not run.

**The circular loss is the finding of this table.** Certified on labels the
verifier produces itself, the Turkish calibration set shows **0 failures in 503
answered responses** — a bound of essentially zero. Against the truth it is 32
failures, 6.4%. The first draft could only state that its guarantee was
conditional on the verifier (§7.3 there); this is the size of that condition.

### 5.6 Does the bound hold where the truth is exact?

The synthetic validation from the first draft stands and is still in the test
suite: responses whose statistic is informative about their loss, overall
failure rate 0.25, α = 0.10, 60 runs of 4,000. Held-out selective risk stays at
or below α in the certified runs; the marginal formulation fails 56% of the
time. The drift monitor is validated the same way: on 300 exchangeable
sequences of 400 requests at alarm level 20, 3.7% ever alarmed, under Ville's
1/20 = 5%; a shift that begins after 1,000 stable requests is caught within 120
more, and a sustained one from the start within a median of 60.

### 5.7 Latency

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

### 6.4 A hallucination that was not one

The first full evaluation run showed the verifier accepting 16% of corrupted
Turkish claims. Reading them, a large share were not false: the corruption had
renumbered a paragraph label — "(2)" to "(1)" — and the provision underneath was
still true. Others changed the law number inside an amendment annotation. A
third rule "negated" the verb *yürütür* into the ungrammatical *yürü değildir*,
which any verifier rejects for the wrong reason. The first two made the verifier
look worse than it is and the third made it look better; the aggregate number
looked plausible either way. The fix was rules — paragraph labels, annotations
and bracketed ids are not facts; a copula is recognised by consonant voicing —
and a test for each. **The error model is part of the measurement apparatus and
deserves the same suspicion.**

---

## 7. Limitations

Stated plainly, because a project about honest uncertainty cannot be vague here.

**7.1 The questions are machine-drafted and unverified.** All 1,500 queries
were drafted by a language model from the corpus. Evidence quotes are checked
verbatim against the corpus mechanically, and every unanswerable query was
checked by searching for its key terms, but the PRD's requirement — a person
verifies every triple — has not been met. A wrong gold label moves the
retrieval and usefulness numbers; it does not move the risk numbers, whose loss
comes from injected corruptions rather than from the gold answers. Review tooling
exists (`make review`), notes on the doubtful items are in
`eval/datasets/REVIEW-NOTES.md`, and `--verified-only` restricts any run to what
a person has signed off.

**7.2 The bound is conditional on the error model.** Hallucinations are injected
— numbers, polarity, domain terms, at 15% per claim — not produced by a
language model. The bound says nothing about fabricated content with no
counterpart in the evidence, subtle paraphrase drift, omissions, or any
particular model's error mix. This replaces the first draft's unmeasurable
condition (verifier accuracy) with a stated, controllable one; it does not
remove the condition. With a generative backend the machinery is unchanged and
the gold labels would come from people or a judge.

**7.3 Relevance is not bounded.** The system answers 81–91% of questions whose
answer is absent from the corpus, with claims that are supported and
irrelevant. The guarantee is about support. Out-of-corpus detection — for
instance a calibrated gate on reranker relevance — is the most important missing
component for the regulated-domain persona.

**7.4 The verifier is conservative to the point of cost.** It rejects 36–61% of
claims copied verbatim from their evidence, which is why useful-answer rates
are 15–26%. Windowed premises fix the rejections and break the guarantee
(§5.2). Better Turkish entailment — the 31% false-accept rate on term swaps is
the binding constraint on G2 in Turkish — is the next investment, and PRD §9
already names the route (a LoRA fine-tune validated on a few hundred pairs).

**7.5 Exchangeability is tested only through the statistic.** The drift monitor
sees the distribution of risk statistics. A shift in P(loss | statistic) that
leaves that distribution alone is invisible without labels on live traffic.

**7.6 Not run.** The reranker ablation, verifier ablations on kvkk-en, the
hallucination-rate sweep (all available as `make collect-ablations`), and the
self-consistency and LLM-judge baselines, which need a generative model and an
API key this evaluation did not have.

**7.7 Latency with the real verifier misses the SLO** by roughly 4× (§5.7),
and **7.8** the deployment is single-node, single-region, with no erasure path:
the append-only store cannot satisfy a KVKK deletion request.

---

## 8. Conclusion

The engineering claim holds: risk control runs inside a RAG serving path with
per-stage tracing, per-tenant admission control, a failure policy under which no
path produces an unverified answer, and an on-line test that withdraws the
bound when its assumption lapses.

The statistical claim now holds too, within stated conditions: on 1,500
questions in two languages, every certified threshold delivered its α on
held-out data, and a system that would have shipped an unsupported claim in 38–46%
of responses shipped one in 0.2–6.5%. The cost is answer quality, not answer
rate — the verifier's conservatism removes correct claims with the false ones.

The research question has an answer: **calibration does not transfer across
languages for free.** The same entailment model, on the same legal provisions,
is twenty times more willing to accept a swapped term in Turkish than in
English, and an English-certified threshold misses its budget in Turkish in 84%
of splits. Certifying in one language and *verifying* the transferred threshold
with a single-hypothesis test in the other catches this with under half the
labels a recalibration needs.

The most transferable lesson is still §6: a guarantee, a retrieval stack, an
abstention policy and now an error model each failed in a way that looked
correct from the inside. A measurement apparatus deserves the same suspicion as
the thing it measures.

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
- Vovk, Nouretdinov & Gammerman. *Testing exchangeability on-line.* (conformal
  test martingales, the Simple Jumper)
- Ville. *Étude critique de la notion de collectif.* (the maximal inequality)
- Laban et al. *SummaC: Re-visiting NLI-based models for inconsistency detection
  in summarization.* (windowed premises)
- Kryściński et al. *Evaluating the factual consistency of abstractive text
  summarization* (FactCC — rule-based claim corruption).

## Reproducing

```bash
docker compose up -d postgres && make testdb
make corpora                               # re-fetch KVKK TR/EN, Wikipedia, arXiv (optional; committed)
make collect PY=.venv/bin/python           # run the pipeline once per dataset (~1 h on Apple silicon)
make eval PY=.venv/bin/python              # every table above -> eval/results/experiments.md
make review DATASET=kvkk-tr PARALLEL=kvkk-en   # verify the drafted questions by hand
cd gateway && go run ./cmd/loadtest -c 20 -n 400
```
