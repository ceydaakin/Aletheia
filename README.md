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

**Status: week 1 of 12 — scaffold.** The topology, contracts, and deployment story are
real and running end to end; the retrieval, generation, verification, and calibration
internals are stubs returning well-formed placeholder data. See
[docs/PRD.md](docs/PRD.md) for the full product spec (Turkish) and
[docs/adr/](docs/adr/) for the architecture decisions.

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

## Repository layout

| Path | What lives there |
|---|---|
| `gateway/` | Go gateway: HTTP/SSE, auth, tenancy, orchestration, timeouts, metrics. Zero external deps. |
| `python/src/aletheia/` | Shared Python package; one sub-package per service, one container each. |
| `python/src/aletheia/retrieval/` | Hybrid search: Postgres FTS (BM25) + pgvector dense + cross-encoder rerank, fused with RRF. |
| `python/src/aletheia/generation/` | Prompt construction and citation-constrained decoding. |
| `python/src/aletheia/verifier/` | Claim decomposition + NLI entailment scoring. |
| `python/src/aletheia/risk/` | Calibration and runtime threshold decisions — *the heart of the project*. |
| `python/src/aletheia/ingestion/` | NATS worker: parse → chunk → embed → version. |
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
```

Running a Python service outside Docker:

```bash
cd python
python -m venv .venv
.venv/Scripts/activate                    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn aletheia.retrieval.app:app --port 8001 --reload
```

## Roadmap

| Week | Milestone |
|---|---|
| 1 | **Scope lock, corpora, repo skeleton — you are here** |
| 2 | Ingestion + bitemporal chunk store |
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
