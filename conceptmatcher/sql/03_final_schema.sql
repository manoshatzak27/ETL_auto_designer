-- 03_final_schema.sql
--
-- Final vocabulary tables (schema `vocab`), modeled on the official OHDSI
-- OMOP CDM v5.4 vocabulary DDL, with two deliberate deviations backed by
-- what's actually in this Athena bundle:
--   * concept_name / concept_synonym_name are TEXT, not varchar(255) /
--     varchar(1000) — real data already exceeds the spec (max 285 / 1162
--     chars observed here), so a fixed-width column would risk load
--     failures on a future vocabulary refresh.
--   * concept_relationship / concept_synonym / concept_ancestor /
--     drug_strength get no primary key, matching the official CDM DDL
--     (only supplementary indexes — see 05_indexes.sql).
--
-- Core guarantee for the whole database: vocab.concept holds ONLY standard,
-- valid concepts (standard_concept = 'S' AND invalid_reason IS NULL),
-- enforced both by how 04_load_filtered.sql populates it and by CHECK
-- constraints here. Every dependent table is loaded (in 04) so that every
-- concept_id it references exists in this filtered vocab.concept — so
-- nothing in this database points at a non-standard or invalid concept.
--
-- vocabulary / domain / concept_class / relationship are small lookup
-- tables and are loaded in full (unfiltered): their own *_concept_id
-- columns point at OMOP metadata concepts (domain "Metadata", standard_concept
-- IS NULL) which are intentionally non-standard classification concepts —
-- so no FK is declared from those columns to vocab.concept.

DROP TABLE IF EXISTS vocab.drug_strength;
DROP TABLE IF EXISTS vocab.concept_ancestor;
DROP TABLE IF EXISTS vocab.concept_synonym;
DROP TABLE IF EXISTS vocab.concept_relationship;
DROP TABLE IF EXISTS vocab.concept;
DROP TABLE IF EXISTS vocab.relationship;
DROP TABLE IF EXISTS vocab.concept_class;
DROP TABLE IF EXISTS vocab.domain;
DROP TABLE IF EXISTS vocab.vocabulary;

-- ── Lookup tables (unfiltered) ─────────────────────────────────────────

CREATE TABLE vocab.vocabulary (
    vocabulary_id           varchar(20) NOT NULL,
    vocabulary_name         text        NOT NULL,
    vocabulary_reference    text        NULL,
    vocabulary_version      text        NULL,
    vocabulary_concept_id   integer     NOT NULL,
    CONSTRAINT xpk_vocabulary PRIMARY KEY (vocabulary_id)
);

CREATE TABLE vocab.domain (
    domain_id           varchar(20) NOT NULL,
    domain_name         text        NOT NULL,
    domain_concept_id   integer     NOT NULL,
    CONSTRAINT xpk_domain PRIMARY KEY (domain_id)
);

CREATE TABLE vocab.concept_class (
    concept_class_id           varchar(20) NOT NULL,
    concept_class_name         text        NOT NULL,
    concept_class_concept_id   integer     NOT NULL,
    CONSTRAINT xpk_concept_class PRIMARY KEY (concept_class_id)
);

CREATE TABLE vocab.relationship (
    relationship_id            varchar(20) NOT NULL,
    relationship_name          text        NOT NULL,
    is_hierarchical            varchar(1)  NOT NULL,
    defines_ancestry           varchar(1)  NOT NULL,
    reverse_relationship_id    varchar(20) NOT NULL,
    relationship_concept_id    integer     NOT NULL,
    CONSTRAINT xpk_relationship PRIMARY KEY (relationship_id)
);

-- ── Core filtered concept table ────────────────────────────────────────

CREATE TABLE vocab.concept (
    concept_id          integer     NOT NULL,
    concept_name        text        NOT NULL,
    domain_id           varchar(20) NOT NULL,
    vocabulary_id       varchar(20) NOT NULL,
    concept_class_id    varchar(20) NOT NULL,
    standard_concept    varchar(1)  NOT NULL,
    concept_code        varchar(50) NOT NULL,
    valid_start_date    date        NOT NULL,
    valid_end_date      date        NOT NULL,
    invalid_reason      varchar(1)  NULL,
    CONSTRAINT xpk_concept PRIMARY KEY (concept_id),
    CONSTRAINT ck_concept_is_standard CHECK (standard_concept = 'S'),
    CONSTRAINT ck_concept_is_valid CHECK (invalid_reason IS NULL),
    CONSTRAINT fk_concept_vocabulary FOREIGN KEY (vocabulary_id) REFERENCES vocab.vocabulary (vocabulary_id),
    CONSTRAINT fk_concept_domain FOREIGN KEY (domain_id) REFERENCES vocab.domain (domain_id),
    CONSTRAINT fk_concept_class FOREIGN KEY (concept_class_id) REFERENCES vocab.concept_class (concept_class_id)
);

-- ── Dependent tables (filtered so every concept_id reference resolves) ──

CREATE TABLE vocab.concept_relationship (
    concept_id_1         integer     NOT NULL,
    concept_id_2         integer     NOT NULL,
    relationship_id      varchar(20) NOT NULL,
    valid_start_date     date        NOT NULL,
    valid_end_date       date        NOT NULL,
    invalid_reason       varchar(1)  NULL,
    CONSTRAINT fk_concept_rel_1    FOREIGN KEY (concept_id_1) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_concept_rel_2    FOREIGN KEY (concept_id_2) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_concept_rel_type FOREIGN KEY (relationship_id) REFERENCES vocab.relationship (relationship_id)
);

CREATE TABLE vocab.concept_synonym (
    concept_id              integer NOT NULL,
    concept_synonym_name    text    NOT NULL,
    language_concept_id     integer NOT NULL,
    CONSTRAINT fk_concept_syn_concept  FOREIGN KEY (concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_concept_syn_language FOREIGN KEY (language_concept_id) REFERENCES vocab.concept (concept_id)
);

CREATE TABLE vocab.concept_ancestor (
    ancestor_concept_id          integer NOT NULL,
    descendant_concept_id        integer NOT NULL,
    min_levels_of_separation     integer NOT NULL,
    max_levels_of_separation     integer NOT NULL,
    CONSTRAINT fk_concept_anc_ancestor   FOREIGN KEY (ancestor_concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_concept_anc_descendant FOREIGN KEY (descendant_concept_id) REFERENCES vocab.concept (concept_id)
);

CREATE TABLE vocab.drug_strength (
    drug_concept_id                integer  NOT NULL,
    ingredient_concept_id          integer  NOT NULL,
    amount_value                   numeric  NULL,
    amount_unit_concept_id         integer  NULL,
    numerator_value                numeric  NULL,
    numerator_unit_concept_id      integer  NULL,
    denominator_value              numeric  NULL,
    denominator_unit_concept_id    integer  NULL,
    box_size                       integer  NULL,
    valid_start_date               date     NOT NULL,
    valid_end_date                 date     NOT NULL,
    invalid_reason                 varchar(1) NULL,
    CONSTRAINT fk_drug_strength_drug       FOREIGN KEY (drug_concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_drug_strength_ingredient FOREIGN KEY (ingredient_concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_drug_strength_amount_u   FOREIGN KEY (amount_unit_concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_drug_strength_numerator_u   FOREIGN KEY (numerator_unit_concept_id) REFERENCES vocab.concept (concept_id),
    CONSTRAINT fk_drug_strength_denominator_u FOREIGN KEY (denominator_unit_concept_id) REFERENCES vocab.concept (concept_id)
);
