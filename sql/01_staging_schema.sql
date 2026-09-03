-- 01_staging_schema.sql
--
-- Raw, unconstrained staging tables that mirror the Athena export files
-- column-for-column. Everything is TEXT so COPY never fails on a type or
-- format surprise; casting and validation happen when rows move into the
-- filtered `vocab` schema (see 04_load_filtered.sql). This whole schema is
-- dropped at the end (06_cleanup.sql) — it never remains part of the final
-- database.

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS vocab;

DROP TABLE IF EXISTS staging.concept;
CREATE TABLE staging.concept (
    concept_id          text,
    concept_name        text,
    domain_id           text,
    vocabulary_id       text,
    concept_class_id    text,
    standard_concept    text,
    concept_code        text,
    valid_start_date    text,
    valid_end_date      text,
    invalid_reason      text
);

DROP TABLE IF EXISTS staging.vocabulary;
CREATE TABLE staging.vocabulary (
    vocabulary_id           text,
    vocabulary_name         text,
    vocabulary_reference    text,
    vocabulary_version      text,
    vocabulary_concept_id   text
);

DROP TABLE IF EXISTS staging.domain;
CREATE TABLE staging.domain (
    domain_id           text,
    domain_name         text,
    domain_concept_id   text
);

DROP TABLE IF EXISTS staging.concept_class;
CREATE TABLE staging.concept_class (
    concept_class_id           text,
    concept_class_name         text,
    concept_class_concept_id   text
);

DROP TABLE IF EXISTS staging.relationship;
CREATE TABLE staging.relationship (
    relationship_id            text,
    relationship_name          text,
    is_hierarchical            text,
    defines_ancestry           text,
    reverse_relationship_id    text,
    relationship_concept_id    text
);

DROP TABLE IF EXISTS staging.concept_relationship;
CREATE TABLE staging.concept_relationship (
    concept_id_1         text,
    concept_id_2         text,
    relationship_id      text,
    valid_start_date     text,
    valid_end_date       text,
    invalid_reason       text
);

DROP TABLE IF EXISTS staging.concept_synonym;
CREATE TABLE staging.concept_synonym (
    concept_id              text,
    concept_synonym_name    text,
    language_concept_id     text
);

DROP TABLE IF EXISTS staging.concept_ancestor;
CREATE TABLE staging.concept_ancestor (
    ancestor_concept_id         text,
    descendant_concept_id       text,
    min_levels_of_separation    text,
    max_levels_of_separation    text
);

DROP TABLE IF EXISTS staging.drug_strength;
CREATE TABLE staging.drug_strength (
    drug_concept_id                text,
    ingredient_concept_id          text,
    amount_value                   text,
    amount_unit_concept_id         text,
    numerator_value                text,
    numerator_unit_concept_id      text,
    denominator_value              text,
    denominator_unit_concept_id    text,
    box_size                       text,
    valid_start_date               text,
    valid_end_date                 text,
    invalid_reason                 text
);
