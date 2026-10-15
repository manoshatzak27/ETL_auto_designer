"""Tests for the matching pipeline.

Run with:      .venv/bin/python -m unittest discover -s tests -v

Uses stdlib unittest rather than pytest so the test suite adds no dependency
beyond what the pipeline already needs.

The Stage 0-6 tests are pure and run anywhere. The integration tests need the
loaded database and skip themselves cleanly when it is not reachable, so the
suite is still useful on a machine with no Postgres.
"""

from __future__ import annotations

import unittest

from pipeline import text_utils
from pipeline.config import PipelineConfig, domain_relation
from pipeline.models import Candidate, ContextBundle
from pipeline.scoring import make_adjuster
from pipeline.stage0_preprocess import Preprocessor
from pipeline.stage6_composite import CompositeScorer, merge_candidates


def make_config() -> PipelineConfig:
    """Shipped defaults, with only the database connection read from the
    environment — so the integration tests find Postgres whether they run on
    the host (`localhost:5433`) or inside the pipeline container
    (`postgres:5432`).

    Everything else stays at the built-in defaults on purpose: the unit tests
    assert on shipped thresholds and weights, and a local `pipeline.json` must
    not be able to quietly change what they are testing.
    """
    config = PipelineConfig()
    environment = PipelineConfig.load()
    for name in ("host", "port", "dbname", "user", "password"):
        setattr(config, name, getattr(environment, name))
    config.log_to_db = False
    return config


# ── Stage 0 ─────────────────────────────────────────────────────────────


