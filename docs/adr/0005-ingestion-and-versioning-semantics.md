# ADR-0005 — Ingestion and versioning semantics

- **Status:** Accepted
- **Date:** 2026-08-04
- **Relates to:** ADR-0002, PRD F1, F9; API `as_of`

## Context

ADR-0002 decided that chunks are bitemporal and nothing is ever deleted. It did not
say what *causes* a new row, and that turns out to be the harder question. "The
document changed" is at least three different events:

1. The world changed — a regulation was amended, and both the old and the new text
   were correct in their own period.
2. We were wrong — our parser mangled a table, or we ingested the wrong file. The old
   row was never correct.
3. Nothing changed — a nightly sync re-uploaded a byte-identical file.

Conflating these breaks things that matter. Treating (3) as a change makes every sync
look like corpus drift, which under PRD §5.2 invalidates the calibration and triggers
a recalibration that was not needed. Treating (2) as (1) leaves a wrong chunk in the
record as though it had been true for a period, which is exactly the audit trail this
project claims to provide.

## Decision

**Identity is the extracted text, not the file.** `content_sha256` hashes the parsed
text, not the uploaded bytes. A PDF re-exported with a new creation timestamp but
identical text is case (3) and ingestion is a no-op. Consequence to accept: a parser
upgrade that extracts text better produces a new hash, and therefore a new version.
That is correct — the corpus genuinely changed from the system's point of view.

**Two distinct write paths.**

*Amendment* (case 1, the default): the previous version's `valid_to` closes at the new
version's `valid_from`; the new version is inserted with `valid_to = infinity`. Both
rows keep `superseded_at = infinity` — both were correct knowledge, in their own
periods. `as_of` before the amendment returns the old text.

*Correction* (case 2, explicit `correction=true`): the previous version's rows get
`superseded_at = now()`; the new rows carry **the same valid interval**. The old text
is retained but marked as something we no longer assert. A query at any valid time now
returns the corrected text, while `known_at` in the past still reproduces the mistake —
which is what "what would this system have said on 12 March?" means.

**The version counter increments in both cases.** Chunk ids embed the version
(`doc_412:v3:chunk_0018`), so a citation always resolves to exactly one row and never
silently changes meaning underneath a stored answer.

**Chunk ids are deterministic**, derived from `(doc_id, version, ordinal)`. Re-running
ingestion over the same input produces byte-identical ids, which is what makes eval
runs reproducible (PRD G6).

**One document version is one transaction.** A partially ingested document is a corpus
state that no calibration was fitted on, and the guarantee would be quoted over it.
Atomicity here is a correctness requirement, not a tidiness preference.

**The database enforces the temporal invariant.** A GiST exclusion constraint forbids
two non-superseded versions of the same document with overlapping valid intervals.
Application code that gets the interval arithmetic wrong fails loudly at write time
instead of quietly serving a stale chunk at read time.

**Every write records a corpus event.** `corpus_events` is the drift signal's source:
under PRD §5.2 the guarantee holds only while live traffic stays exchangeable with the
calibration set, and a corpus change is the most direct way for that to stop being
true. Week 9 consumes this table; week 2 only has to make sure the evidence is there.

## Consequences

**Good.** Idempotent re-ingestion, so a cron sync is cheap and does not manufacture
false drift. Amendments and corrections are distinguishable in the record, which is the
difference between an audit trail and a changelog. The exclusion constraint converts a
whole class of temporal bugs from silent to loud.

**Bad, and accepted.** Two write paths mean the caller has to know which one they are
in, and they will sometimes get it wrong — an amendment recorded as a correction erases
the period during which the old text was in force. There is no way to infer intent from
the file alone, so this is pushed to the caller deliberately rather than guessed.

**Bad.** Hashing extracted text means the parser is part of the corpus identity. A
`pypdf` upgrade can version every PDF in the corpus at once. Mitigation: the parser
version is recorded on the document row, so such a mass reversion is explainable after
the fact rather than mysterious.

**Deferred.** Deletion is "close `valid_to` at now()", which is right for amendments but
is not erasure — a KVKK/GDPR deletion request needs real removal, and real removal
conflicts with an append-only audit trail. Out of scope for v1 and recorded here as an
open problem rather than pretended away.

## Alternatives considered

**Hash the raw bytes.** Simpler, and immune to parser changes. Rejected: byte-identical
re-exports are the common case in document management systems, and each one would look
like corpus drift.

**Single write path, always append a new version.** Fewer concepts. Rejected because it
cannot express "this was never true", so a parsing error stays in the record as a fact
that held for a period.

**Let the application enforce interval consistency.** Avoids `btree_gist` and a
constraint that will occasionally reject a legitimate write. Rejected: interval
off-by-ones are exactly the bug class ADR-0002 flagged as easy to get wrong and
invisible when wrong.
