-- Week 3: language-aware lexical search.
--
-- 0001 indexed every chunk with the 'simple' configuration, which does no
-- stemming at all. That was an honest placeholder; this replaces it with a
-- configuration chosen per chunk from its language (ADR-0006).

BEGIN;

-- ---------------------------------------------------------------------------
-- Turkish text search configuration
-- ---------------------------------------------------------------------------

-- Snowball's Turkish stemmer plus unaccent. The stemmer is a suffix stripper
-- rather than a morphological analyzer and will under-stem the long derivational
-- chains legislative prose is full of — see ADR-0006 for why that is accepted for
-- now and what replaces it.
--
-- unaccent matters independently of stemming: ş/s, ğ/g and ı/i get typed both
-- ways in queries even when the corpus is consistent, and an unaccented query
-- against an accented index simply finds nothing.
CREATE TEXT SEARCH CONFIGURATION turkish_unaccent (COPY = turkish);

ALTER TEXT SEARCH CONFIGURATION turkish_unaccent
    ALTER MAPPING FOR hword, hword_part, word
    WITH unaccent, turkish_stem;

-- English gets the same treatment so that a query typed without diacritics still
-- matches loanwords and proper nouns that carry them.
CREATE TEXT SEARCH CONFIGURATION english_unaccent (COPY = english);

ALTER TEXT SEARCH CONFIGURATION english_unaccent
    ALTER MAPPING FOR hword, hword_part, word
    WITH unaccent, english_stem;

-- ---------------------------------------------------------------------------
-- Language-aware tsvector
-- ---------------------------------------------------------------------------

-- Chosen per row from the chunk's language. Literal regconfig casts keep the
-- expression immutable, which a generated column requires; the one-argument
-- to_tsvector(text) is only STABLE because it reads default_text_search_config,
-- and would be rejected here.
--
-- Generated rather than trigger-maintained so the index can never disagree with
-- the text it indexes.
CREATE OR REPLACE FUNCTION chunk_tsvector(p_lang text, p_text text)
    RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
$$
    SELECT CASE lower(substring(p_lang from 1 for 2))
        WHEN 'tr' THEN to_tsvector('turkish_unaccent'::regconfig, p_text)
        WHEN 'en' THEN to_tsvector('english_unaccent'::regconfig, p_text)
        ELSE to_tsvector('simple'::regconfig, p_text)
    END
$$;

-- Rebuilding the column rather than adding a second one: two tsvectors would
-- mean two indexes and a query that has to know which to use.
DROP INDEX IF EXISTS chunks_tsv_idx;
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks
    ADD COLUMN tsv tsvector GENERATED ALWAYS AS (chunk_tsvector(lang, text)) STORED;

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);

-- ---------------------------------------------------------------------------
-- Query parsing
-- ---------------------------------------------------------------------------

-- Turns a natural-language question into a *disjunctive* tsquery.
--
-- This exists because both built-in parsers are conjunctive: plainto_tsquery and
-- websearch_to_tsquery join terms with AND, so "Sözleşmeyi feshetmek için kaç gün
-- önceden ihbar gerekir?" only matches a chunk containing every one of those
-- lexemes. Real questions are longer than the passage that answers them, so that
-- is a recall floor near zero — measured at 0.02 before this function existed.
--
-- Disjunctive retrieval plus a ranking function is what BM25 actually does: match
-- anything sharing a term, then let ts_rank_cd sort by how densely the terms
-- cluster. Stopword removal is already handled by the configuration, so the
-- common words never reach the query.
CREATE OR REPLACE FUNCTION query_tsquery(p_config regconfig, p_query text)
    RETURNS tsquery
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
-- Returns NULL for a query with no lexemes (all stopwords, or empty). The
-- caller's `tsv @@ NULL` is NULL, so the arm returns nothing — which is the
-- right answer, and avoids inventing a match-everything query.
$$
    SELECT string_agg(quote_literal(lexeme), ' | ')::tsquery
    FROM unnest(tsvector_to_array(to_tsvector(p_config, p_query))) AS lexeme
$$;

-- ---------------------------------------------------------------------------
-- Retrieval indexes
-- ---------------------------------------------------------------------------

-- Both arms filter on tenant and both temporal intervals before ranking, so the
-- covering order here matches the predicate order in the queries (ADR-0006:
-- filtering happens before ranking, never after).
CREATE INDEX IF NOT EXISTS chunks_retrieval_idx
    ON chunks (tenant_id, valid_from, valid_to, recorded_at, superseded_at);

-- The backfill's work queue: chunks ingested with the null embedding backend.
CREATE INDEX IF NOT EXISTS chunks_unembedded_idx
    ON chunks (tenant_id, chunk_id)
    WHERE embedding IS NULL;

COMMIT;
