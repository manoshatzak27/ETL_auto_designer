-- 11_pipeline_schema.sql
--
-- Everything the matching pipeline needs on top of the vocabulary — either the
-- standard-only one ./load_vocab.sh builds or the full Athena export:
--   * pipeline.concept_search   - materialized search view (Stage 2)
--   * pipeline.token_idf        - corpus token frequencies (Stage 3)
--   * pipeline.concept_embedding- embedding cache (Stage 5 / 8.2)
--   * logging + feedback tables (Stage 7)
--   * reporting views           (Stage 9)
--
-- Safe to re-run. Data tables (logs, manual mappings, embeddings) are created
-- with IF NOT EXISTS so re-running never destroys captured history; the
-- derived structures (matview, IDF) are rebuilt.

CREATE SCHEMA IF NOT EXISTS pipeline;

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 2: materialized search view
--
-- One row per standard+valid concept, carrying the concept name and its
-- ENGLISH synonyms pre-joined into a single weighted tsvector:
--   weight A = concept name, weight B = synonyms.
-- ts_rank_cd() then naturally prefers a hit in the name over a hit in a
-- synonym without any extra bookkeeping.
--
-- Non-English synonyms (Spanish ~756k, Chinese ~91k, ...) are excluded: they
-- share the vocabulary's Latin alphabet, so they would otherwise pollute both
-- the English FTS dictionary and trigram similarity with false neighbours.
--
-- ALL domains are included, not just the five "eligible" ones. Domain
-- restriction is a query-time predicate (Stage 2.1) so that the Stage 6.4
-- fallback ("retry without domain filters") can genuinely reach everything.
-- ─────────────────────────────────────────────────────────────────────────
DROP MATERIALIZED VIEW IF EXISTS pipeline.concept_search CASCADE;

CREATE MATERIALIZED VIEW pipeline.concept_search AS
SELECT
    c.concept_id,
    c.concept_name,
    c.domain_id,
    c.vocabulary_id,
    c.concept_class_id,
    c.concept_code,
    public.pipeline_normalize(c.concept_name)               AS concept_name_norm,
    -- Step 2.3b: the concept NAME's own lexemes, synonyms excluded. Stage 2
    -- uses these to measure how much of the name the query accounts for —
    -- precision, where `coverage` is recall. Precomputed here because the
    -- alternative is to_tsvector() per row over every FTS match.
    tsvector_to_array(to_tsvector('english', c.concept_name)) AS name_lexemes,
    COALESCE(s.synonyms, ARRAY[]::text[])                   AS synonyms,
    c.concept_name
        || COALESCE(' ' || array_to_string(s.synonyms, ' '), '')  AS search_text,
    setweight(to_tsvector('english', c.concept_name), 'A')
        || setweight(to_tsvector('english',
               COALESCE(array_to_string(s.synonyms, ' '), '')), 'B') AS search_vector
FROM vocab.concept c
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT cs.concept_synonym_name) AS synonyms
    FROM vocab.concept_synonym cs
    WHERE cs.concept_id = c.concept_id
      AND cs.language_concept_id = 4180186          -- English language
      AND cs.concept_synonym_name <> c.concept_name
      -- LOINC's search-keyword dumps are not names; see
      -- public.pipeline_is_keyword_dump in 10_pipeline_extensions.sql.
      AND NOT public.pipeline_is_keyword_dump(cs.concept_synonym_name)
) s ON TRUE
-- A no-op against the standard-only vocabulary; required against the full one.
WHERE c.standard_concept = 'S'
  AND c.invalid_reason IS NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 3: IDF weights
--
-- doc_freq = number of concepts whose search_vector contains the lexeme.
-- Tokens appearing in exactly one concept are omitted to keep the table
-- small; the Python side treats any token missing here as maximally rare
-- (doc_freq = 1), which is exactly what a hapax deserves.
-- ─────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS pipeline.token_idf;
CREATE TABLE pipeline.token_idf (
    token     text    NOT NULL,
    doc_freq  bigint  NOT NULL,
    CONSTRAINT pk_token_idf PRIMARY KEY (token)
);

