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

**Status: week 2 of 12.** Ingestion and the bitemporal chunk store are real — documents
are parsed, chunked with exact character spans, versioned, and queryable at any point in
history. Retrieval, generation, verification, and calibration are still stubs returning
well-formed placeholder data. See [docs/PRD.md](docs/PRD.md) for the full product spec
(Turkish) and [docs/adr/](docs/adr/) for the architecture decisions.

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
| `eval/` | Datasets, harness, and result tables. |
| `docs/` | PRD and ADRs. |
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
| 2 | **Ingestion + bitemporal chunk store — you are here** |
| 3 | Hybrid retrieval + reranker |
| 4 | EN eval set v1 (400 verified QA triples) |
| 5 | Cited generation + claim decomposer |
| 6 | Verifier integration |
| 7 | **Risk controller + calibration — critical path** |
| 8 | Go gateway hardening, SSE, timeout/fallback |
| 9 | TR corpus + cross-lingual calibration experiment |
| 10 | k3s deployment, OTel, Grafana |
| 11 | Ablations + final result tables |
| 12 | Technical report, demo video, blog post |

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
