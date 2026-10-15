-- 12_pipeline_indexes.sql
--
-- Indexes for the matching pipeline. Build order matters for wall-clock time:
-- the two GIN indexes over 3.5M rows are the expensive ones.

\timing on

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY (Stage 8.3 incremental
-- refresh without taking the search view offline).
CREATE UNIQUE INDEX IF NOT EXISTS uq_concept_search_id
    ON pipeline.concept_search (concept_id);

-- ── Stage 2: FTS ────────────────────────────────────────────────────────
-- Multicolumn GIN over (domain_id, search_vector). Postgres can use a
-- multicolumn GIN index with any subset of its columns, so this one index
-- serves both the domain-filtered query (Step 2.1) and the Stage 6.4
-- fallback that drops the domain predicate. btree_gin supplies the operator
-- class for the plain text column.
CREATE INDEX IF NOT EXISTS idx_concept_search_domain_vector
    ON pipeline.concept_search USING gin (domain_id, search_vector);

CREATE INDEX IF NOT EXISTS idx_concept_search_domain
    ON pipeline.concept_search (domain_id);

CREATE INDEX IF NOT EXISTS idx_concept_search_vocab
    ON pipeline.concept_search (vocabulary_id);

-- ── Stage 1: exact name / synonym / code lookup ─────────────────────────
-- Both sides are compared through pipeline_normalize(), so the index has to
-- be on the normalized form or it will not be used.
CREATE INDEX IF NOT EXISTS idx_concept_search_name_norm
    ON pipeline.concept_search (concept_name_norm);

CREATE INDEX IF NOT EXISTS idx_concept_synonym_norm
    ON vocab.concept_synonym (public.pipeline_normalize(concept_synonym_name));

CREATE INDEX IF NOT EXISTS idx_concept_vocab_code
    ON vocab.concept (vocabulary_id, concept_code);

-- Step 1.1 / 6.3: deprecated code -> replacement concept.
CREATE INDEX IF NOT EXISTS idx_concept_rel_maps_to
    ON vocab.concept_relationship (concept_id_1, relationship_id);

-- ── Stage 4: trigram ────────────────────────────────────────────────────
-- The main Stage 4 path only rescores the ~15 candidates handed over by
-- Stage 3 and needs no index. This one exists for the recall path: when FTS
-- returns nothing at all (a typo that shares no lexeme with any concept),
-- the pipeline falls back to a `%` similarity scan, which without a GIN
-- trigram index would be a 3.5M-row sequential scan per column.
CREATE INDEX IF NOT EXISTS idx_concept_search_name_trgm
    ON pipeline.concept_search USING gin (concept_name_norm gin_trgm_ops);

-- Synonym similarity looks synonyms up per candidate concept. 05_indexes.sql
-- creates this under the same name; a vocabulary loaded by something else
-- (the ETL Auto-Designer's full one) may not have it.
CREATE INDEX IF NOT EXISTS idx_concept_syn_concept
    ON vocab.concept_synonym (concept_id);

-- ── Logging / feedback lookups ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mapping_log_run     ON pipeline.mapping_log (run_id);
CREATE INDEX IF NOT EXISTS idx_mapping_log_status  ON pipeline.mapping_log (status);
CREATE INDEX IF NOT EXISTS idx_mapping_log_concept ON pipeline.mapping_log (selected_concept_id);
CREATE INDEX IF NOT EXISTS idx_mapping_cand_map    ON pipeline.mapping_candidate (mapping_id);
CREATE INDEX IF NOT EXISTS idx_stage_timing_run    ON pipeline.stage_timing (run_id, stage);
CREATE INDEX IF NOT EXISTS idx_manual_mapping_key  ON pipeline.manual_mapping (lookup_key) WHERE active;

ANALYZE pipeline.concept_search;
