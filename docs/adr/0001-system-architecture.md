# ADR-0001 — System architecture: Go gateway, Python model services

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Ceyda Akın
- **Relates to:** PRD §5, NFR latency/availability targets

## Context

Aletheia has to satisfy two requirement families that pull in opposite directions.

The **serving** family: p50 ≤ 1.8 s and p95 ≤ 3.5 s end to end with the verifier in the
path, ≥ 20 concurrent requests on a single Hetzner node, per-tenant isolation, SSE
streaming, and — the hard part — correct behaviour under partial failure. Four network
hops happen inside one user-visible request (retrieval → generation → verification →
decision). Any of them can be slow. A system whose whole value proposition is "we do not
emit unverified answers" cannot respond to a verifier timeout by emitting an unverified
answer.

The **modelling** family: multilingual embeddings, a cross-encoder reranker, an NLI
entailment model, tokenizers, and a conformal calibration routine. All of that lives in
the Python ecosystem and is not worth reimplementing elsewhere.

A single-language system would force one of these to be uncomfortable. Python-only means
hand-rolling deadline propagation and structured concurrency in an ecosystem where
cancellation is awkward and a blocking model call can stall an event loop. Go-only means
either rewriting or FFI-binding the entire model stack.

## Decision

Split along the boundary where the two families actually separate: **I/O orchestration in
Go, model execution in Python.**

The **gateway (Go)** owns the request. It authenticates, resolves the tenant and its risk
budget, propagates a deadline, calls the four services, applies the partial-failure
policy, and writes the response (JSON or SSE). It holds no model and no business logic
about entailment or thresholds. It is written against the standard library only —
`net/http` routing patterns since Go 1.22 cover the routing needs, `log/slog` covers
structured logging, and `/metrics` is a few dozen lines of Prometheus text format. An
OpenTelemetry SDK dependency arrives in week 10 when there is something to trace to.

The **model services (Python/FastAPI)** are stateless HTTP servers: retrieval,
generation, verifier, risk. Plus one non-HTTP ingestion worker consuming NATS JetStream.
Each is a container; each exposes `/healthz`, `/readyz`, `/metrics`.

**Deadline propagation is explicit.** The gateway sends the remaining budget as an
`X-Aletheia-Deadline-Ms` header on every upstream call, and cancels the request context
when it expires. Services are expected to honour it — a retrieval call with 40 ms left
should fail fast, not start a rerank.

**Partial failure has a policy, not a default.** Per stage:

| Stage fails | Behaviour |
|---|---|
| Retrieval | `abstain(out_of_corpus)` — no evidence means no answer, by definition |
| Generation | HTTP 503. There is nothing to degrade to |
| Verifier | `abstain(insufficient_evidence)`. **Never** answer unverified |
| Risk controller | `abstain(stale_calibration)`. No threshold means no guarantee |

The through-line: every failure path lands on `abstain`, never on "answer, unchecked".
That asymmetry is the product.

## Consequences

**Good.** Each language does what it is good at. The latency-critical, concurrency-heavy,
partial-failure-heavy code is in Go where deadlines and cancellation are first-class. The
model code stays idiomatic Python. Services scale independently — the verifier is the
expected bottleneck and can be replicated without touching anything else. The gateway's
zero-dependency posture keeps its build fast and its supply chain trivial to audit, which
matters for the regulated-sector persona.

**Bad, and accepted.** Two toolchains, two test runners, two CI paths, two Dockerfiles.
Contract types are defined twice (Go structs in `gateway/internal/contract`, Pydantic
models in `aletheia.contracts`) and can drift. Mitigation for now is a contract test in
CI that round-trips a golden JSON fixture through both; if drift becomes a real cost,
generate both from one JSON Schema. That is deliberately deferred — a code generator in
week 1 is speculative infrastructure.

**Also bad.** Four in-process hops add serialization overhead that a monolith would not
pay. Measured budget: ~15–25 ms total for local HTTP + JSON at expected payload sizes,
against a 3.5 s p95 target. Acceptable. If it stops being acceptable, the fallback is to
co-locate the verifier and risk controller in one process — they are on the same critical
path and share no state with the others.

## Alternatives considered

**Python monolith with an async orchestrator.** Fewer moving parts, one language, faster
week-1 velocity. Rejected on partial-failure and deadline semantics: getting reliable
cancellation of a blocking model call in Python requires process pools and careful
plumbing, and that plumbing is exactly the code that must not be subtly wrong. This
project's differentiator is that its failure modes are principled.

**Go gateway calling models via gRPC.** Better ergonomics than JSON for typed contracts
and streaming, and generated types would solve the drift problem above. Rejected for now
because it adds protobuf toolchain to both sides in week 1, and the payloads are small
enough that JSON's cost is not measurable against the budget. Worth revisiting at week 8
if the contract test proves annoying.

**Everything in one process, model calls via FFI.** Rejected outright — pinning CPython
into a Go binary trades a well-understood network boundary for a poorly-understood memory
one.
