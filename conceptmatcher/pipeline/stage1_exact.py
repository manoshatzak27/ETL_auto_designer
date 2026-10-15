"""Stage 1 — exact and code-based matching.

Deterministic hits only. Anything found here scores 100 and the pipeline exits
immediately; anything not found here costs one indexed lookup and falls
through to Stage 2.

Order of attempts:

    1.0  a stored manual mapping        (Step 7.5 feedback loop)
    1.1  an extracted vocabulary code   (Step 1.1)
    1.2  exact concept name             (Step 1.2)
    1.3  exact synonym                  (Step 1.3)

A note on which codes can be resolved
------------------------------------
Step 1.1 asks that an invalid code be redirected through
``concept_relationship / 'Maps to'``. Whether that works depends on which
vocabulary this runs against.

``vocab`` may be the full Athena export (shared with the ETL Auto-Designer) or
the standard-only one ./load_vocab.sh builds, so every query here filters to
standard, valid concepts itself rather than relying on the database to.

Against the full vocabulary a *source* code — ICD10CM, ICD9CM, CPT4, ATC, all
non-standard by definition — resolves through its 'Maps to' edge to the
standard concept OHDSI curated for it. That path is only taken for a code the
input *labeled* ("ICD-10: E11.9"): an inferred, bare ICD-10-shaped token is
far more often a questionnaire item number ("Q14"), and a hit here exits at
100.

Against the standard-only vocabulary filtering removed every source concept and
with it every outgoing 'Maps to' edge, so an ICD-10 or CPT-4 code cannot resolve
at all. When that happens the stage says so explicitly rather than silently
falling through to a name match and presenting the result as deterministic.
See the README section "Source-vocabulary codes" for the options.
"""

from __future__ import annotations

from typing import Any

from . import text_utils
from .config import DOMAIN_COMPATIBILITY, PipelineConfig
from .db import Database
from .models import Candidate, ContextBundle, ExtractedCode

# Ranking used whenever several concepts tie on an exact string. Preference
# order: hinted domain, then a preferred vocabulary, then the shorter name
# (a shorter name that matched exactly is the more direct concept).
_TIEBREAK = """
    ORDER BY
        -- Cast is required: a bare parameter compared only to NULL leaves
        -- Postgres no way to infer its type.
        (%(domain_hint)s::text IS NOT NULL AND c.domain_id = %(domain_hint)s::text) DESC,
        CASE c.vocabulary_id
            WHEN 'SNOMED' THEN 0 WHEN 'LOINC' THEN 1
            WHEN 'RxNorm' THEN 2 ELSE 3 END,
        length(c.concept_name),
        c.concept_id
    LIMIT 5
"""