class TestPreprocessing(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pre = Preprocessor(make_config())

    def test_identifier_is_split_into_words(self):
        self.assertEqual(text_utils.split_identifier("systolic_bp"), "systolic bp")
        self.assertEqual(text_utils.split_identifier("SystolicBPValue"),
                         "Systolic BP Value")
        self.assertEqual(text_utils.split_identifier("patient-weight"),
                         "patient weight")

    def test_dotted_acronym_is_collapsed(self):
        ctx = self.pre.run("W.B.C.", "White blood cell count")
        self.assertIn("white_blood_cell_count", ctx.tokens)

    def test_python_and_sql_normalization_agree(self):
        # Must match public.pipeline_normalize(); Stage 1 compares across them.
        for raw, expected in [
            ("Blood Pressure", "blood pressure"),
            ("Naïve-Bayes", "naive bayes"),
            ("  A/B  test ", "a b test"),
            ("Glucose [Mass/volume]", "glucose mass volume"),
        ]:
            self.assertEqual(text_utils.normalize_for_exact_match(raw), expected)

    def test_unambiguous_abbreviation_expands(self):
        ctx = self.pre.run("bmi", "bmi")
        self.assertIn("body_mass_index", ctx.tokens)

    def test_ambiguous_abbreviation_produces_variants(self):
        ctx = self.pre.run("CRP", "CRP")
        labels = {v.label for v in ctx.variants}
        self.assertIn("primary", labels)
        self.assertTrue(any(l.startswith("ambiguous") for l in labels))
        self.assertTrue(any(a.ambiguous for a in ctx.abbreviations))

    def test_literal_variant_is_always_kept(self):
        # Insurance against a wrong expansion being the only thing searched.
        ctx = self.pre.run("map", "map")
        self.assertIn("literal", {v.label for v in ctx.variants})

    def test_description_is_searched_without_the_column_name(self):
        # DOC_PAT_HAIR contributes "doc" and "pat", which match no concept and
        # drag every candidate down; the description alone is the real query.
        ctx = self.pre.run("DOC_PAT_HAIR", "Hair sample has been collected")
        variants = {v.label: v.tokens for v in ctx.variants}
        self.assertIn("primary", variants)
        self.assertIn("description/primary", variants)
        self.assertIn("doc", variants["primary"])
        self.assertNotIn("doc", variants["description/primary"])

    def test_no_description_variant_when_the_name_adds_nothing(self):
        # Same words on both sides: a second identical query is pure cost.
        ctx = self.pre.run("hair_sample", "Hair sample has been collected")
        self.assertFalse([v for v in ctx.variants
                          if v.label.startswith("description/")])

    def test_no_description_variant_for_a_stub_description(self):
        # "Result value" is not prose; the name carries everything.
        ctx = self.pre.run("DOC_PAT_HAIR", "Result value")
        self.assertFalse([v for v in ctx.variants
                          if v.label.startswith("description/")])

    def test_description_variant_can_be_switched_off(self):
        config = make_config()
        config.description_only_variant = False
        ctx = Preprocessor(config).run("DOC_PAT_HAIR",
                                       "Hair sample has been collected")
        self.assertFalse([v for v in ctx.variants
                          if v.label.startswith("description/")])

    def test_equivalent_words_expand_on_the_query_side(self):
        from pipeline import resources
        from pipeline.stage2_fts import _expand, _or_group

        self.assertEqual(resources.equivalents()["sample"], ("sample", "specimen"))
        self.assertEqual(_expand("sample"), ["sample", "specimen"])
        # Unknown words and phrases pass through untouched, and a single-member
        # group must render identical SQL to the pre-equivalence version.
        self.assertEqual(_expand("creatinine"), ["creatinine"])
        self.assertEqual(_expand("blood pressure"), ["blood pressure"])
        self.assertEqual(_or_group(1), "plainto_tsquery('english', %s)")
        self.assertEqual(_or_group(2),
                         "(plainto_tsquery('english', %s) || "
                         "plainto_tsquery('english', %s))")

    def test_equivalence_groups_are_single_words(self):
        # _expand only substitutes whole words; a multi-word member would be
        # silently mis-counted by the group coverage calculation.
        from pipeline import resources

        for word, group in resources.equivalents().items():
            self.assertNotIn(" ", word)
            for member in group:
                self.assertNotIn(" ", member)

    def test_units_are_extracted_and_removed(self):
        ctx = self.pre.run("glucose", "Glucose 120 mg/dL")
        self.assertEqual([u.canonical for u in ctx.units], ["mg/dl"])
        self.assertEqual(ctx.units[0].unit_type, "concentration")
        self.assertNotIn("120", ctx.tokens)   # the value went with the unit
        self.assertIn("glucose", ctx.tokens)

    def test_units_imply_measurement_domain_and_loinc(self):
        ctx = self.pre.run("sbp", "Systolic blood pressure (mmHg)")
        self.assertEqual(ctx.domain_hint, "Measurement")
        self.assertEqual(ctx.vocabulary_hint, "LOINC")

    def test_ambiguous_unit_needs_context(self):
        # "hr" is a unit (hour) only with a number or bracket beside it.
        self.assertEqual(self.pre.run("hr", "Heart rate").units, [])
        with_context = self.pre.run("dur", "duration 12 hr")
        self.assertEqual([u.canonical for u in with_context.units], ["hr"])

    def test_date_pattern_sets_temporal_flag(self):
        ctx = self.pre.run("adm", "Admitted 2023-04-12")
        self.assertTrue(ctx.temporal_flag)
        self.assertIn("YYYY-MM-DD", ctx.date_formats)

    def test_duration_words_are_not_temporal(self):
        # "Length of stay in days" is a measurement, not a timestamp.
        ctx = self.pre.run("los_days", "Length of stay in days")
        self.assertFalse(ctx.temporal_flag)

    def test_operators_and_ranges_are_extracted(self):
        ctx = self.pre.run("hr", "Heart rate > 100 bpm")
        kinds = {(c.kind, c.operator) for c in ctx.numeric_constraints}
        self.assertIn(("comparison", ">"), kinds)
        self.assertTrue(ctx.numeric_flag)

        bp = self.pre.run("bp", "Blood pressure 140/90")
        self.assertIn("ratio", {c.kind for c in bp.numeric_constraints})

    def test_protected_phrase_stays_one_token(self):
        ctx = self.pre.run("chf", "congestive heart failure")
        self.assertIn("congestive_heart_failure", ctx.tokens)
        self.assertNotIn("heart", ctx.tokens)

    def test_repeated_phrase_is_deduplicated(self):
        # Expanding the name yields the same words the description already has.
        ctx = self.pre.run("SBP", "Systolic blood pressure")
        self.assertEqual(ctx.tokens, ["systolic_blood_pressure"])

    def test_subphrase_is_dropped(self):
        ctx = self.pre.run("wbc", "white blood cell count")
        self.assertIn("white_blood_cell_count", ctx.tokens)
        self.assertNotIn("white_blood_cell", ctx.tokens)

    def test_stopwords_removed_but_domain_words_kept(self):
        # Needs at least one content word, or the "never return nothing"
        # fallback restores the whole token list (see the next test).
        ctx = self.pre.run("x", "the glucose value of the patient measurement result")
        self.assertIn("glucose", ctx.tokens)
        for noise in ("value", "patient", "result", "the", "of"):
            self.assertNotIn(noise, ctx.tokens)

        kept = self.pre.run("y", "blood pressure level rate count")
        for word in ("level", "rate", "count"):
            self.assertIn(word, kept.tokens)

    def test_all_stopword_input_falls_back_rather_than_emptying(self):
        ctx = self.pre.run("x", "the value of the patient record")
        self.assertTrue(ctx.tokens)

    def test_single_character_tokens_are_dropped(self):
        ctx = self.pre.run("temp_c", "Body temperature")
        self.assertNotIn("c", ctx.tokens)

    def test_codes_are_extracted(self):
        ctx = self.pre.run("code", "LOINC: 2160-0")
        self.assertEqual([(c.vocabulary_id, c.code) for c in ctx.codes],
                         [("LOINC", "2160-0")])

    def test_code_label_is_not_mistaken_for_the_code(self):
        # "loinc code" must not yield code="code".
        ctx = self.pre.run("loinc_code", "LOINC: 2160-0 serum creatinine")
        self.assertTrue(all(any(ch.isdigit() for ch in c.code) for c in ctx.codes))

    def test_stage0_never_returns_an_empty_token_set(self):
        ctx = self.pre.run("value", "value")
        self.assertTrue(ctx.tokens)


class TestLemmatizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lem = text_utils.Lemmatizer()

    def test_irregular_forms(self):
        for word, expected in [
            ("feet", "foot"), ("mice", "mouse"), ("diagnoses", "diagnosis"),
            ("analyses", "analysis"), ("vertebrae", "vertebra"),
            ("bronchi", "bronchus"), ("indices", "index"),
        ]:
            self.assertEqual(self.lem.lemmatize(word), expected, word)

    def test_regular_plurals(self):
        self.assertEqual(self.lem.lemmatize("levels"), "level")
        self.assertEqual(self.lem.lemmatize("arteries"), "artery")

    def test_invariants_are_not_stripped(self):
        # The over-stemming that destroys IDF weight.
        for word in ("sepsis", "analysis", "diabetes", "mass", "status",
                     "arthritis", "series"):
            self.assertEqual(self.lem.lemmatize(word), word, word)

    def test_protected_phrases_are_untouched(self):
        self.assertEqual(self.lem.lemmatize("heart_failure"), "heart_failure")


class TestSimilarity(unittest.TestCase):
    def test_trigram_matches_pg_trgm_semantics(self):
        self.assertEqual(text_utils.trigrams("ab"), {"  a", " ab", "ab "})
        self.assertAlmostEqual(text_utils.trigram_similarity("word", "word"), 1.0)
        self.assertEqual(text_utils.trigram_similarity("abc", "xyz"), 0.0)

    def test_levenshtein(self):
        self.assertEqual(text_utils.levenshtein("kitten", "sitting"), 3)
        self.assertEqual(text_utils.levenshtein("bp", "b p"), 1)
        self.assertEqual(text_utils.levenshtein("", "abc"), 3)

    def test_short_strings_need_edit_distance_not_trigrams(self):
        # The Step 4.3 justification, asserted: "bp" and "b p" are one edit
        # apart but share almost no trigrams, so trigram similarity would call
        # them unrelated while Levenshtein correctly calls them near-identical.
        self.assertLess(text_utils.trigram_similarity("bp", "b p"), 0.2)
        self.assertGreater(text_utils.levenshtein_similarity("bp", "b p"), 0.6)

    def test_acronym(self):
        self.assertEqual(text_utils.acronym_of("Heart rate"), "hr")
        self.assertEqual(text_utils.acronym_of("Left ventricular ejection fraction"),
                         "lvef")


# ── Stages 3 and 6 ──────────────────────────────────────────────────────


class TestJaccard(unittest.TestCase):
    def test_rare_tokens_dominate(self):
        from pipeline.stage3_jaccard import _weighted_jaccard

        weights = {"level": 0.5, "ventricular": 9.0, "blood": 0.5}
        rare = _weighted_jaccard({"ventricular"}, {"ventricular", "level"}, weights)
        common = _weighted_jaccard({"level"}, {"ventricular", "level"}, weights)
        self.assertGreater(rare, common)

    def test_empty_sets(self):
        from pipeline.stage3_jaccard import _weighted_jaccard

        self.assertEqual(_weighted_jaccard(set(), {"a"}, {"a": 1.0}), 0.0)


class TestComposite(unittest.TestCase):
    def setUp(self) -> None:
        self.config = make_config()
        self.scorer = CompositeScorer(self.config, db=None)   # no DB needed
        self.ctx = ContextBundle(column_name="x", column_description=None)

    def _candidate(self, **scores) -> Candidate:
        cand = Candidate(concept_id=1, concept_name="Test", domain_id="Measurement",
                         vocabulary_id="OTHER")
        for key, value in scores.items():
            setattr(cand, key, value)
        return cand

    def test_all_stages_present(self):
        cand = self._candidate(fts_score=80.0, jaccard_score=80.0,
                               fuzzy_score=80.0, embedding_score=80.0)
        self.assertAlmostEqual(self.scorer._composite(self.ctx, cand), 80.0)

    def test_missing_stage_weight_is_redistributed_not_zeroed(self):
        # 80 on three stages must stay 80, not fall to 64.
        cand = self._candidate(fts_score=80.0, jaccard_score=80.0, fuzzy_score=80.0)
        self.assertAlmostEqual(self.scorer._composite(self.ctx, cand), 80.0)

    def test_weights_are_respected(self):
        cand = self._candidate(fts_score=100.0, jaccard_score=0.0,
                               fuzzy_score=0.0, embedding_score=0.0)
        self.assertAlmostEqual(self.scorer._composite(self.ctx, cand), 35.0)

    def test_no_scores_at_all(self):
        self.assertEqual(self.scorer._composite(self.ctx, self._candidate()), 0.0)

    def test_broad_class_is_penalized(self):
        cand = self._candidate(fts_score=90.0, jaccard_score=90.0,
                               fuzzy_score=90.0, embedding_score=90.0)
        cand.concept_class_id = "Classification"
        self.assertLess(self.scorer._composite(self.ctx, cand), 90.0)

    def test_decide_thresholds(self):
        def status(score: float) -> str:
            cand = self._candidate()
            cand.composite_score = score
            return self.scorer.decide([cand])[0]

        self.assertEqual(status(95.0), "auto_accept")
        self.assertEqual(status(90.0), "auto_accept")
        self.assertEqual(status(80.0), "review")
        self.assertEqual(status(70.0), "review")
        self.assertEqual(status(69.9), "unmapped")
        self.assertEqual(self.scorer.decide([])[0], "unmapped")

    def test_merge_keeps_best_per_concept(self):
        low = Candidate(concept_id=7, concept_name="a", domain_id="d",
                        vocabulary_id="v")
        low.composite_score = 40.0
        high = Candidate(concept_id=7, concept_name="a", domain_id="d",
                         vocabulary_id="v")
        high.composite_score = 90.0
        merged = merge_candidates([[low], [high]])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].composite_score, 90.0)


