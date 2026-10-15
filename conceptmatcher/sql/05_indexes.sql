-- 05_indexes.sql
--
-- General-purpose lookup indexes only (code/vocab/domain/class lookups and
-- the FK join columns). Deliberately does NOT include full-text search,
-- trigram, or embedding indexes — those are specific to the later matching
-- pipeline and belong with that implementation, not with data provisioning.

\timing on

CREATE INDEX IF NOT EXISTS idx_concept_code  ON vocab.concept (concept_code);
CREATE INDEX IF NOT EXISTS idx_concept_domain ON vocab.concept (domain_id);
CREATE INDEX IF NOT EXISTS idx_concept_vocab ON vocab.concept (vocabulary_id);
CREATE INDEX IF NOT EXISTS idx_concept_class ON vocab.concept (concept_class_id);

CREATE INDEX IF NOT EXISTS idx_concept_rel_1    ON vocab.concept_relationship (concept_id_1);
CREATE INDEX IF NOT EXISTS idx_concept_rel_2    ON vocab.concept_relationship (concept_id_2);
CREATE INDEX IF NOT EXISTS idx_concept_rel_type ON vocab.concept_relationship (relationship_id);

CREATE INDEX IF NOT EXISTS idx_concept_syn_concept ON vocab.concept_synonym (concept_id);

CREATE INDEX IF NOT EXISTS idx_concept_anc_ancestor   ON vocab.concept_ancestor (ancestor_concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_anc_descendant ON vocab.concept_ancestor (descendant_concept_id);

CREATE INDEX IF NOT EXISTS idx_drug_strength_drug       ON vocab.drug_strength (drug_concept_id);
CREATE INDEX IF NOT EXISTS idx_drug_strength_ingredient ON vocab.drug_strength (ingredient_concept_id);
