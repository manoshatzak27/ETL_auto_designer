-- 06_cleanup.sql
--
-- Drops the raw staging schema (it must never be part of the final
-- database — it's the only place unfiltered/non-standard/invalid concepts
-- ever existed), reclaims space, refreshes planner statistics, and points
-- new connections at the `vocab` schema by default.

DROP SCHEMA IF EXISTS staging CASCADE;

VACUUM ANALYZE vocab.concept;
VACUUM ANALYZE vocab.vocabulary;
VACUUM ANALYZE vocab.domain;
VACUUM ANALYZE vocab.concept_class;
VACUUM ANALYZE vocab.relationship;
VACUUM ANALYZE vocab.concept_relationship;
VACUUM ANALYZE vocab.concept_synonym;
VACUUM ANALYZE vocab.concept_ancestor;
VACUUM ANALYZE vocab.drug_strength;

DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET search_path = vocab, public', current_database());
END $$;

\echo '--- final row counts (standard + valid only) ---'
SELECT 'concept' t, count(*) FROM vocab.concept
UNION ALL SELECT 'vocabulary', count(*) FROM vocab.vocabulary
UNION ALL SELECT 'domain', count(*) FROM vocab.domain
UNION ALL SELECT 'concept_class', count(*) FROM vocab.concept_class
UNION ALL SELECT 'relationship', count(*) FROM vocab.relationship
UNION ALL SELECT 'concept_relationship', count(*) FROM vocab.concept_relationship
UNION ALL SELECT 'concept_synonym', count(*) FROM vocab.concept_synonym
UNION ALL SELECT 'concept_ancestor', count(*) FROM vocab.concept_ancestor
UNION ALL SELECT 'drug_strength', count(*) FROM vocab.drug_strength;

\echo '--- sanity check: any non-standard or invalid concept present? (must be 0) ---'
SELECT count(*) AS bad_rows FROM vocab.concept WHERE standard_concept <> 'S' OR invalid_reason IS NOT NULL;

\echo '--- remaining schemas ---'
SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema';