class TestDomainRelation(unittest.TestCase):
    def test_relations(self):
        self.assertEqual(domain_relation("Measurement", "Measurement"), "match")
        self.assertEqual(domain_relation("Measurement", "Observation"), "plausible")
        self.assertEqual(domain_relation("Measurement", "Drug"), "wrong")
        self.assertEqual(domain_relation(None, "Drug"), "unknown")


class TestAdjuster(unittest.TestCase):
    def setUp(self) -> None:
        self.config = make_config()
        self.ctx = ContextBundle(column_name="x", column_description=None)
        self.ctx.domain_hint = "Measurement"

    def _cand(self, domain: str, vocab: str) -> Candidate:
        return Candidate(concept_id=1, concept_name="n", domain_id=domain,
                         vocabulary_id=vocab)

    def test_uniform_adjustment_is_skipped(self):
        # Every candidate in the hinted domain and vocabulary: multiplying them
        # all by 1.10 only pushes them into the cap.
        candidates = [self._cand("Measurement", "SNOMED") for _ in range(3)]
        adjust = make_adjuster(self.config, self.ctx, candidates)
        self.assertAlmostEqual(adjust(candidates[0], 95.0), 95.0)

    def test_discriminating_adjustment_is_applied(self):
        candidates = [self._cand("Measurement", "SNOMED"),
                      self._cand("Drug", "SNOMED")]
        adjust = make_adjuster(self.config, self.ctx, candidates)
        self.assertGreater(adjust(candidates[0], 50.0), 50.0)    # match: boost
        self.assertLess(adjust(candidates[1], 50.0), 50.0)       # wrong: penalty

    def test_score_is_capped(self):
        candidates = [self._cand("Measurement", "SNOMED"), self._cand("Drug", "X")]
        adjust = make_adjuster(self.config, self.ctx, candidates)
        self.assertLessEqual(adjust(candidates[0], 99.0), 100.0)


