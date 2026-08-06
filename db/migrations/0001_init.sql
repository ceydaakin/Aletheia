-- Aletheia initial schema.
--
-- The chunk store is bitemporal (ADR-0002). Two independent time axes:
--
--   valid time       (valid_from, valid_to)        when the fact was true in the world
--   transaction time (recorded_at, superseded_at)  when Aletheia knew it
--
-- Nothing is ever deleted. An amendment closes the previous row's intervals and
-- inserts a new one, so an answer given last March can still be reconstructed
-- from the evidence that actually backed it. That is the difference between an
-- audit trail and a log.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Sentinel for "still in force". A NULL upper bound would force every temporal
-- predicate to spell out IS NULL, and one forgotten branch silently returns a
-- stale chunk.
--
-- Deliberately NOT 'infinity', which is the semantically obvious choice: Python's
-- datetime cannot represent it, so psycopg raises DataError on any SELECT that
-- returns the column. Round-tripping matters more here than elegance, and
-- datetime.max maps to this value exactly. Every open interval in the schema uses
-- this function, so there is one definition to change if that ever stops being
-- true.
CREATE OR REPLACE FUNCTION forever() RETURNS timestamptz
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT '9999-12-31 23:59:59.999999+00'::timestamptz $$;

-- ---------------------------------------------------------------------------
-- Tenants
-- ---------------------------------------------------------------------------

