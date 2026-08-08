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

**Status: the pipeline is real end to end.** Ingestion, the bitemporal store, hybrid
retrieval, claim decomposition, NLI verification, and Learn-then-Test calibration all
run; nothing is a stub returning placeholder data. What is *not* done is the science:
the eval sets are small, and the default generator is extractive and therefore cannot
hallucinate, so no bound produced today transfers to a generative system. See
[Where this actually stands](#where-this-actually-stands) below, [docs/PRD.md](docs/PRD.md)
for the product spec (Turkish), and [docs/adr/](docs/adr/) for the decisions.

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

### Week 3 baseline — bootstrap-tr

24 answerable queries, 3 deliberately unanswerable, 8 documents, 12 chunks, lexical arm
only (no embeddings):

| configuration | recall@1 | recall@3 | recall@5 | recall@10 | nDCG@10 | MRR |
|---|---|---|---|---|---|---|
| lexical-only | 0.576 | 0.806 | 0.840 | 0.924 | 0.831 | 0.910 |

**Read recall@1 and recall@3, not recall@10.** With 12 chunks in the corpus a top-10 cut
returns most of it, so recall@10 flatters any system that returns anything. This is a
smoke test with real signal, not a benchmark result — week 4 replaces it with 400
verified QA triples.

Retrieval returns something for all three unanswerable queries. That is expected, not a
defect: disjunctive matching retrieves anything sharing a term, so retrieval alone cannot
identify out-of-corpus questions. Deciding that no answer is supportable is the
verifier's and the risk controller's job.

## The guarantee

A threshold is not a constant in a config file. It is fitted on a labelled calibration
set, certified by a hypothesis test, and stored with the corpus snapshot it was fitted
on:

```bash
cd python
VERIFIER_BACKEND=nli python -m aletheia.eval.calibrate \
  --dataset ../eval/datasets/bootstrap-tr --alpha 0.05 --curve ../eval/results/curve.json
```

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

## Where this actually stands

Honest accounting, because the whole point of this project is not overclaiming.

**Real and measured.** The bitemporal store, hybrid retrieval, claim decomposition, and
the NLI verifier. The verifier scores `otuz gündür` at 0.985 against its evidence and the
contradicting `altmış gündür` at 0.139 — the discrimination the entire thesis rests on.
The lexical-overlap baseline scores that same false claim *above* the support threshold,
which is why it is a baseline and not a verifier.

**Real but not yet meaningful.** The calibration machinery is verified against synthetic
data with known ground truth: across 60 runs, thresholds certified at α held on unseen
data. But on `bootstrap-tr` it correctly certifies *nothing* — 27 queries is far below the
135-response floor. That is the machinery working, not failing.

**Not done.**

- **Eval sets are far too small** (PRD §7.2 asks for ~400 hand-verified triples per
  corpus; there are 27). This is the binding constraint on every number.
- **The default generator is extractive** — it copies sentences from retrieved chunks, so
  it cannot hallucinate. A bound calibrated against it measures retrieval quality and
  verifier strictness, *not* unsupported generation, and does not transfer. The Anthropic
  backend exists and needs a key.
- **The verifier's own error rate is not folded into the bound** (PRD open question 4).
  Calibration labels come from the verifier, so the guarantee is conditional on it.
- Weeks 8–12 of the roadmap: gateway hardening, cross-lingual calibration, k3s, OTel,
  ablation tables, the technical report.

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
| `ops/` | Prometheus/Grafana config; k3s manifests land here in week 10. |

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
| 3 | **Hybrid retrieval + reranker — you are here** |
| 4 | EN eval set v1 (400 verified QA triples) |
| 5 | Cited generation + claim decomposer |
| 6 | Verifier integration |
| 7 | **Risk controller + calibration — critical path** |
| 8 | **Go gateway hardening, admission control, p95 measured — done** |
| 9 | TR corpus + cross-lingual calibration — blocked on eval-set size |
| 10 | **k3s deployment, OTel, Grafana — done** |
| 11 | Ablations + final result tables — blocked on eval-set size |
| 12 | **Technical report + blog post — done; demo video outstanding** |

Week 7 is the critical path. If the schedule slips, the Turkish track (week 9) narrows;
the risk controller never does.

## Honest limitations

The guarantee rests on **exchangeability** between the calibration set and live traffic.
Corpus updates and query-distribution shift break it. Drift detection is therefore not a
nice-to-have but part of the guarantee: when drift is detected the system enters degraded
mode and says so in the response, rather than quietly continuing to quote a bound that no
longer holds. The verifier's own error rate is likewise not yet folded into the bound —
see open question 4 in the PRD.

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