class TestDomainRestriction(unittest.TestCase):
    """The caller's `--domain` allow-list, as pure logic."""

    def setUp(self) -> None:
        self.config = make_config()

    def test_nothing_is_restricted_by_default(self):
        self.assertEqual(self.config.restrict_domains, ())
        self.assertTrue(self.config.domain_allowed("Drug"))
        self.assertTrue(self.config.domain_allowed(None))
        # The pre-filter must pass through untouched, or every default run
        # would change behaviour the moment this feature was added.
        self.assertEqual(self.config.domain_filter(["Measurement"]), ["Measurement"])
        self.assertEqual(self.config.domain_filter(()), [])

    def test_only_the_named_domains_are_allowed(self):
        self.config.restrict_domains = ("Measurement",)
        self.assertTrue(self.config.domain_allowed("Measurement"))
        self.assertFalse(self.config.domain_allowed("Observation"))
        # "Only these domains" cannot be satisfied by an unknown domain.
        self.assertFalse(self.config.domain_allowed(None))

    def test_an_empty_stage_filter_becomes_the_restriction(self):
        # This is the Stage 6.4 retry: it passes () to mean "every domain",
        # and must still not escape the restriction.
        self.config.restrict_domains = ("Measurement", "Observation")
        self.assertEqual(self.config.domain_filter(()),
                         ["Measurement", "Observation"])

    def test_the_restriction_narrows_the_inferred_filter(self):
        self.config.restrict_domains = ("Measurement",)
        self.assertEqual(
            self.config.domain_filter(["Measurement", "Observation", "Drug"]),
            ["Measurement"])

    def test_the_restriction_wins_when_the_two_disagree(self):
        # Units infer Measurement; the caller asked for Condition. Intersecting
        # would leave nothing and silently return no candidates at all, so the
        # explicit instruction outranks the inference.
        self.config.restrict_domains = ("Condition",)
        self.assertEqual(self.config.domain_filter(["Measurement"]), ["Condition"])

    def test_no_hint_means_no_stage1_filter(self):
        from pipeline.stage1_exact import _allowed_domains

        # Empty, not None: domain_filter() uses empty to mean "no filter" on
        # both sides, and Stage 1's SQL reads NULL the same way.
        self.assertEqual(_allowed_domains(None), [])
        self.assertIn("Measurement", _allowed_domains("Measurement"))

    def test_the_restriction_is_recorded_in_the_run_config(self):
        self.config.restrict_domains = ("Meas Value",)
        self.assertEqual(self.config.to_json()["restrict_domains"], ["Meas Value"])

    def test_cli_flag_splits_on_commas_only(self):
        import argparse

        from pipeline.cli import requested_domains

        args = argparse.Namespace(domain=["Measurement,Observation", "Meas Value"])
        # Never on whitespace: several OMOP domain names contain a space.
        self.assertEqual(requested_domains(args),
                         ["Measurement", "Observation", "Meas Value"])
        self.assertEqual(requested_domains(argparse.Namespace(domain=None)), [])

    def test_service_tells_absent_from_empty(self):
        from pipeline.service import parse_domains

        # Absent: keep whatever the process was configured with.
        self.assertIsNone(parse_domains({"columns": []}))
        # Present and empty: lift the restriction for this request.
        self.assertEqual(parse_domains({"domains": []}), [])
        self.assertEqual(parse_domains({"domains": "Measurement"}), ["Measurement"])
        self.assertEqual(parse_domains({"domains": [" Measurement ", ""]}),
                         ["Measurement"])
        with self.assertRaises(ValueError):
            parse_domains({"domains": 5})

    def test_service_shortlist_flag_is_a_strict_boolean(self):
        from pipeline.service import parse_shortlist

        self.assertIsNone(parse_shortlist({"columns": []}))
        self.assertIs(parse_shortlist({"shortlist": True}), True)
        self.assertIs(parse_shortlist({"shortlist": False}), False)
        # A string "false" would be truthy; refuse it instead of guessing.
        with self.assertRaises(ValueError):
            parse_shortlist({"shortlist": "false"})


