"""Multi-stage OMOP concept matching pipeline.

Maps source column names/descriptions onto standard, valid OMOP concepts from
the filtered Athena vocabulary in this project's Postgres.

Stage layout (one module per stage of the specification):

    stage0_preprocess  normalization, abbreviations, units, phrases, lemmas
    stage1_exact       code / exact name / exact synonym lookup (100 or bust)
    stage2_fts         domain-filtered full-text search with phrase boosting
    stage3_jaccard     IDF-weighted token-set overlap
    stage4_fuzzy       trigram + Levenshtein similarity
    stage5_embeddings  sentence-embedding cosine similarity
    stage6_composite   weighted composite score + decision logic
    stage7_validate    validation, logging, feedback loop
    stage8_batch       parallel batch execution + caching
    stage9_report      statistics, quality and performance reporting

The usual entry points are `pipeline.runner.ConceptMatcher` for one column at a
time and `pipeline.stage8_batch.BatchMatcher` for many, or the `pipeline.cli`
command line wrapping both.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