DROP TABLE IF EXISTS pipeline.corpus_stats;
CREATE TABLE pipeline.corpus_stats (
    stat_key   text NOT NULL,
    stat_value numeric NOT NULL,
    CONSTRAINT pk_corpus_stats PRIMARY KEY (stat_key)
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 5 / 8.2: embedding cache
--
-- Vectors are stored as raw float32 bytea (4 bytes/dim) rather than real[]:
-- a 384-dim vector is 1.5kB instead of ~9kB of array overhead, and numpy can
-- frombuffer() it with no parsing.
--
-- name_hash lets Stage 8.3 invalidate only the concepts whose text actually
-- changed when the vocabulary is refreshed.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.concept_embedding (
    concept_id  integer      NOT NULL,
    model       text         NOT NULL,
    dim         integer      NOT NULL,
    name_hash   text         NOT NULL,
    embedding   bytea        NOT NULL,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT pk_concept_embedding PRIMARY KEY (concept_id, model)
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 8.3: which vocabulary release the derived structures were built from
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.vocabulary_version (
    version_id           bigserial    PRIMARY KEY,
    vocabulary_version   text         NOT NULL,
    concept_count        bigint       NOT NULL,
    refreshed_at         timestamptz  NOT NULL DEFAULT now(),
    note                 text
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 7.4: comprehensive logging
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.mapping_run (
    run_id              bigserial    PRIMARY KEY,
    started_at          timestamptz  NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    source_system       text,
    vocabulary_version  text,
    config              jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_by          text,
    note                text
);

CREATE TABLE IF NOT EXISTS pipeline.mapping_log (
    mapping_id              bigserial    PRIMARY KEY,
    run_id                  bigint       REFERENCES pipeline.mapping_run (run_id) ON DELETE CASCADE,

    -- what came in
    column_name             text         NOT NULL,
    column_description      text,
    source_table            text,

    -- what Stage 0 made of it
    search_string           text,
    tokens                  text[],
    protected_phrases       text[],
    units                   text[],
    unit_types              text[],
    domain_hint             text,
    numeric_flag            boolean      NOT NULL DEFAULT false,
    temporal_flag           boolean      NOT NULL DEFAULT false,
    extracted_codes         jsonb        NOT NULL DEFAULT '[]'::jsonb,
    abbreviations_expanded  jsonb        NOT NULL DEFAULT '[]'::jsonb,

    -- what came out
    selected_concept_id     integer,
    selected_concept_name   text,
    selected_domain_id      text,
    selected_vocabulary_id  text,
    selected_concept_code   text,
    confidence              numeric(6,2),
    status                  text         NOT NULL,
    decided_at_stage        text,
    decision_reason         text,
    ambiguous               boolean      NOT NULL DEFAULT false,
    validation_flags        text[]       NOT NULL DEFAULT ARRAY[]::text[],
    replaced_from_concept_id integer,

    elapsed_ms              numeric(12,2),
    created_at              timestamptz  NOT NULL DEFAULT now(),
    created_by              text,

    CONSTRAINT ck_mapping_log_status
        CHECK (status IN ('auto_accept', 'review', 'unmapped', 'manual', 'error'))
);

CREATE TABLE IF NOT EXISTS pipeline.mapping_candidate (
    candidate_id     bigserial   PRIMARY KEY,
    mapping_id       bigint      NOT NULL REFERENCES pipeline.mapping_log (mapping_id) ON DELETE CASCADE,
    rank             integer     NOT NULL,
    concept_id       integer     NOT NULL,
    concept_name     text,
    domain_id        text,
    vocabulary_id    text,
    fts_score        numeric(6,2),
    jaccard_score    numeric(6,2),
    fuzzy_score      numeric(6,2),
    embedding_score  numeric(6,2),
    composite_score  numeric(6,2),
    boosts           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    selected         boolean     NOT NULL DEFAULT false
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 9.3: per-stage timings
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.stage_timing (
    timing_id      bigserial   PRIMARY KEY,
    run_id         bigint      REFERENCES pipeline.mapping_run (run_id) ON DELETE CASCADE,
    mapping_id     bigint      REFERENCES pipeline.mapping_log (mapping_id) ON DELETE CASCADE,
    stage          text        NOT NULL,
    elapsed_ms     numeric(12,2) NOT NULL,
    candidates_in  integer,
    candidates_out integer
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 7.5: feedback loop
--
-- manual_mapping is consulted by Stage 1 before anything else, so a human
-- correction is never re-derived on the next run.
-- lookup_key is the Stage 0 normalized form of "name description".
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline.manual_mapping (
    manual_id          bigserial   PRIMARY KEY,
    lookup_key         text        NOT NULL,
    column_name        text        NOT NULL,
    column_description text,
    source_system      text,
    concept_id         integer     NOT NULL REFERENCES vocab.concept (concept_id),
    active             boolean     NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         text,
    note               text,
    CONSTRAINT uq_manual_mapping_key UNIQUE (lookup_key, source_system)
);

-- Stage 4.2: abbreviations the fuzzy stage stumbled onto that the Stage 0
-- dictionary did not know about. Reviewed offline, then promoted into
-- pipeline/resources/abbreviations.json.
CREATE TABLE IF NOT EXISTS pipeline.discovered_abbreviation (
    discovery_id  bigserial   PRIMARY KEY,
    abbreviation  text        NOT NULL,
    expansion     text        NOT NULL,
    confidence    numeric(6,2),
    times_seen    integer     NOT NULL DEFAULT 1,
    promoted      boolean     NOT NULL DEFAULT false,
    first_seen    timestamptz NOT NULL DEFAULT now(),
    last_seen     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_discovered_abbrev UNIQUE (abbreviation, expansion)
);

-- Stage 7.5: scoring weights live in the DB so the feedback loop can tune
-- them without a code change. Empty table = use the built-in defaults.
CREATE TABLE IF NOT EXISTS pipeline.scoring_weight (
    weight_key  text         NOT NULL,
    weight      numeric(8,4) NOT NULL,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    updated_by  text,
    note        text,
    CONSTRAINT pk_scoring_weight PRIMARY KEY (weight_key)
);

-- ─────────────────────────────────────────────────────────────────────────
-- Stage 9: reporting views
-- ─────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW pipeline.v_mapping_stats AS
SELECT
    run_id,
    count(*)                                                        AS total_columns,
    count(*) FILTER (WHERE status = 'auto_accept')                  AS auto_accepted,
    count(*) FILTER (WHERE status = 'review')                       AS needs_review,
    count(*) FILTER (WHERE status = 'unmapped')                     AS unmapped,
    count(*) FILTER (WHERE status = 'manual')                       AS from_manual_mapping,
    count(*) FILTER (WHERE ambiguous)                               AS ambiguous,
    round(100.0 * count(*) FILTER (WHERE status = 'auto_accept') / NULLIF(count(*), 0), 1) AS auto_accept_pct,
    round(100.0 * count(*) FILTER (WHERE status = 'review')      / NULLIF(count(*), 0), 1) AS review_pct,
    round(100.0 * count(*) FILTER (WHERE status = 'unmapped')    / NULLIF(count(*), 0), 1) AS unmapped_pct,
    round(avg(confidence), 1)                                       AS avg_confidence,
    round(avg(elapsed_ms), 1)                                       AS avg_elapsed_ms
FROM pipeline.mapping_log
GROUP BY run_id;

CREATE OR REPLACE VIEW pipeline.v_domain_confidence AS
SELECT
    run_id,
    COALESCE(selected_domain_id, '(unmapped)') AS domain_id,
    count(*)                                   AS n,
    round(avg(confidence), 1)                  AS avg_confidence,
    min(confidence)                            AS min_confidence,
    max(confidence)                            AS max_confidence
FROM pipeline.mapping_log
GROUP BY run_id, COALESCE(selected_domain_id, '(unmapped)');

CREATE OR REPLACE VIEW pipeline.v_failure_reasons AS
SELECT
    run_id,
    decision_reason,
    count(*) AS n
FROM pipeline.mapping_log
WHERE status IN ('unmapped', 'review')
GROUP BY run_id, decision_reason;

CREATE OR REPLACE VIEW pipeline.v_stage_performance AS
SELECT
    run_id,
    stage,
    count(*)                    AS invocations,
    round(avg(elapsed_ms), 2)   AS avg_ms,
    round(max(elapsed_ms), 2)   AS max_ms,
    round(sum(elapsed_ms), 2)   AS total_ms,
    round(avg(candidates_out), 1) AS avg_candidates_out
FROM pipeline.stage_timing
GROUP BY run_id, stage;

-- Step 9.2: the tokens that keep showing up in columns we could not map are
-- exactly the ones the abbreviation/phrase dictionaries are missing.
CREATE OR REPLACE VIEW pipeline.v_unmapped_tokens AS
SELECT
    l.run_id,
    t.token,
    count(*) AS n
FROM pipeline.mapping_log l
CROSS JOIN LATERAL unnest(l.tokens) AS t(token)
WHERE l.status = 'unmapped'
GROUP BY l.run_id, t.token;

CREATE OR REPLACE VIEW pipeline.v_ambiguous_mappings AS
SELECT
    l.mapping_id,
    l.run_id,
    l.column_name,
    l.confidence,
    c.rank,
    c.concept_id,
    c.concept_name,
    c.composite_score
FROM pipeline.mapping_log l
JOIN pipeline.mapping_candidate c ON c.mapping_id = l.mapping_id
WHERE l.ambiguous
ORDER BY l.mapping_id, c.rank;