# ── spelling correction ─────────────────────────────────────────────────


class _FakeSpeller:
    """Stands in for pipeline.spelling.SpellChecker without the database."""

    def __init__(self, fixes: dict[str, str]) -> None:
        self.fixes = fixes

    def correct(self, word: str) -> str | None:
        return self.fixes.get(word)


class TestSpelling(unittest.TestCase):
    def preprocessor(self, fixes: dict[str, str]) -> Preprocessor:
        return Preprocessor(make_config(), speller=_FakeSpeller(fixes))

    def test_correction_adds_an_interpretation_and_keeps_the_original_first(self):
        ctx = self.preprocessor({"bolirubin": "bilirubin"}).run("", "Serum bolirubin level")
        self.assertEqual(ctx.spelling_corrections, [("bolirubin", "bilirubin")])
        self.assertIn("bolirubin", ctx.variants[0].tokens)
        self.assertNotIn("spelling", ctx.variants[0].label)
        corrected = [v for v in ctx.variants if "spelling" in v.label]
        self.assertEqual(len(corrected), 1)
        self.assertIn("bilirubin", corrected[0].tokens)

    def test_word_inside_a_hyphenated_token_is_corrected(self):
        # "c-reactuve" is one whitespace token; the letter run inside it is
        # what has to be looked up.
        ctx = self.preprocessor({"reactuve": "reactive"}).run("", "C-reactuve protein")
        self.assertEqual(ctx.spelling_corrections, [("reactuve", "reactive")])

    def test_abbreviations_are_never_spell_checked(self):
        from pipeline import resources

        abbreviation = next(k for k in resources.abbreviations() if k.isalpha() and len(k) >= 3)
        ctx = self.preprocessor({abbreviation: "wrong"}).run("", f"{abbreviation} value")
        self.assertEqual(ctx.spelling_corrections, [])

    def test_disabled_by_config(self):
        config = make_config()
        config.spelling_correction = False
        pre = Preprocessor(config, speller=_FakeSpeller({"bolirubin": "bilirubin"}))
        ctx = pre.run("", "Serum bolirubin level")
        self.assertEqual(ctx.spelling_corrections, [])
        self.assertFalse(any("spelling" in v.label for v in ctx.variants))

    def test_stage1_never_probes_a_corrected_interpretation(self):
        """An exact hit exits at 100, so it must not rest on a guess."""
        from pipeline.stage1_exact import ExactMatcher

        ctx = self.preprocessor({"bolirubin": "bilirubin"}).run("", "Serum bolirubin level")
        probes = ExactMatcher(make_config(), None).name_probes(ctx)
        self.assertFalse(any("bilirubin" in p.split() for p in probes))

    def test_a_winner_found_only_by_spelling_is_not_auto_accepted(self):
        from pipeline.models import MatchResult
        from pipeline.stage7_validate import Validator

        ctx = ContextBundle(column_name="", column_description="Systelic blood pressure",
                            spelling_corrections=[("systelic", "systemic")])
        winner = Candidate(concept_id=1, concept_name="Systemic blood pressure",
                           domain_id="Measurement", vocabulary_id="SNOMED",
                           composite_score=100.0, variant_label="spelling/primary")
        result = MatchResult(context=ctx, candidates=[winner], selected=winner,
                             status="auto_accept", confidence=100.0)
        Validator(make_config(), None)._check_spelling(result)
        self.assertEqual(result.status, "review")
        self.assertTrue(any("spelling" in f for f in result.validation_flags))


