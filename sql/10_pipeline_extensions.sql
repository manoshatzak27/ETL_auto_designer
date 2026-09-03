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