class ExactMatcher:
    """Stage 1."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._vocab_present: dict[str, bool] = {}

    # ────────────────────────────────────────────────────────────────────
    def run(self, ctx: ContextBundle, source_system: str | None = None
            ) -> tuple[Candidate | None, str, list[str]]:
        """Return ``(candidate, reason, flags)``.

        ``candidate`` is None when nothing hit. ``flags`` carries anything the
        caller should record even on a miss — chiefly a code that was extracted
        but could not be resolved, which is a fact about the input worth
        keeping in the log.
        """
        flags: list[str] = []

        manual = self._manual_mapping(ctx, source_system)
        if manual is not None:
            if self.config.domain_allowed(manual.domain_id):
                return manual, "manual mapping (Step 7.5 feedback loop)", flags
            # A restriction overrides even a stored human decision, because the
            # human who set the restriction is the more recent of the two. Said
            # out loud rather than silently skipped: a manual mapping that does
            # not fire is exactly the kind of thing that looks like a bug.
            flags.append(self._out_of_scope_flag("manual mapping", manual))

        for code in sorted(ctx.codes, key=lambda c: -c.confidence):
            hit = self._by_code(code, ctx)
            if hit is not None and not self.config.domain_allowed(hit.domain_id):
                flags.append(self._out_of_scope_flag(
                    f"code {code.vocabulary_id} {code.code}", hit))
                continue
            if hit is not None:
                label = "labeled" if code.labeled else "inferred"
                return hit, f"exact {code.vocabulary_id} code {code.code} ({label})", flags
            if code.labeled:
                flags.append(self._unresolved_code_flag(code))

        # The domain hint is a hard filter here, not a preference. An exact
        # string match in a domain the units contradict is not a deterministic
        # match: "Glucose 120 mg/dL" matches the RxNorm *drug ingredient*
        # "glucose" exactly, and returning that at confidence 100 would be
        # confidently wrong. Better to find nothing and let Stages 2-6 weigh it.
        #
        # `domain_filter` then narrows that to the caller's restriction, and an
        # empty result becomes NULL — the SQL below reads NULL as "any domain".
        allowed = self.config.domain_filter(_allowed_domains(ctx.domain_hint)) or None

        # Concept class is a hard filter here for the same reason (see
        # config.never_variable_classes, which is narrower than the
        # Stage 6 penalty list). A
        # Qualifier Value is a modifier word, not a variable: the column
        # "Weight" matched SNOMED 37111521 'Weight' (Observation /
        # Qualifier Value) exactly and was auto-accepted at 100, when the
        # ETL target is 'Body weight' (Measurement). Stage 6 already
        # penalizes these classes, but a Stage 1 hit exits before Stage 6
        # ever runs, so the penalty could never apply.

        for probe in self._name_probes(ctx):
            hit = self._by_exact_name(probe, ctx, allowed)
            if hit is not None:
                return hit, f"exact concept name match on {probe!r}", flags

        for probe in self._name_probes(ctx):
            hit = self._by_exact_synonym(probe, ctx, allowed)
            if hit is not None:
                return hit, f"exact synonym match on {probe!r}", flags

        return None, "", flags

    def name_probes(self, ctx: ContextBundle) -> list[str]:
        """The exact-match keys this stage would try, in order.

        Public so `match --explain` can show what was actually looked up
        instead of paraphrasing it.
        """
        return self._name_probes(ctx)

    def _out_of_scope_flag(self, what: str, hit: Candidate) -> str:
        allowed = ", ".join(self.config.restrict_domains)
        return (f"{what} resolved to {hit.concept_id} {hit.concept_name!r} "
                f"({hit.domain_id}), which the domain restriction ({allowed}) "
                "excludes")

    def _unresolved_code_flag(self, code: ExtractedCode) -> str:
        if not self._vocabulary_present(code.vocabulary_id):
            return (
                f"code {code.code} ({code.vocabulary_id}) not resolvable: "
                f"{code.vocabulary_id} is a source vocabulary and has no standard "
                "concepts in this database")
        return f"code {code.code} ({code.vocabulary_id}) not found"

    def _vocabulary_present(self, vocabulary_id: str) -> bool:
        """Does this vocabulary have any standard concept here at all?

        Cached: the answer is a property of the loaded vocabulary, and it is
        asked once per unresolved code.
        """
        cached = self._vocab_present.get(vocabulary_id)
        if cached is None:
            cached = bool(self.db.fetch_value(
                "SELECT EXISTS (SELECT 1 FROM vocab.concept "
                "WHERE vocabulary_id = %s AND standard_concept = 'S' "
                "AND invalid_reason IS NULL LIMIT 1)",
                (vocabulary_id,),
            ))
            self._vocab_present[vocabulary_id] = cached
        return cached

    # ── Step 7.5 ────────────────────────────────────────────────────────
    def _manual_mapping(self, ctx: ContextBundle,
                        source_system: str | None) -> Candidate | None:
        """A human already answered this exact question — never re-derive it."""
        row = self.db.fetch_one(
            """
            SELECT cs.concept_id, cs.concept_name, cs.domain_id, cs.vocabulary_id,
                   cs.concept_class_id, cs.concept_code
            FROM pipeline.manual_mapping m
            JOIN pipeline.concept_search cs ON cs.concept_id = m.concept_id
            WHERE m.active
              AND m.lookup_key = %s
              AND (m.source_system IS NOT DISTINCT FROM %s OR m.source_system IS NULL)
            ORDER BY (m.source_system IS NOT NULL) DESC, m.created_at DESC
            LIMIT 1
            """,
            (ctx.lookup_key, source_system),
        )
        return _to_candidate(row, "stage1_manual") if row else None

    # ── Step 1.1 ────────────────────────────────────────────────────────
    def _by_code(self, code: ExtractedCode, ctx: ContextBundle) -> Candidate | None:
        vocabularies = _code_vocabularies(code)

        row = self.db.fetch_one(
            f"""
            SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
                   c.concept_class_id, c.concept_code
            FROM vocab.concept c
            WHERE c.concept_code = %(code)s
              AND c.vocabulary_id = ANY(%(vocabs)s)
              AND c.standard_concept = 'S' AND c.invalid_reason IS NULL
            {_TIEBREAK}
            """,
            {"code": code.code, "vocabs": list(vocabularies),
             "domain_hint": ctx.domain_hint},
        )
        if row:
            return _to_candidate(row, "stage1_code")

        # A source-vocabulary code is only followed through 'Maps to' when the
        # input labeled it ("ICD-10: E11.9"). An inferred one is usually a
        # questionnaire item number ("Q14") that happens to be ICD-10-shaped,
        # and a hit here would exit at 100.
        if not code.labeled:
            return None

        # The code did not name a concept that survived filtering. Try the
        # 'Maps to' edge anyway — see the module docstring for why this only
        # partly works against a standard-only vocabulary.
        row = self.db.fetch_one(
            f"""
            SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
                   c.concept_class_id, c.concept_code
            FROM vocab.concept src
            JOIN vocab.concept_relationship r
                 ON r.concept_id_1 = src.concept_id
                AND r.relationship_id = 'Maps to'
                AND r.invalid_reason IS NULL
            JOIN vocab.concept c ON c.concept_id = r.concept_id_2
            WHERE src.concept_code = %(code)s
              AND src.vocabulary_id = ANY(%(vocabs)s)
              AND c.standard_concept = 'S' AND c.invalid_reason IS NULL
            {_TIEBREAK}
            """,
            {"code": code.code, "vocabs": list(vocabularies),
             "domain_hint": ctx.domain_hint},
        )
        return _to_candidate(row, "stage1_code_mapped") if row else None

    # ── Steps 1.2 / 1.3 ─────────────────────────────────────────────────
    def _name_probes(self, ctx: ContextBundle) -> list[str]:
        """Normalized strings worth testing for an exact hit, best first.

        Step 1.2 says "handle common punctuation variations" — that is what
        normalize_for_exact_match does on this side and what the index built
        over public.pipeline_normalize() does on the other, so "Blood Pressure",
        "blood-pressure" and "Blood pressure" all collapse to one key.

        Bare short acronyms are excluded. An exact hit on a four-letter token is
        not a deterministic match, it is a collision waiting to happen: "EGFR"
        is an exact *synonym* of "EGFR gene variant measurement", so a column
        called ``egfr`` whose description reads "Estimated glomerular filtration
        rate" was being auto-accepted as a genomics concept at confidence 100.
        Dropping these costs nothing, because Step 0.2 has already expanded the
        acronym and the expansion is a probe in its own right — "bmi" still
        reaches "Body mass index" via "body mass index".
        """
        probes: list[str] = []

        def add(text: str | None) -> None:
            if not text:
                return
            key = text_utils.normalize_for_exact_match(text)
            if not key or key in probes:
                return
            if _is_bare_acronym(key):
                return
            probes.append(key)

        add(ctx.column_description)
        add(ctx.lookup_key)
        for variant in ctx.variants:
            # A spelling correction is a guess, not a deterministic reading:
            # an exact hit on it would exit at 100 and auto-accept whatever the
            # guess happened to name, past every check in Stage 6. Corrected
            # interpretations go through the ranked stages and earn their score.
            # (Abbreviation expansions stay: that dictionary is curated.)
            if "spelling" in variant.label:
                continue
            add(" ".join(text_utils.split_phrase(t) for t in variant.tokens))
        add(ctx.column_name)
        return probes

    def _by_exact_name(self, probe: str, ctx: ContextBundle,
                       allowed: list[str] | None) -> Candidate | None:
        row = self.db.fetch_one(
            """
            SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
                   c.concept_class_id, c.concept_code
            FROM pipeline.concept_search c
            WHERE c.concept_name_norm = %(probe)s
              AND (%(allowed)s::text[] IS NULL OR c.domain_id = ANY(%(allowed)s::text[]))
              AND NOT (c.concept_class_id = ANY(%(broad)s::text[]))
            """ + _TIEBREAK,
            {"probe": probe, "domain_hint": ctx.domain_hint, "allowed": allowed,
             "broad": list(self.config.never_variable_classes)},
        )
        return _to_candidate(row, "stage1_name") if row else None

    def _by_exact_synonym(self, probe: str, ctx: ContextBundle,
                          allowed: list[str] | None) -> Candidate | None:
        row = self.db.fetch_one(
            """
            SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
                   c.concept_class_id, c.concept_code
            FROM vocab.concept_synonym s
            JOIN pipeline.concept_search c ON c.concept_id = s.concept_id
            WHERE public.pipeline_normalize(s.concept_synonym_name) = %(probe)s
              AND s.language_concept_id = 4180186
              AND (%(allowed)s::text[] IS NULL OR c.domain_id = ANY(%(allowed)s::text[]))
              AND NOT (c.concept_class_id = ANY(%(broad)s::text[]))
            """ + _TIEBREAK,
            {"probe": probe, "domain_hint": ctx.domain_hint, "allowed": allowed,
             "broad": list(self.config.never_variable_classes)},
        )
        return _to_candidate(row, "stage1_synonym") if row else None


# ── helpers ─────────────────────────────────────────────────────────────


# A single token this short carries too little information for an exact match
# to be deterministic. Six characters admits "sodium" and "iron" stays out.
_MIN_PROBE_LEN = 6


def _is_bare_acronym(probe: str) -> bool:
    return " " not in probe and len(probe) < _MIN_PROBE_LEN


def _allowed_domains(hint: str | None) -> list[str]:
    """Domains an exact match may come from, given a Stage 0 domain hint.

    Empty means "no restriction" (no hint was inferred) — the same convention
    ``PipelineConfig.domain_filter`` uses on both sides. Otherwise the hinted
    domain plus the ones DOMAIN_COMPATIBILITY calls plausible: everything the
    'wrong' bucket would have penalized is excluded outright, because at this
    stage there is no score to penalize, only a yes or no.
    """
    if not hint:
        return []
    rules = DOMAIN_COMPATIBILITY.get(hint)
    if rules is None:
        return [hint]
    return sorted(rules["match"] | rules["plausible"])


def _code_vocabularies(code: ExtractedCode) -> tuple[str, ...]:
    """Which OMOP vocabularies a code of this flavour might live in.

    A bare digit run is the ambiguous case: SNOMED concept ids, RxNorm RXCUIs
    and ICD-9 codes all look identical, so all three are tried.
    """
    if code.labeled:
        if code.vocabulary_id == "ICD10CM":
            return ("ICD10CM", "ICD10", "ICD10GM")
        if code.vocabulary_id == "ICD9CM":
            return ("ICD9CM", "ICD9Proc")
        if code.vocabulary_id == "RxNorm":
            return ("RxNorm", "RxNorm Extension")
        return (code.vocabulary_id,)

    if code.vocabulary_id == "SNOMED":
        return ("SNOMED", "RxNorm", "RxNorm Extension", "ICD9CM")
    if code.vocabulary_id == "ICD10CM":
        return ("ICD10CM", "ICD10", "ICD10GM")
    return (code.vocabulary_id,)


def _to_candidate(row: dict[str, Any], source_stage: str) -> Candidate:
    candidate = Candidate(
        concept_id=row["concept_id"],
        concept_name=row["concept_name"],
        domain_id=row["domain_id"],
        vocabulary_id=row["vocabulary_id"],
        concept_class_id=row.get("concept_class_id") or "",
        concept_code=row.get("concept_code") or "",
        source_stage=source_stage,
    )
    # A deterministic hit is 100 across the board; Stage 6 has nothing to
    # weigh up, and the runner short-circuits before it anyway.
    candidate.fts_score = 100.0
    candidate.jaccard_score = 100.0
    candidate.fuzzy_score = 100.0
    candidate.composite_score = 100.0
    return candidate