# ── integration ─────────────────────────────────────────────────────────


def database_available() -> bool:
    try:
        from pipeline.db import Database

        db = Database(make_config())
        ok = not db.check_ready()
        db.close()
        return ok
    except Exception:
        return False


@unittest.skipUnless(database_available(),
                     "vocabulary database not reachable; run ./load_vocab.sh "
                     "and ./setup_pipeline.sh")
class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from pipeline.runner import ConceptMatcher

        config = make_config()
        config.embeddings_enabled = False    # keep the suite fast and offline
        cls.matcher = ConceptMatcher(config)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.matcher.close()

    def match(self, name, description=None):
        return self.matcher.match(name, description, log_result=False)

    def test_loinc_keyword_dumps_are_not_searchable_text(self):
        """LOINC ships an index dump in the synonym field; it is not a name.

        "AOEObservation; Coll; Collect; Collected; Drugs; Nominal; ..." made
        concept 1176344 ("Sample collection timing related to drug dose")
        score 73.3 on Stage 4 for the column "Hair sample has been collected",
        because the dump contains the literal words 'sample' and 'collected'.
        """
        db = self.matcher.db
        self.assertTrue(db.fetch_value(
            "SELECT public.pipeline_is_keyword_dump(%s)",
            ("AOEObservation; Coll; Collect; Collected; Drugs; Nominal; "
             "Point in time; Random; Sample timing related to dose; Spec; Typ",)))
        # A bare ';' is syntax in genomic and cytogenetic names, not a delimiter.
        self.assertFalse(db.fetch_value(
            "SELECT public.pipeline_is_keyword_dump(%s)",
            ("t(11;22)(q24;q12.2)(FLI1,EWSR1) fusion transcript analysis, "
             "Blood or tissue specimen",)))
        # HCPCS separates long prose clauses with '; '; those are real text.
        self.assertFalse(db.fetch_value(
            "SELECT public.pipeline_is_keyword_dump(%s)",
            ("Technical component; under certain circumstances, a charge may "
             "be made for the technical component alone; under those "
             "circumstances the technical component charge is identified by "
             "adding modifier tc",)))
        # And the dump must be gone from the searchable view.
        synonyms = db.fetch_value(
            "SELECT synonyms FROM pipeline.concept_search WHERE concept_id = 1176344")
        self.assertTrue(all("AOEObservation" not in s for s in (synonyms or [])))

    def test_exact_name_match_exits_at_stage_1(self):
        result = self.match("bp", "Systolic blood pressure")
        self.assertEqual(result.decided_at_stage, "stage1")
        self.assertEqual(result.confidence, 100.0)
        self.assertEqual(result.selected.concept_name, "Systolic blood pressure")

    def test_code_lookup(self):
        result = self.match("creat", "LOINC 2160-0")
        self.assertEqual(result.selected.concept_code, "2160-0")
        self.assertEqual(result.selected.vocabulary_id, "LOINC")

    def test_source_vocabulary_code_is_mapped_or_flagged(self):
        # Against the full vocabulary the code resolves through 'Maps to';
        # against the standard-only one it cannot, and must say so rather
        # than be silently dropped.
        result = self.match("dx", "ICD10: E11.9")
        if self._has_source_concepts():
            self.assertEqual(result.decided_at_stage, "stage1")
            self.assertEqual(result.selected.concept_id, 201826)  # Type 2 DM
        else:
            self.assertTrue(any("source vocabulary" in f
                                for f in result.validation_flags),
                            result.validation_flags)

    def test_inferred_code_is_not_followed_through_maps_to(self):
        # "Q14" is ICD-10-shaped, but here it is a questionnaire item number.
        # Followed, it resolves to a congenital eye anomaly at confidence 100.
        result = self.match("Q14", "Q14. 9a. Cream/ointment:")
        self.assertNotIn("code", result.decision_reason)

    def _has_source_concepts(self) -> bool:
        return bool(self.matcher.db.fetch_value(
            "SELECT EXISTS (SELECT 1 FROM vocab.concept "
            "WHERE vocabulary_id = 'ICD10CM' LIMIT 1)"))

    def test_short_acronym_does_not_win_over_description(self):
        # "EGFR" is an exact synonym of a genomics concept; the description wins.
        result = self.match("egfr", "Estimated glomerular filtration rate")
        self.assertIn("glomerular", result.selected.concept_name.lower())

    def test_units_keep_the_match_in_measurement(self):
        # Bare "glucose" is an exact RxNorm drug ingredient name.
        result = self.match("glucose_mgdl", "Glucose 120 mg/dL")
        self.assertNotEqual(result.selected.domain_id, "Drug")

    def test_every_selected_concept_is_standard_and_valid(self):
        for name, description in [("sbp", "Systolic blood pressure"),
                                  ("hr", "Heart rate"),
                                  ("dx", "Type 2 diabetes mellitus")]:
            result = self.match(name, description)
            row = self.matcher.db.fetch_one(
                "SELECT standard_concept, invalid_reason FROM vocab.concept "
                "WHERE concept_id = %s", (result.selected.concept_id,))
            self.assertEqual(row["standard_concept"], "S")
            self.assertIsNone(row["invalid_reason"])

    def test_nonsense_input_is_unmapped(self):
        result = self.match("zzz", "qqq xyzzy nonsense column")
        self.assertEqual(result.status, "unmapped")

    def test_stage5_disabled_redistributes_its_weight(self):
        result = self.match("hgb", "Hemoglobin A1c")
        for cand in result.candidates:
            self.assertIsNone(cand.embedding_score)
        self.assertGreater(result.confidence, 0.0)


