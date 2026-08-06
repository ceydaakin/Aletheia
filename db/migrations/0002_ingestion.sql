-- Week 2: ingestion support.
--
-- Adds the temporal invariant as a database constraint, job tracking for
-- asynchronous ingestion, and the corpus-change event log that drift detection
-- will consume. See ADR-0005.

BEGIN;

-- Needed to combine equality columns with a range operator in one exclusion
-- constraint.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ---------------------------------------------------------------------------
-- Provenance
-- ---------------------------------------------------------------------------

-- The parser is part of the corpus identity: content_sha256 hashes extracted
-- text, so an extraction change produces new versions. Recording which parser
-- ran makes that explainable after the fact instead of mysterious (ADR-0005).
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS parser text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS parser_version text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS chunk_count integer NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- Temporal invariant
-- ---------------------------------------------------------------------------

-- Two versions of the same document may not both claim to be in force at the
-- same instant. Superseded rows are exempt: a correction deliberately shares its
-- predecessor's valid interval, and the predecessor is what gets superseded.
--
-- This turns interval off-by-ones from "silently serves a stale chunk at read
-- time" into "fails at write time", which is the whole point.
ALTER TABLE documents
    ADD CONSTRAINT documents_no_overlapping_versions
    EXCLUDE USING gist (
        tenant_id WITH =,
        doc_id WITH =,
        tstzrange(valid_from, valid_to) WITH &&
    )
    WHERE (superseded_at = forever());

-- ---------------------------------------------------------------------------
-- Ingestion jobs
-- ---------------------------------------------------------------------------

CREATE TABLE ingest_jobs (
    job_id       text PRIMARY KEY,
    tenant_id    text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    doc_id       text NOT NULL,
    status       text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'unchanged')),
    -- Which write path ADR-0005 took, once known.
    outcome      text CHECK (outcome IN ('created', 'amended', 'corrected', 'unchanged')),
    version      integer,
    chunk_count  integer NOT NULL DEFAULT 0,
    error        text NOT NULL DEFAULT '',
    -- Redelivery count. A document that fails repeatedly is a poison message and
    -- must stop being retried, or one bad PDF blocks the whole corpus.
    attempts     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ingest_jobs_tenant_idx ON ingest_jobs (tenant_id, created_at DESC);
CREATE INDEX ingest_jobs_status_idx ON ingest_jobs (status) WHERE status IN ('queued', 'running');

-- ---------------------------------------------------------------------------
-- Corpus events
-- ---------------------------------------------------------------------------

-- The drift signal's source. The guarantee holds only while live traffic stays
-- exchangeable with the calibration set (PRD §5.2), and a corpus change is the
-- most direct way for that to stop being true. Week 9 consumes this; week 2 only
-- has to make sure the evidence exists.
CREATE TABLE corpus_events (
    id          bigserial PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    doc_id      text NOT NULL,
    version     integer NOT NULL,
    event       text NOT NULL CHECK (event IN ('created', 'amended', 'corrected')),
    chunk_count integer NOT NULL DEFAULT 0,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX corpus_events_tenant_time_idx ON corpus_events (tenant_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Convenience views
-- ---------------------------------------------------------------------------

-- "What is in force right now, as far as we currently know." Reading this rather
-- than hand-writing the four temporal predicates is the difference between a
-- correct query and a nearly correct one.
CREATE OR REPLACE VIEW current_documents AS
    SELECT *
    FROM documents
    WHERE valid_to = forever()
      AND superseded_at = forever();

COMMIT;
