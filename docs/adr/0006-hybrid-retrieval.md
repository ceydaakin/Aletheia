# ADR-0006 — Hybrid retrieval: RRF over BM25 and dense, reranked

- **Status:** Accepted
- **Date:** 2026-08-06
- **Relates to:** ADR-0002, PRD F2, G1/G2 (retrieval bounds what can be answered)

## Context

Retrieval is upstream of everything the guarantee is about. A claim can only be
supported by a chunk that was actually retrieved, so a chunk that retrieval misses is
not merely a worse answer — it is an abstention, or worse, an answer built on the
second-best evidence. Recall is therefore the metric that matters most here, more than
precision: the reranker and the verifier can discard a bad chunk that was retrieved,
but nothing downstream can recover one that was not.

The corpora make this harder than the usual benchmark. Turkish is agglutinative, so
`sözleşmeyi`, `sözleşmenin`, and `sözleşmelerde` are all the same lemma and a
substring-free lexical match finds none of them from `sözleşme`. Meanwhile the legal and
financial vocabulary is exactly where dense retrieval is weakest: embeddings blur
`otuz gün` and `altmış gün` into near-identical vectors, because the sentences differ in
one number that carries the entire meaning.

Neither method alone is adequate, and the two fail in uncorrelated ways.

## Decision

**Hybrid retrieval fused with Reciprocal Rank Fusion, then reranked.**

*Lexical* is Postgres full-text search with a language-appropriate configuration chosen
per chunk: `turkish` (Snowball) for `lang='tr'`, `english` for `lang='en'`, `simple`
otherwise. The configuration is applied in a generated `tsvector` column so it can never
disagree with the text it indexes. Turkish additionally goes through `unaccent`, because
`ş/s`, `ğ/g`, and `ı/i` are routinely typed both ways in queries even when the corpus is
consistent.

Snowball's Turkish stemmer is a compromise and this ADR records it as one: it is a
suffix stripper, not a morphological analyzer, and it will under-stem the longer
derivational chains that legislative prose is full of. A real analyzer (Zemberek) is the
upgrade path, deferred until week 9 measures whether Recall@10 on TR-Domain actually
justifies running a JVM service.

*Dense* is pgvector HNSW over cosine distance with a multilingual embedding model.

*Fusion is RRF, not score normalisation.* BM25 scores are unbounded and
corpus-dependent; cosine similarities are bounded and roughly calibrated. Any weighted
sum of the two requires normalising one into the other's range, and every such
normalisation has a free parameter that has to be tuned per corpus and then quietly
becomes wrong when the corpus changes. RRF uses only ranks:

    score(chunk) = Σ_over_lists 1 / (k + rank)

with k = 60, the value from Cormack et al. and the one every subsequent paper reuses.
It has one parameter, it is insensitive to it, and it cannot be broken by a corpus whose
score distribution shifts. Given that corpus updates already threaten the calibration's
exchangeability assumption (PRD §5.2), a fusion rule that is *also* sensitive to corpus
statistics would be compounding a problem we already have.

*Reranking* is a cross-encoder over the fused top-k, cut to the top-n that generation
sees. This is the one place precision is bought back.

**Both retrieval arms are temporal and tenant-scoped in SQL, not in Python.** Every
query goes through the same predicate set — tenant, valid time, transaction time — and
filtering happens *before* ranking, not after. Post-filtering a top-k would silently
shrink the result set when old versions crowd out current ones, which is a recall bug
that looks like a corpus gap.

**Dense retrieval degrades to lexical-only rather than failing.** Chunks ingested with a
null embedding (the default backend, ADR-0005) are excluded from the vector arm by an
explicit `embedding IS NOT NULL`. With no embeddings at all, RRF over one list is just
that list's ranking, and the system keeps working. That is what makes `docker compose
up` on a clean machine possible without torch (PRD G6).

## Consequences

**Good.** The two arms fail independently, which is the entire argument for hybrid: the
number-sensitive query that embeddings blur is exactly the one BM25 gets right, and the
morphological variant BM25 misses is the one embeddings catch. RRF adds no tunable that
can rot. Temporal filtering in SQL means `as_of` cannot be forgotten by a caller.

**Bad, and accepted.** Two index structures per corpus — a GIN over tsvector and an HNSW
over vectors — doubles the write cost of ingestion and the storage. Measured against
corpora of 10⁵–10⁶ chunks this is affordable; it would not be at 10⁸.

**Bad.** RRF throws away score *magnitude*. A chunk that BM25 ranks first with an
overwhelming score and one it ranks first by a hair contribute identically. This is the
price of not tuning a normalisation, and it is a real loss on queries with one obviously
correct answer. If measurement later shows it costing recall, the fallback is a weighted
RRF with per-arm weights fitted on the calibration split — which reintroduces a
parameter, so it needs evidence first.

**Bad.** The cross-encoder is the latency bottleneck and it sits on the critical path
before generation even starts. Budget is 1.2 s for the whole retrieval stage (ADR-0001).
Reranking a fused top-24 down to 6 is what fits; reranking top-100 does not.

**Deferred.** Query expansion, HyDE, and multi-vector retrieval are all plausible recall
wins and all out of scope. The ablation table (PRD §7.3) reports what the reranker buys;
adding untested techniques before that number exists would make it unattributable.

## Alternatives considered

**Dense only.** Simplest, one index, and what most RAG demos do. Rejected on the number
problem above: for the regulated-sector persona, `thirty days` versus `sixty days` is
the whole question, and that distinction is close to invisible in embedding space.

**Weighted score fusion (normalised BM25 + cosine).** Can outperform RRF when tuned, and
retains score magnitude. Rejected for v1 because the tuning is per-corpus and the project
already has a hard enough time keeping calibration valid across corpus changes. Worth
revisiting once there is a calibration split to fit weights on honestly.

**A dedicated search engine (Elasticsearch/OpenSearch) for the lexical arm.** Better
analyzers, including a real Turkish one. Rejected for the same reason ADR-0002 rejected a
separate vector store: a second system means non-transactional writes, and a chunk that
is lexically findable but not densely findable (or vice versa) is a silent recall bug
that only shows up as a mysteriously low score.
