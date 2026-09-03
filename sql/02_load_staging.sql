-- 02_load_staging.sql
--
-- Server-side COPY straight from the read-only mounted Athena export
-- directory (/athena_source, bind-mounted from ./athena_ALL). Server-side
-- COPY reads the file directly from the container's own filesystem, so
-- multi-GB files load without streaming through the client.
--
-- QUOTE E'\b' disables CSV quote handling: Athena's TSVs are unquoted and
-- can contain literal double-quote characters inside text fields (e.g. a
-- device size like 14"). \b (backspace) is used as the quote character
-- because it never appears in the data, which effectively turns quoting
-- off while keeping COPY in CSV parsing mode. NULL '' turns empty fields
-- into real SQL NULLs (used below for invalid_reason, nullable
-- drug_strength columns, etc).

\timing on

COPY staging.concept FROM '/athena_source/CONCEPT.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.vocabulary FROM '/athena_source/VOCABULARY.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.domain FROM '/athena_source/DOMAIN.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.concept_class FROM '/athena_source/CONCEPT_CLASS.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.relationship FROM '/athena_source/RELATIONSHIP.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.concept_relationship FROM '/athena_source/CONCEPT_RELATIONSHIP.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.concept_synonym FROM '/athena_source/CONCEPT_SYNONYM.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.concept_ancestor FROM '/athena_source/CONCEPT_ANCESTOR.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

COPY staging.drug_strength FROM '/athena_source/DRUG_STRENGTH.csv'
    WITH (FORMAT csv, DELIMITER E'\t', NULL '', HEADER, QUOTE E'\b', ENCODING 'UTF8');

-- Note: CONCEPT_CPT4.csv and cpt4.jar are not loaded — that file is not an
-- OMOP table, it's a helper for the Athena CPT4 licensing utility (requires
-- a UMLS API key) that patches CPT4 names directly into CONCEPT.csv. The
-- CONCEPT.csv in this bundle already has no blank CPT4 names, so it's a
-- no-op here; if you re-download a fresh bundle and it does contain blank
-- CPT4 names, run that utility against athena_ALL/CONCEPT.csv before
-- re-running this load.

\echo '--- staging row counts ---'
SELECT 'concept' t, count(*) FROM staging.concept
UNION ALL SELECT 'vocabulary', count(*) FROM staging.vocabulary
UNION ALL SELECT 'domain', count(*) FROM staging.domain
UNION ALL SELECT 'concept_class', count(*) FROM staging.concept_class
UNION ALL SELECT 'relationship', count(*) FROM staging.relationship
UNION ALL SELECT 'concept_relationship', count(*) FROM staging.concept_relationship
UNION ALL SELECT 'concept_synonym', count(*) FROM staging.concept_synonym
UNION ALL SELECT 'concept_ancestor', count(*) FROM staging.concept_ancestor
UNION ALL SELECT 'drug_strength', count(*) FROM staging.drug_strength;