@unittest.skipUnless(database_available(),
                     "vocabulary database not reachable; run ./load_vocab.sh "
                     "and ./setup_pipeline.sh")
class TestDomainRestrictionIntegration(unittest.TestCase):
    """`--domain` against the real vocabulary."""

    @classmethod
    def setUpClass(cls) -> None:
        from pipeline.runner import ConceptMatcher

        config = make_config()
        config.embeddings_enabled = False
        cls.matcher = ConceptMatcher(config)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.matcher.close()

    def restrict(self, *domains: str) -> None:
        """Apply a restriction for the duration of one test."""
        self.matcher.config.restrict_domains = domains
        self.addCleanup(setattr, self.matcher.config, "restrict_domains", ())

    def match(self, name, description=None):
        return self.matcher.match(name, description, log_result=False)

    def test_no_candidate_escapes_the_restriction(self):
        self.restrict("Measurement")
        result = self.match("height")
        self.assertTrue(result.candidates)
        self.assertEqual({c.domain_id for c in result.candidates}, {"Measurement"})

    def test_the_restriction_outranks_the_inferred_domain(self):
        # The unit infers Measurement (Step 0.3). The caller said Condition,
        # and the caller is the one who knows what they are mapping.
        self.restrict("Condition")
        result = self.match("glucose_mgdl", "Glucose 120 mg/dL")
        self.assertEqual(result.context.domain_hint, "Measurement")
        self.assertTrue(result.candidates)
        self.assertEqual({c.domain_id for c in result.candidates}, {"Condition"})

    def test_an_out_of_domain_exact_hit_does_not_short_circuit(self):
        """Stage 1 exits at 100 before Stage 6, so it must filter itself.

        "Body height" is an exact concept name in Measurement. Under a
        Condition restriction that hit has to be abandoned rather than returned
        at confidence 100, and the ranked stages must get their turn.
        """
        unrestricted = self.match("Body height")
        self.assertEqual(unrestricted.decided_at_stage, "stage1")

        self.restrict("Condition")
        result = self.match("Body height")
        self.assertNotEqual(result.decided_at_stage, "stage1")
        self.assertNotIn("Measurement",
                         {c.domain_id for c in result.candidates})

    def test_unmapped_says_the_restriction_was_in_force(self):
        self.restrict("Currency")
        result = self.match("systolic blood pressure")
        self.assertEqual(result.status, "unmapped")
        self.assertIn("Currency", result.decision_reason)

    def test_shortlist_after_exact_hit_leaves_the_decision_alone(self):
        """`exact_hit_shortlist` adds alternatives behind a Stage 1 hit only.

        "Native Hawaiian" is an exact Race concept name; without the flag it is
        the whole shortlist, and the broader "Native Hawaiian or Other Pacific
        Islander" (8557) is never considered.
        """
        self.restrict("Race")
        plain = self.match("Native Hawaiian")
        self.assertEqual(plain.decided_at_stage, "stage1")
        self.assertEqual([c.concept_id for c in plain.candidates],
                         [plain.selected.concept_id])

        self.matcher.config.exact_hit_shortlist = True
        self.addCleanup(setattr, self.matcher.config, "exact_hit_shortlist", False)
        full = self.match("Native Hawaiian")
        # Same decision, field for field.
        self.assertEqual(
            (full.selected.concept_id, full.status, full.confidence, full.decided_at_stage),
            (plain.selected.concept_id, plain.status, plain.confidence, plain.decided_at_stage))
        # The hit stays first, appears once, and the restriction still holds.
        ids = [c.concept_id for c in full.candidates]
        self.assertEqual(ids[0], plain.selected.concept_id)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(8557, ids)
        self.assertEqual({c.domain_id for c in full.candidates}, {"Race"})

    def test_domain_names_are_resolved_against_the_vocabulary(self):
        db = self.matcher.db
        # Case-insensitive, because "Meas Value" is not a guessable spelling.
        self.assertEqual(db.resolve_domains(["measurement", "MEAS VALUE"]),
                         ["Measurement", "Meas Value"])
        self.assertEqual(db.resolve_domains([]), [])
        with self.assertRaises(ValueError):
            db.resolve_domains(["Measurment"])
        # Defined in vocab.domain, but holds no standard concept here — a
        # restriction to it could only ever return nothing.
        with self.assertRaises(ValueError):
            db.resolve_domains(["Modifier"])


