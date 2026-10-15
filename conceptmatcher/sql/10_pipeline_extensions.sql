-- 10_pipeline_extensions.sql
--
-- Extensions required by the matching pipeline. All ship with the stock
-- postgres:16 image, so no custom image is needed.
--
--   pg_trgm       -> Stage 4 trigram similarity + GIN trigram indexes
--   fuzzystrmatch -> Stage 4 levenshtein() for short tokens/abbreviations
--   unaccent      -> Stage 0/1 normalization of accented concept names
--   btree_gin     -> Stage 2 composite (domain_id, search_vector) GIN index
--
-- Everything here is pinned to the `public` schema. 06_cleanup.sql set the
-- database-level search_path to `vocab, public`, so a bare CREATE EXTENSION
-- would install into `vocab` and the `public.unaccent` text-search dictionary
-- that immutable_unaccent() needs would not exist.

CREATE EXTENSION IF NOT EXISTS pg_trgm       WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS unaccent      WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS btree_gin     WITH SCHEMA public;

-- Relocate any that a previous run installed into the wrong schema. Cheap
-- no-op once they are already in public.
DO $$
DECLARE
    ext text;
BEGIN
    FOREACH ext IN ARRAY ARRAY['pg_trgm', 'fuzzystrmatch', 'unaccent', 'btree_gin'] LOOP
        IF EXISTS (
            SELECT 1 FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = ext AND n.nspname <> 'public'
        ) THEN
            EXECUTE format('ALTER EXTENSION %I SET SCHEMA public', ext);
            RAISE NOTICE 'relocated extension % into public', ext;
        END IF;
    END LOOP;
END $$;

-- unaccent() is STABLE by default (it depends on a dictionary lookup), which
-- blocks its use in index expressions. Naming the dictionary explicitly makes
-- the call deterministic, so this wrapper can honestly be marked IMMUTABLE.
-- This is the standard workaround; it is only unsafe if the unaccent
-- dictionary files are edited underneath an existing index, which we never do.
CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- Canonical normalization used by Stage 1 exact matching on both sides of the
-- comparison: lowercase, strip accents, collapse punctuation to single spaces.
-- Kept in SQL (rather than only in Python) so the index and the query agree.
-- Mirrored by normalize_for_exact_match() in pipeline/text_utils.py.
CREATE OR REPLACE FUNCTION public.pipeline_normalize(text)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT btrim(regexp_replace(
               lower(public.immutable_unaccent($1)),
               '[^a-z0-9]+', ' ', 'g'))
$$;


-- Is this "synonym" actually LOINC's search-keyword dump rather than a name?
--
-- 98,462 LOINC synonyms (41% of LOINC's) are a semicolon-delimited bag of
-- index fragments rather than an alternative name for the concept:
--
--   AOEObservation; Coll; Collect; Collected; Drugs; Nominal; Point in time;
--   Random; Sample timing related to dose; Spec; Typ
--
-- Treating that as searchable text lets a concept match any query containing
-- any of those words. It put "Sample collection timing related to drug dose"
-- second for the column "Hair sample has been collected": Stage 4 found
-- literal 'sample' and 'collected' in the dump and scored it 73.3.
--
-- Two guards keep real synonyms:
--   * split on ';' followed by whitespace, never a bare ';' — genomic HGVS and
--     cytogenetic names ("t(11;22)(q24;q12.2)", "c.[541_543del;545T=]") use a
--     bare semicolon as syntax, and splitting on it would gut OMOP Genomic.
--   * require the segments to be *short*. HCPCS writes long prose clauses
--     separated by "; "; a keyword dump averages one or two words per segment.
CREATE OR REPLACE FUNCTION public.pipeline_is_keyword_dump(text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT cardinality(segs) >= 5
       AND (SELECT avg(cardinality(regexp_split_to_array(btrim(x), '\s+')))
              FROM unnest(segs) AS x) <= 3
    FROM (SELECT regexp_split_to_array($1, ';\s') AS segs) t
$$;
