# Aletheia — Risk-Controlled RAG Gateway

> A production RAG gateway that puts a **statistical upper bound** on the rate of
> unsupported claims in its answers, verifies evidence at the sentence level, and
> deliberately **abstains** when it cannot honour that bound.

Most RAG systems answer confidently and leave verification to the reader. Aletheia
inverts that: every sentence is decomposed into an atomic claim, checked against the
chunks it cites with an NLI verifier, and the whole response is gated by a threshold
chosen via distribution-free risk control (Learn-then-Test / RCPS). The result is a
guarantee you can write in a contract:

```
P(unsupported claim) <= 0.05, with 95% confidence — calibration_id=cal_2026_07_tr
```

…and, when the evidence is too thin to honour it, an `abstain` with a reason code and
the three best sources instead of a fluent guess.

**Status: built and measured; evaluation questions await human verification.**
Every component runs end to end, the guarantee is evaluated on 1,500 questions over a
parallel Turkish–English legal corpus and an English public set, and the headline
research question has an answer: **an English-calibrated threshold does not hold in
Turkish** (held-out risk 0.065 against α = 0.05, over budget in 84% of splits). The
questions are machine-drafted and still `draft` until a person verifies them. See
[Results](#results), [Where this actually stands](#where-this-actually-stands), the
[technical report](docs/report/aletheia.md), [docs/PRD.md](docs/PRD.md) (Turkish), and
[docs/adr/](docs/adr/).

---

## Architecture

```
Client ──POST /v1/answer──▶ Gateway (Go)
                              │  auth, tenant resolution, rate limit,
                              │  deadline propagation, partial-failure policy
                              ▼
              ┌──────────┬───────────┬──────────┬──────────┐
              │Retrieval │Generation │ Verifier │   Risk   │  (Python / FastAPI)
              │ BM25 +   │ citation- │   NLI    │Controller│
              │ pgvector │constrained│entailment│ conformal│
              │ + rerank │  decoding │          │ threshold│
              └────┬─────┴───────────┴──────────┴─────┬────┘
                   │                                  │
            Postgres + pgvector                 Calibration job
          (bitemporal chunk store)                (periodic)
                   ▲
            Ingestion worker ◀── NATS JetStream
```

The gateway owns the request: it is the only component with a deadline, and it degrades
rather than hangs. If the verifier times out, the response is not silently downgraded to
"unverified" — it becomes an `abstain`, because an unverified answer is exactly what this
system exists to not emit. See [ADR-0001](docs/adr/0001-system-architecture.md).

## Quick start

```bash
cp .env.example .env
docker compose up --build          # add --profile obs for Prometheus + Grafana
curl -s localhost:8080/healthz
```

Ask something:

```bash
curl -s localhost:8080/v1/answer \
  -H 'Authorization: Bearer demo-key-change-me' \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the termination notice period?","risk_budget":0.05,"mode":"flagged"}' | jq
```

Stream it (SSE — provisional tokens first, then a final `decision` event):

```bash
curl -N localhost:8080/v1/answer \
  -H 'Authorization: Bearer demo-key-change-me' \
  -H 'Content-Type: application/json' \
  -d '{"query":"...","stream":true}'
```

## Loading documents

Synchronously, straight to Postgres — the path for corpus loads and eval runs:

```bash
cd python
python -m aletheia.ingestion.cli load ../corpora/demo --tenant demo --lang en
python -m aletheia.ingestion.cli list --tenant demo
```

Or asynchronously through the queue, which is what the API uses:

```bash
curl -s localhost:8005/ingest -H 'Content-Type: application/json' -d '{
  "tenant_id":"demo","doc_id":"policy.pdf","filename":"policy.pdf",
  "source_uri":"file:///corpora/demo/policy.pdf","valid_from":"2024-01-01T00:00:00Z"}'
curl -s localhost:8005/jobs/<job_id>
```

Documents are **versioned bitemporally** (ADR-0002, ADR-0005). Re-ingesting identical
text is a no-op — a nightly sync must not look like corpus drift. Amending a document
closes the previous version's validity interval rather than overwriting it, so the same
query answers differently depending on when you ask about:

```sql
SELECT text FROM chunks_as_of('demo', '2024-03-01');  -- what was in force then
SELECT text FROM chunks_as_of('demo', now());         -- what is in force now
```

Correcting a mistake is a *different* operation from recording an amendment
(`--correction`): it retracts the old rows instead of giving them a period in which they
applied. Getting that distinction wrong erases history, which is why the caller has to
state which one they mean.

## Retrieval

Lexical and dense arms run concurrently and fuse with Reciprocal Rank Fusion, then the
fused head is reranked to what generation sees (ADR-0006). Recall is what matters here:
a chunk retrieval misses cannot be recovered by the reranker or the verifier, so the
arms are deliberately wider than the final cut.

The lexical arm picks its stemmer from each chunk's language — Turkish is agglutinative,
so `sözleşme` has to match `sözleşmeyi` and `sözleşmelerde`, and `unaccent` makes
`sozlesme` match all three. Dense retrieval is optional: with `EMBEDDING_BACKEND=null`
chunks carry no vectors, RRF over a single list is that list's ranking, and everything
still works. Turning it on is a config flip plus a backfill:

```bash
pip install -e "python[models]"
EMBEDDING_BACKEND=sentence-transformers python -m aletheia.ingestion.backfill --tenant demo
```

Measure it:

```bash
cd python
python -m aletheia.eval.retrieval --dataset ../eval/datasets/bootstrap-tr --ingest
```

On the evaluation sets, hybrid retrieval with reranking puts the passage that answers
the question into the top six chunks for 96% (kvkk-tr), 97% (kvkk-en) and 99.7%
(en-public) of answerable queries. Retrieval returns something for unanswerable queries
too — disjunctive matching retrieves anything sharing a term — so deciding that no
answer is supportable is left to the verifier and the risk controller, and on
relevance (as opposed to support) they do not yet manage it; see the limitations.

## The guarantee

A threshold is not a constant in a config file. It is fitted on a labelled calibration
set, certified by a hypothesis test, and stored with the corpus snapshot it was fitted
on:

```bash
make collect DATASETS=kvkk-tr PY=.venv/bin/python   # run the pipeline once, cache responses
make calibrate DATASET=kvkk-tr ALPHA=0.10 PY=.venv/bin/python
```

**The loss is ground truth, not the verifier's opinion** ([ADR-0008](docs/adr/0008-gold-loss-from-controlled-hallucination.md)).
Generated claims are corrupted at a known rate — a changed number, flipped polarity, a
swapped legal term — and a response fails if a claim it would return was corrupted. The
verifier produces the statistic, never the label, so a corrupted claim it accepts is
inside the bound. Certified instead on the verifier's own labels, the Turkish set shows
0 failures in 503 answers; the truth is 32.

The risk controller reads that record at request time. **With no certified, fresh
calibration for the requested α, it abstains** — there is no fallback threshold and no
default, because a guessed one would be a guarantee-shaped string with nothing behind
it. `GET /calibration/{tenant}` reports what is currently in force.

The controlled quantity is *selective* risk — P(response is wrong | we answered), not
P(wrong and answered). The difference is large at low answer rates and favours the
vendor, so it gets its own decision record ([ADR-0007](docs/adr/0007-selective-risk.md)).
Getting it wrong is not hypothetical: the first implementation certified the marginal
risk while evaluating the selective one, and held-out risk exceeded α in **56% of
certified runs** with nothing in the code looking wrong.

Certification has a hard floor. With zero observed failures the p-value is (1−α)^m, so
Bonferroni over a 50-point λ grid needs **135 answered calibration responses at α=0.05**
regardless of how good the system is. The calibration job computes this and says so when
it fails, because "the verifier is bad" and "the sample cannot say anything" look
identical from outside and have opposite fixes.

## Drift detection

The bound holds only while live traffic is exchangeable with the calibration set
([ADR-0009](docs/adr/0009-online-drift-detection.md)). Two signals withdraw it: any
document change after the calibration's corpus snapshot, and a conformal test martingale
over live risk statistics crossing `DRIFT_ALARM_LEVEL`. By Ville's inequality the chance
of *ever* raising a false alarm is at most 1/level over the calibration's whole
lifetime, however often it is checked. Either signal turns responses into
`abstain(drift_detected)` with `degraded: true` until the calibration job runs again;
`GET /calibration/{tenant}` shows the martingale.

## Results

1,500 questions: 550 per language on the article-aligned KVKK corpus (400 answerable,
150 adversarial with no answer in the corpus) and 400 on en-public. δ = 0.05, 200
random 60/40 splits per cell, claim corruption rate 0.15. Full tables:
[eval/results/experiments.md](eval/results/experiments.md) (`make eval`).

| | kvkk-en | kvkk-tr | en-public |
|---|---|---|---|
| responses with an unsupported claim, no verifier | 46.2% | 38.0% | 38.8% |
| … with Aletheia at α = 0.15 (held-out) | **0.2%** | **6.5%** | **0.0%** |
| answer rate at α = 0.05 | **88%** | not certified | **77%** |
| lowest α that certifies in every split | 0.05 | 0.15 | 0.05 |
| verifier false-accept rate on swapped legal terms | 1.3% | **30.8%** | — |
| held-out risk ≤ α in every certified cell (10 α values) | yes | yes | yes |

- **G1 holds**: wherever a threshold was certified, it delivered its α on held-out data.
- **G5, cross-lingual**: the English α = 0.05 threshold, applied to Turkish, delivers
  6.5% — over budget in 84% of splits. A single-hypothesis transfer test needs 59
  labelled Turkish answers (vs 135 to recalibrate) and refuses that transfer correctly.
- **G2** is met in English and missed in Turkish at α = 0.05, for a measured reason: the
  entailment model accepts 31% of Turkish term swaps.
- **Not bounded: relevance.** The system still answers 81–91% of questions whose answer
  is absent — with supported, irrelevant sentences.

## Evaluation data

| dataset | corpus | queries |
|---|---|---|
| `kvkk-tr`, `kvkk-en` | Law 6698 (KVKK, 2024 consolidated) + 3 regulations; 90 provisions per language, **article-aligned** — same filename, same provision | 550 each, parallel ids |
| `en-public` | 30 Wikipedia articles in near-duplicate clusters + 120 arXiv abstracts | 400 |
| `bootstrap-tr` | 8 hand-written policy documents | 27 (hand-labelled) |

Corpora are fetched by `scripts/fetch_kvkk.py` and `scripts/fetch_en_public.py`
(sources and licences in each `SOURCES.md`). Questions were drafted by a language model
with verbatim evidence checked mechanically, and **every one is `draft` until a person
verifies it**:

```bash
make review DATASET=kvkk-tr PARALLEL=kvkk-en   # one decision covers both languages
make review DATASET=en-public
```

Items the drafters flagged as doubtful are listed in
[eval/datasets/REVIEW-NOTES.md](eval/datasets/REVIEW-NOTES.md). Any run can be restricted
to verified questions with `--verified-only`.

## Where this actually stands

Honest accounting, because the whole point of this project is not overclaiming.

**Real and measured.** Every component, on 1,500 questions in two languages, with a
loss that does not come from the component under test. The guarantee holds where it
certifies; the cross-lingual finding is measured and explained.

**Not done.**

- **The questions are unverified drafts** (PRD §7.2 requires hand verification).
  Mechanical checks cover evidence and labels; naturalness and correctness need a
  person. The risk numbers do not depend on the gold answers; retrieval and usefulness
  numbers do.
- **The bound is conditional on the error model**: number, polarity and term errors at
  a 15% rate — not fabrication, paraphrase drift or omission, and not any particular
  LLM. The Anthropic backend exists and needs a key.
- **Relevance is not bounded**; out-of-corpus questions are mostly answered with
  supported, irrelevant sentences.
- **The verifier is conservative** (36–61% of verbatim claims rejected), so useful
  answers are 15–26% of queries, and with it latency misses the SLO by ~4×.
- **Not run**: reranker and rate ablations (`make collect-ablations`), self-consistency
  and LLM-judge baselines (need an API key), the live demo and demo video.

## Repository layout

| Path | What lives there |
|---|---|
| `gateway/` | Go gateway: HTTP/SSE, auth, tenancy, orchestration, timeouts, metrics. Zero external deps. |
| `python/src/aletheia/` | Shared Python package; one sub-package per service, one container each. |
| `python/src/aletheia/retrieval/` | Hybrid search: Postgres FTS (BM25) + pgvector dense + cross-encoder rerank, fused with RRF. |
| `python/src/aletheia/generation/` | Prompt construction and citation-constrained decoding. |
| `python/src/aletheia/verifier/` | Claim decomposition + NLI entailment scoring. |
| `python/src/aletheia/risk/` | Calibration and runtime threshold decisions — *the heart of the project*. |
| `python/src/aletheia/ingestion/` | Parse → chunk → embed → version, over NATS or the CLI. |
| `db/migrations/` | SQL migrations. The bitemporal chunk store lives here. |
| `python/src/aletheia/eval/` | Metrics and the evaluation harness. A tool, not a service, so it may import from what it measures. |
| `eval/datasets/` | Labelled corpora. Gold labels name documents, not chunk ids, so they survive a chunking change. |
| `docs/` | PRD, ADRs, and the technical report. |
| `ops/k8s/` | k3s manifests. Secrets are created out of band; see `secret.example.yaml`. |
| `ops/` | Prometheus/Grafana config. |
| `eval/datasets/` + `eval/results/` | Corpora, drafted questions, cached pipeline records, and the generated result tables. |

## Development

```bash
make help          # list targets
make up            # docker compose up --build
make test          # go test ./... + pytest
make fmt lint      # gofmt + ruff
make testdb        # create the scratch database the store tests need
```

Running a Python service outside Docker:

```bash
cd python
python -m venv .venv
.venv/Scripts/activate                    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn aletheia.retrieval.app:app --port 8001 --reload
```

The bitemporal store's guarantees are database constraints, so those tests need a real
Postgres and skip without one:

```bash
make testdb
export ALETHEIA_TEST_DATABASE_URL=postgresql://aletheia:aletheia@localhost:5432/aletheia_test
cd python && pytest
```

Point that at a **scratch** database — the fixture truncates every table between tests,
including `tenants`.

## Roadmap

| Week | Milestone |
|---|---|
| 1 | Scope lock, corpora, repo skeleton |
| 2 | Ingestion + bitemporal chunk store |
| 3 | Hybrid retrieval + reranker — done |
| 4 | Eval sets: 1,500 questions drafted and mechanically checked — **human verification pending** |
| 5 | Cited generation + claim decomposer — done |
| 6 | Verifier integration; discrimination by error type reported — done |
| 7 | Risk controller + calibration on gold labels (ADR-0008) — done |
| 8 | Go gateway hardening, admission control, p95 measured — done |
| 9 | TR corpus + cross-lingual calibration — done; transfer fails at α = 0.05, transfer test proposed |
| 10 | k3s deployment, OTel, Grafana; drift detection (ADR-0009) — done |
| 11 | Ablations + final result tables — done, reranker/rate ablations not run |
| 12 | Technical report — updated; demo video out of scope |

Week 7 is the critical path. If the schedule slips, the Turkish track (week 9) narrows;
the risk controller never does.

## Honest limitations

The guarantee rests on **exchangeability** between the calibration set and live traffic.
Corpus updates and query-distribution shift break it. Drift detection is therefore not a
nice-to-have but part of the guarantee: when drift is detected the system enters degraded
mode and says so in the response, rather than quietly continuing to quote a bound that no
longer holds — which is implemented, with the caveat that it can only see shifts in
the risk statistic, not in P(loss | statistic). The verifier's own error rate *is* now
inside the bound (the loss is ground truth, ADR-0008), at the price of conditioning it
on the injected error model instead — PRD open question 4, answered for that model.

## License

TBD before first public release.

## Observability

```bash
OTLP_ENDPOINT=http://tempo:4318 OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318 \
  docker compose --profile obs up -d
```

Grafana on :3000 (admin/admin) with a provisioned dashboard, Prometheus on :9090,
Tempo on :3200. One request produces a single trace across all five services —
gateway root span, one span per pipeline stage, and one server span per Python
service. The root span carries the decision, the abstain reason, the risk
statistic, and the calibration id, because "why did *this* request abstain" is the
question traces get opened for.

The response's `trace_id` **is** the OpenTelemetry trace id, so a user quoting it
from a bad answer lands on the trace directly.

The dashboard leads with answer rate and uncalibrated abstentions rather than
error rate, because the characteristic failure of this design is a system that is
up, fast, and returning nothing useful — every probe green, no errors, no answers.

## Deployment

`ops/k8s/` holds the k3s manifests (`kubectl apply -k ops/k8s`). Notable choices
are documented in `ops/k8s/README.md`; the calibration job is a CronJob whose
schedule and the controller's `CALIBRATION_MAX_AGE_HOURS` are the same decision
written twice — if the job stops, the system stops answering rather than quietly
serving a stale bound.

## Writeup

- [Technical report](docs/report/aletheia.md) — method, results, and a limitations
  section that is longer than the results section on purpose.
- [Blog post draft](docs/report/blog-post.md).