@unittest.skipUnless(database_available(),
                     "vocabulary database not reachable; run ./load_vocab.sh "
                     "and ./setup_pipeline.sh")
class TestSpellingIntegration(unittest.TestCase):
    """SymSpell over pipeline.word_freq, end to end."""

    @classmethod
    def setUpClass(cls) -> None:
        from pipeline.runner import ConceptMatcher

        config = make_config()
        config.embeddings_enabled = False
        cls.matcher = ConceptMatcher(config)
        if cls.matcher.speller.unavailable_reason:
            raise unittest.SkipTest(cls.matcher.speller.unavailable_reason)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.matcher.close()

    def test_misspelt_drug_reaches_review(self):
        """The case that started this: unmapped with nothing scored before."""
        self.matcher.config.restrict_domains = ("Drug",)
        self.addCleanup(setattr, self.matcher.config, "restrict_domains", ())
        result = self.matcher.match("", "Certoluzimab", log_result=False)
        self.assertIn(("certoluzimab", "certolizumab"), result.context.spelling_corrections)
        self.assertEqual(result.selected.concept_id, 912263)    # certolizumab pegol
        self.assertNotEqual(result.status, "unmapped")
        self.assertNotEqual(result.status, "auto_accept")

    def test_common_words_are_left_alone(self):
        for word in ("bilirubin", "serum", "concentration", "certolizumab"):
            self.assertIsNone(self.matcher.speller.correct(word), word)

    def test_short_words_are_left_alone(self):
        self.assertIsNone(self.matcher.speller.correct("whie"))

    def test_a_typo_the_vocabulary_itself_contains_is_still_corrected(self):
        # 'creatnine' occurs once in concept names; 'creatinine' thousands of times.
        self.assertEqual(self.matcher.speller.correct("creatnine"), "creatinine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
