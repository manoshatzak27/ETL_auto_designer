-- 04_load_filtered.sql
--
-- Populates the final `vocab` schema from `staging`, applying the
-- standard+valid filter. Load order matters: lookup tables first (unfiltered,
-- needed for concept's FKs), then concept itself (the actual filter), then
-- every dependent table joined back against the now-filtered vocab.concept
-- so only rows whose concept_id references all survived the filter are kept.
--
-- Staging columns were loaded with COPY's NULL '' option, so empty source
-- fields are already real SQL NULLs here — no NULLIF needed.

\timing on

-- ── Lookup tables: load in full ──────────────────────────────────────────

INSERT INTO vocab.vocabulary (vocabulary_id, vocabulary_name, vocabulary_reference, vocabulary_version, vocabulary_concept_id)
SELECT vocabulary_id, vocabulary_name, vocabulary_reference, vocabulary_version, vocabulary_concept_id::integer
FROM staging.vocabulary;

INSERT INTO vocab.domain (domain_id, domain_name, domain_concept_id)
SELECT domain_id, domain_name, domain_concept_id::integer
FROM staging.domain;

INSERT INTO vocab.concept_class (concept_class_id, concept_class_name, concept_class_concept_id)
SELECT concept_class_id, concept_class_name, concept_class_concept_id::integer
FROM staging.concept_class;

INSERT INTO vocab.relationship (relationship_id, relationship_name, is_hierarchical, defines_ancestry, reverse_relationship_id, relationship_concept_id)
SELECT relationship_id, relationship_name, is_hierarchical, defines_ancestry, reverse_relationship_id, relationship_concept_id::integer
FROM staging.relationship;

-- ── Core filter: standard + valid concepts only ──────────────────────────

INSERT INTO vocab.concept (concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, standard_concept, concept_code, valid_start_date, valid_end_date, invalid_reason)
SELECT
    concept_id::integer,
    concept_name,
    domain_id,
    vocabulary_id,
    concept_class_id,
    standard_concept,
    concept_code,
    to_date(valid_start_date, 'YYYYMMDD'),
    to_date(valid_end_date, 'YYYYMMDD'),
    invalid_reason
FROM staging.concept
WHERE standard_concept = 'S'
  AND invalid_reason IS NULL;

-- ── Dependent tables: keep a row only if every concept_id it references
--    survived the filter above ───────────────────────────────────────────

INSERT INTO vocab.concept_relationship (concept_id_1, concept_id_2, relationship_id, valid_start_date, valid_end_date, invalid_reason)
SELECT s.concept_id_1::integer, s.concept_id_2::integer, s.relationship_id,
       to_date(s.valid_start_date, 'YYYYMMDD'), to_date(s.valid_end_date, 'YYYYMMDD'), s.invalid_reason
FROM staging.concept_relationship s
JOIN vocab.concept c1 ON c1.concept_id = s.concept_id_1::integer
JOIN vocab.concept c2 ON c2.concept_id = s.concept_id_2::integer;

INSERT INTO vocab.concept_synonym (concept_id, concept_synonym_name, language_concept_id)
SELECT s.concept_id::integer, s.concept_synonym_name, s.language_concept_id::integer
FROM staging.concept_synonym s
JOIN vocab.concept c  ON c.concept_id  = s.concept_id::integer
JOIN vocab.concept cl ON cl.concept_id = s.language_concept_id::integer;

INSERT INTO vocab.concept_ancestor (ancestor_concept_id, descendant_concept_id, min_levels_of_separation, max_levels_of_separation)
SELECT s.ancestor_concept_id::integer, s.descendant_concept_id::integer,
       s.min_levels_of_separation::integer, s.max_levels_of_separation::integer
FROM staging.concept_ancestor s
JOIN vocab.concept ca ON ca.concept_id = s.ancestor_concept_id::integer
JOIN vocab.concept cd ON cd.concept_id = s.descendant_concept_id::integer;

INSERT INTO vocab.drug_strength (drug_concept_id, ingredient_concept_id, amount_value, amount_unit_concept_id,
                                  numerator_value, numerator_unit_concept_id, denominator_value, denominator_unit_concept_id,
                                  box_size, valid_start_date, valid_end_date, invalid_reason)
SELECT s.drug_concept_id::integer, s.ingredient_concept_id::integer, s.amount_value::numeric,
       s.amount_unit_concept_id::integer, s.numerator_value::numeric, s.numerator_unit_concept_id::integer,
       s.denominator_value::numeric, s.denominator_unit_concept_id::integer, s.box_size::integer,
       to_date(s.valid_start_date, 'YYYYMMDD'), to_date(s.valid_end_date, 'YYYYMMDD'), s.invalid_reason
FROM staging.drug_strength s
JOIN vocab.concept cd ON cd.concept_id = s.drug_concept_id::integer
JOIN vocab.concept ci ON ci.concept_id = s.ingredient_concept_id::integer
LEFT JOIN vocab.concept cau ON cau.concept_id = s.amount_unit_concept_id::integer
LEFT JOIN vocab.concept cnu ON cnu.concept_id = s.numerator_unit_concept_id::integer
LEFT JOIN vocab.concept cdu ON cdu.concept_id = s.denominator_unit_concept_id::integer
WHERE (s.amount_unit_concept_id IS NULL OR cau.concept_id IS NOT NULL)
  AND (s.numerator_unit_concept_id IS NULL OR cnu.concept_id IS NOT NULL)
  AND (s.denominator_unit_concept_id IS NULL OR cdu.concept_id IS NOT NULL);

\echo '--- filtered (final) row counts ---'
SELECT 'concept' t, count(*) FROM vocab.concept
UNION ALL SELECT 'vocabulary', count(*) FROM vocab.vocabulary
UNION ALL SELECT 'domain', count(*) FROM vocab.domain
UNION ALL SELECT 'concept_class', count(*) FROM vocab.concept_class
UNION ALL SELECT 'relationship', count(*) FROM vocab.relationship
UNION ALL SELECT 'concept_relationship', count(*) FROM vocab.concept_relationship
UNION ALL SELECT 'concept_synonym', count(*) FROM vocab.concept_synonym
UNION ALL SELECT 'concept_ancestor', count(*) FROM vocab.concept_ancestor
UNION ALL SELECT 'drug_strength', count(*) FROM vocab.drug_strength;
