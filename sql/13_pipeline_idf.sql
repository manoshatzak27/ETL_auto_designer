-- 13_pipeline_idf.sql
--
-- Populates pipeline.token_idf (Stage 3.2 weighted Jaccard) by counting, for
-- every lexeme in the corpus, how many concepts contain it.
--
-- Deliberately written as an unnest + GROUP BY rather than ts_stat(): ts_stat
-- builds its whole result in a non-spillable in-memory hash, and this corpus
-- has millions of distinct lexemes (drug names alone contribute a long tail of
-- one-off strength/brand tokens). The aggregate below can spill to disk.
--
-- Tokens seen in exactly one concept are dropped: they are ~60% of the rows
-- and carry no information the Python side cannot reconstruct, which treats a
-- missing token as doc_freq = 1 (maximum rarity).

\timing on

-- Session-scoped (psql autocommits each statement, so SET LOCAL would not
-- outlive the statement it is issued in).
SET work_mem = '512MB';

TRUNCATE pipeline.token_idf;

INSERT INTO pipeline.token_idf (token, doc_freq)
SELECT u.lexeme, count(*)
FROM pipeline.concept_search cs,
     LATERAL unnest(cs.search_vector) AS u
GROUP BY u.lexeme
HAVING count(*) > 1;

TRUNCATE pipeline.corpus_stats;

INSERT INTO pipeline.corpus_stats (stat_key, stat_value)
SELECT 'total_concepts', count(*) FROM pipeline.concept_search
UNION ALL
SELECT 'idf_tokens', count(*) FROM pipeline.token_idf
UNION ALL
SELECT 'max_doc_freq', COALESCE(max(doc_freq), 1) FROM pipeline.token_idf;

ANALYZE pipeline.token_idf;

-- Stage 8.3: stamp which vocabulary release these derived structures came
-- from, so a later refresh can tell whether they are stale.
INSERT INTO pipeline.vocabulary_version (vocabulary_version, concept_count, note)
SELECT
    COALESCE(
        (SELECT vocabulary_version FROM vocab.vocabulary
         WHERE vocabulary_id = 'None' AND vocabulary_version IS NOT NULL),
        (SELECT max(vocabulary_version) FROM vocab.vocabulary
         WHERE vocabulary_version IS NOT NULL),
        'unknown'),
    (SELECT count(*) FROM vocab.concept),
    'concept_search + token_idf rebuilt';

\echo '--- pipeline corpus stats ---'
SELECT stat_key, stat_value FROM pipeline.corpus_stats ORDER BY stat_key;

\echo '--- most common tokens (lowest IDF weight) ---'
SELECT token, doc_freq FROM pipeline.token_idf ORDER BY doc_freq DESC LIMIT 15;

\echo '--- vocabulary version stamp ---'
SELECT version_id, vocabulary_version, concept_count, refreshed_at
FROM pipeline.vocabulary_version ORDER BY version_id DESC LIMIT 3;
