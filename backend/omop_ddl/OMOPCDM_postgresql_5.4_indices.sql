--postgresql CDM Indices for OMOP Common Data Model 5.4
--
-- Like the constraints file, these are NOT applied by `omop-init`. Run them
-- AFTER vocabulary + ETL load:
--
--   sed "s/@cdmDatabaseSchema/cdm/g" \
--     backend/omop_ddl/OMOPCDM_postgresql_5.4_indices.sql \
--     | psql -h localhost -U omop -d omop
--
-- Upstream:
--   https://github.com/OHDSI/CommonDataModel/blob/v5.4/inst/ddl/5.4/postgresql/OMOPCDM_postgresql_5.4_indices.sql
--
-- A small set of practically useful indices for OMOP query workloads:

CREATE INDEX IF NOT EXISTS idx_concept_concept_code   ON @cdmDatabaseSchema.CONCEPT (concept_code);
CREATE INDEX IF NOT EXISTS idx_concept_domain         ON @cdmDatabaseSchema.CONCEPT (domain_id);
CREATE INDEX IF NOT EXISTS idx_concept_vocab          ON @cdmDatabaseSchema.CONCEPT (vocabulary_id);

CREATE INDEX IF NOT EXISTS idx_concept_rel_1          ON @cdmDatabaseSchema.CONCEPT_RELATIONSHIP (concept_id_1);
CREATE INDEX IF NOT EXISTS idx_concept_rel_2          ON @cdmDatabaseSchema.CONCEPT_RELATIONSHIP (concept_id_2);

CREATE INDEX IF NOT EXISTS idx_concept_ancestor_anc   ON @cdmDatabaseSchema.CONCEPT_ANCESTOR (ancestor_concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_ancestor_dec   ON @cdmDatabaseSchema.CONCEPT_ANCESTOR (descendant_concept_id);

CREATE INDEX IF NOT EXISTS idx_person_pid             ON @cdmDatabaseSchema.PERSON (person_id);

CREATE INDEX IF NOT EXISTS idx_visit_pid              ON @cdmDatabaseSchema.VISIT_OCCURRENCE (person_id);
CREATE INDEX IF NOT EXISTS idx_visit_concept          ON @cdmDatabaseSchema.VISIT_OCCURRENCE (visit_concept_id);

CREATE INDEX IF NOT EXISTS idx_obs_period_pid         ON @cdmDatabaseSchema.OBSERVATION_PERIOD (person_id);

CREATE INDEX IF NOT EXISTS idx_death_pid              ON @cdmDatabaseSchema.DEATH (person_id);

CREATE INDEX IF NOT EXISTS idx_measurement_pid        ON @cdmDatabaseSchema.MEASUREMENT (person_id);
CREATE INDEX IF NOT EXISTS idx_measurement_concept    ON @cdmDatabaseSchema.MEASUREMENT (measurement_concept_id);

CREATE INDEX IF NOT EXISTS idx_observation_pid        ON @cdmDatabaseSchema.OBSERVATION (person_id);
CREATE INDEX IF NOT EXISTS idx_observation_concept    ON @cdmDatabaseSchema.OBSERVATION (observation_concept_id);