CREATE TABLE tenants (
    tenant_id           text PRIMARY KEY,
    name                text NOT NULL,
    -- Per-tenant because a bank and a docs site do not share a tolerance for
    -- being wrong.
    default_risk_budget double precision NOT NULL DEFAULT 0.05
        CHECK (default_risk_budget > 0 AND default_risk_budget < 1),
    api_key_hash        text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Documents and chunks
-- ---------------------------------------------------------------------------

CREATE TABLE documents (
    doc_id      text NOT NULL,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    version     integer NOT NULL CHECK (version >= 1),
    title       text NOT NULL DEFAULT '',
    source_uri  text NOT NULL DEFAULT '',
    media_type  text NOT NULL DEFAULT '',
    lang        text NOT NULL DEFAULT '',
    -- Content hash: re-ingesting an unchanged document must not create a new
    -- version, or every nightly sync would look like a corpus change and trip
    -- drift detection.
    content_sha256 text NOT NULL,

    valid_from     timestamptz NOT NULL,
    valid_to       timestamptz NOT NULL DEFAULT forever(),
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    superseded_at  timestamptz NOT NULL DEFAULT forever(),

    PRIMARY KEY (tenant_id, doc_id, version),
    CHECK (valid_from < valid_to),
    CHECK (recorded_at < superseded_at)
);

CREATE TABLE chunks (
    -- Unique per tenant, not globally: two tenants may legitimately hold a
    -- document with the same id, and a global unique constraint would make one
    -- tenant's ingestion fail because of another's. Citations are always resolved
    -- inside a tenant context, so they stay readable.
    chunk_id    text NOT NULL,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    doc_id      text NOT NULL,
    version     integer NOT NULL,
    ordinal     integer NOT NULL,

    text        text NOT NULL,
    -- Character span within the document version, so a citation points at a
    -- range of a specific version rather than at "the document".
    span_start  integer NOT NULL DEFAULT 0,
    span_end    integer NOT NULL DEFAULT 0,
    lang        text NOT NULL DEFAULT '',

    valid_from     timestamptz NOT NULL,
    valid_to       timestamptz NOT NULL DEFAULT forever(),
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    superseded_at  timestamptz NOT NULL DEFAULT forever(),

    -- Dimension matches intfloat/multilingual-e5-base. Changing the embedding
    -- model is a migration, not a config change: mixed-dimension indexes and
    -- silently incomparable vectors are the same bug wearing different hats.
    embedding   vector(768),
    -- Generated rather than trigger-maintained so it can never disagree with
    -- `text`. Language-specific configurations are chosen per corpus in week 3;
    -- 'simple' is the honest placeholder until Turkish analysis is benchmarked.
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,

    PRIMARY KEY (tenant_id, chunk_id),
    FOREIGN KEY (tenant_id, doc_id, version)
        REFERENCES documents(tenant_id, doc_id, version) ON DELETE RESTRICT,
    CHECK (valid_from < valid_to),
    CHECK (recorded_at < superseded_at)
);

CREATE INDEX chunks_tsv_idx     ON chunks USING gin (tsv);
CREATE INDEX chunks_tenant_idx  ON chunks (tenant_id, valid_from, valid_to);
CREATE INDEX chunks_doc_idx     ON chunks (tenant_id, doc_id, version, ordinal);
-- HNSW over cosine distance. Build parameters are tuned in week 3 against the
-- Recall@10 target; these are the pgvector defaults.
CREATE INDEX chunks_embedding_idx ON chunks
    USING hnsw (embedding vector_cosine_ops);

-- All temporal access goes through this function. Ad-hoc interval predicates in
-- application code are how an off-by-one silently serves a superseded chunk.
CREATE OR REPLACE FUNCTION chunks_as_of(
    p_tenant_id text,
    p_valid_at  timestamptz DEFAULT now(),
    p_known_at  timestamptz DEFAULT now()
) RETURNS SETOF chunks
    LANGUAGE sql STABLE PARALLEL SAFE AS
$$
    SELECT *
    FROM chunks
    WHERE tenant_id = p_tenant_id
      AND valid_from    <= p_valid_at AND p_valid_at < valid_to
      AND recorded_at   <= p_known_at AND p_known_at < superseded_at
$$;

-- ---------------------------------------------------------------------------
-- Calibration
-- ---------------------------------------------------------------------------

-- Each row is one Learn-then-Test run: a threshold, the alpha it certifies, and
-- the corpus state it was fitted on. A threshold divorced from its corpus
-- snapshot cannot be checked for staleness, and an unverifiable guarantee is
-- worse than none.
CREATE TABLE calibrations (
    calibration_id  text PRIMARY KEY,
    tenant_id       text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    alpha           double precision NOT NULL CHECK (alpha > 0 AND alpha < 1),
    delta           double precision NOT NULL CHECK (delta > 0 AND delta < 1),
    threshold       double precision NOT NULL,
    -- Empirical numbers from the calibration set, for the risk–coverage curve.
    n               integer NOT NULL CHECK (n >= 0),
    coverage        double precision NOT NULL,
    empirical_risk  double precision NOT NULL,
    -- False means no lambda on the grid could be certified. Such a row is kept
    -- as evidence; it must never be selected at serving time.
    certified       boolean NOT NULL DEFAULT false,
    -- The transaction-time snapshot of the corpus this was fitted against.
    corpus_known_at timestamptz NOT NULL,
    statistic_name  text NOT NULL DEFAULT 'one_minus_min_support',
    lang            text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX calibrations_lookup_idx
    ON calibrations (tenant_id, alpha, created_at DESC)
    WHERE certified;

-- Individual calibration examples, kept so a run can be reproduced and audited.
CREATE TABLE calibration_examples (
    id              bigserial PRIMARY KEY,
    calibration_id  text NOT NULL REFERENCES calibrations(calibration_id) ON DELETE CASCADE,
    query           text NOT NULL,
    answer          text NOT NULL,
    statistic       double precision NOT NULL,
    -- The binary loss the bound is defined over: did the response we would have
    -- returned contain at least one unsupported claim? (ADR-0004)
    loss            boolean NOT NULL,
    -- How the label was produced. Verifier-only labels are noisy, and the
    -- technical report has to report the mix (PRD open question 4).
    label_source    text NOT NULL CHECK (label_source IN ('human', 'llm_judge', 'verifier')),
    split           text NOT NULL DEFAULT 'calibration'
        CHECK (split IN ('calibration', 'test'))
);

CREATE INDEX calibration_examples_run_idx ON calibration_examples (calibration_id, split);

-- ---------------------------------------------------------------------------
-- Request traces
-- ---------------------------------------------------------------------------

-- Feeds drift monitoring, which is part of the guarantee rather than an add-on:
-- the bound holds only while live traffic stays exchangeable with the
-- calibration set (PRD §5.2).
CREATE TABLE request_traces (
    trace_id        text PRIMARY KEY,
    tenant_id       text NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    query           text NOT NULL,
    risk_budget     double precision NOT NULL,
    mode            text NOT NULL,
    decision        text NOT NULL,
    abstain_reason  text,
    statistic       double precision,
    threshold       double precision,
    calibration_id  text REFERENCES calibrations(calibration_id),
    degraded        boolean NOT NULL DEFAULT false,
    latency_ms      integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX request_traces_tenant_time_idx ON request_traces (tenant_id, created_at DESC);
CREATE INDEX request_traces_decision_idx    ON request_traces (tenant_id, decision, created_at DESC);

-- ---------------------------------------------------------------------------
-- Seed
-- ---------------------------------------------------------------------------

-- Matches the default in .env.example. The hash is a placeholder: real key
-- hashing lands with the tenants table swap in week 8.
INSERT INTO tenants (tenant_id, name, default_risk_budget, api_key_hash)
VALUES ('demo', 'Demo tenant', 0.05, 'replace-me')
ON CONFLICT (tenant_id) DO NOTHING;

COMMIT;
