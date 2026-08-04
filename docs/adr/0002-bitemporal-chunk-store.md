# ADR-0002 — Postgres + pgvector as the single store, with bitemporal chunks

- **Status:** Accepted
- **Date:** 2026-08-04
- **Relates to:** PRD F1, F2, F10; API `as_of` parameter

## Context

The primary persona is a regulated-sector operations team asking questions over policy and
legislation. Those documents get amended. "What was the termination notice period for
contracts signed after 2024?" has a different correct answer depending on which version of
the document you consult, and — separately — depending on when you ask. A store that
overwrites a chunk when its document is updated destroys the evidence for every answer
given before the update, which makes the audit trail worthless precisely where it matters.

Retrieval also needs three access patterns over the same chunks: lexical (BM25, with a
morphology-aware analyzer for Turkish), dense (embedding similarity), and metadata
filtering (tenant, validity window). And calibration records — the `(query, answer, label)`
triples and the selected λ per tenant — need to be stored transactionally alongside them,
because a threshold that does not match the corpus it was calibrated on is worse than no
threshold.

## Decision

**One Postgres instance with the `vector` extension**, holding chunks, embeddings,
full-text indexes, calibration records, and tenants.

Chunks are **bitemporal**. Two independent time axes:

- *Valid time* (`valid_from`, `valid_to`) — when the fact was true in the world, i.e. when
  the document version was in force.
- *Transaction time* (`recorded_at`, `superseded_at`) — when Aletheia knew it.

Nothing is ever deleted. An update closes the old row's interval and inserts a new one.
The API's `as_of` parameter selects the valid-time slice; transaction time answers "what
would this system have said on date X", which is the question an auditor asks.

Retrieval is **hybrid**, fused with Reciprocal Rank Fusion:
- BM25 via Postgres full-text search — `english` config for EN, and for Turkish a
  Snowball/`unaccent` pipeline, upgraded to a proper morphological analyzer if measured
  Recall@10 justifies the operational cost (evaluated week 3).
- Dense via pgvector HNSW over a multilingual embedding model.
- Cross-encoder rerank over the fused top-k, in the retrieval service.

Tenant isolation is a mandatory `tenant_id` predicate on every query, enforced by row-level
security so that a forgotten `WHERE` clause fails closed rather than leaking.

## Consequences

**Good.** One backup, one migration path, one connection pool, one thing to run in
`docker compose up`. Transactional consistency between chunks and calibration records
comes free — we can never end up serving a λ calibrated against a corpus state that no
longer exists, because the calibration row references the transaction-time snapshot it was
computed on. RLS gives tenant isolation a hard floor.

**Bad, and accepted.** pgvector's HNSW is not as fast as a dedicated vector database at
large scale, and index build times grow awkward past a few million vectors. Target corpora
are in the 10⁵–10⁶ chunk range, comfortably inside where pgvector performs well. If that
changes, the retrieval service is the only component that would need to know.

**Bad.** Bitemporal queries are genuinely harder to write and easy to get subtly wrong —
an off-by-one on an interval boundary silently returns a stale chunk. Mitigation: all
temporal access goes through named SQL functions (`chunks_as_of(tenant, valid_at)`), never
ad-hoc predicates in application code, and those functions get their own tests with
boundary cases.

**Bad.** Never deleting means the store grows monotonically. Acceptable at project scale;
a real deployment needs a retention policy on transaction time, which is out of scope for
v1 and should be recorded as such.

## Alternatives considered

**Dedicated vector DB (Qdrant/Weaviate) + Postgres for metadata.** Better vector
performance and nicer filtering ergonomics. Rejected: two stores means two-phase writes on
ingestion and no transactional guarantee between a chunk and its embedding — a crash
between the two leaves a chunk that can be found lexically but not densely, or worse, an
embedding pointing at a chunk version that was superseded. The consistency cost is not
worth the performance gain at this scale.

**Soft-delete flag instead of bitemporal intervals.** Much simpler. Rejected because it
answers "is this current?" but not "what was in force on 2024-06-01?", and the latter is
the actual user question in the primary scenario.
