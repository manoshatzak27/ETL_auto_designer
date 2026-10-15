-- 14_pipeline_spelling.sql
--
-- Populates pipeline.word_freq, the dictionary for Stage 0 spelling correction
-- (pipeline/spelling.py): every word of every concept name and English
-- synonym in pipeline.concept_search, with the number of times it occurs.
--
-- Built from the vocabulary itself rather than shipped as a word list because a
-- general English dictionary knows neither 'certolizumab' nor 'bilirubin', and
-- a spell checker that does not know a word "corrects" it into one it does.
-- search_text already excludes non-English synonyms and LOINC keyword dumps
-- (11_pipeline_schema.sql), so neither pollutes the dictionary.
--
-- Surface words, not lexemes: a correction is substituted back into the input
-- text, so it has to be a word ('bilirubin'), not a stem ('bilirubin' happens
-- to survive; 'phosphatase' would come back as 'phosphatas').
--
-- Letters only and at least 3 long: digits and strengths are not misspellings,
-- and the Python side never corrects anything shorter than 5 letters anyway.
-- Hapaxes are kept, unlike token_idf: a word the vocabulary contains once is
-- still a word, and dropping it would make the checker "correct" it.

\timing on

SET work_mem = '512MB';

CREATE TABLE IF NOT EXISTS pipeline.word_freq (
    word  text   NOT NULL,
    freq  bigint NOT NULL,
    CONSTRAINT pk_word_freq PRIMARY KEY (word)
);

TRUNCATE pipeline.word_freq;

INSERT INTO pipeline.word_freq (word, freq)
SELECT w.word, count(*)
FROM pipeline.concept_search cs,
     LATERAL regexp_split_to_table(lower(cs.search_text), '[^a-z]+') AS w(word)
WHERE length(w.word) >= 3
GROUP BY w.word;

ANALYZE pipeline.word_freq;

\echo '--- spelling dictionary ---'
SELECT count(*) AS words, sum(freq) AS occurrences FROM pipeline.word_freq;
