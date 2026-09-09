"""Stage 0 — advanced pre-processing and context extraction.

Turns ``(column_name, column_description)`` into a :class:`ContextBundle` that
every later stage reads from.

Execution order deviates from the numbering of the specification in one place,
deliberately: units (0.3), dates (0.4) and numeric ranges (0.5) are detected
*before* clinical abbreviations are expanded (0.2). The reason is collisions —
``hr`` is both "hour" and "heart rate", ``mg`` is both "milligram" and nothing
at all, ``ms`` is both "millisecond" and "multiple sclerosis". Resolving the
unit reading first, using positional context (adjacent to a number, inside
parentheses), means the abbreviation dictionary only ever sees tokens that are
genuinely clinical. The numbered steps below say where each rule comes from.
"""

from __future__ import annotations

import itertools
import re
from functools import lru_cache
from typing import Iterable

from . import resources, text_utils
from .config import PipelineConfig
from .models import (
    AbbreviationExpansion,
    ContextBundle,
    DetectedUnit,
    ExtractedCode,
    NumericConstraint,
    SearchVariant,
)

# ── Step 0.1: embedded code patterns ────────────────────────────────────

# Spelled-out vocabulary names -> OMOP vocabulary_id. Anything matched through
# this route is high confidence because the source named the vocabulary.
_VOCAB_LABELS = {
    "loinc": "LOINC",
    "snomed": "SNOMED",
    "snomedct": "SNOMED",
    "snomed ct": "SNOMED",
    "icd10": "ICD10CM",
    "icd10cm": "ICD10CM",
    "icd 10": "ICD10CM",
    "icd9": "ICD9CM",
    "icd9cm": "ICD9CM",
    "icd 9": "ICD9CM",
    "rxnorm": "RxNorm",
    "rxcui": "RxNorm",
    "cpt": "CPT4",
    "cpt4": "CPT4",
    "hcpcs": "HCPCS",
    "ndc": "NDC",
    "atc": "ATC",
}

_LABELED_CODE = re.compile(
    r"\b(?P<vocab>loinc|snomed\s*ct|snomed|icd\s*-?\s*10\s*-?\s*cm|icd\s*-?\s*10"
    r"|icd\s*-?\s*9\s*-?\s*cm|icd\s*-?\s*9|rxnorm|rxcui|cpt\s*-?\s*4|cpt|hcpcs|ndc|atc)"
    r"\s*(?:code)?\s*[:=#]?\s*"
    r"(?P<code>[A-Za-z0-9][A-Za-z0-9.\-]{1,17})\b",
    re.IGNORECASE,
)

# Unlabeled but distinctive shapes. Ordered: the more specific pattern wins.
_BARE_LOINC = re.compile(r"\b\d{1,5}-\d\b")
_BARE_ICD10 = re.compile(r"\b[A-TV-Z]\d{2}(?:\.[0-9A-TV-Z]{1,4})?\b")
_BARE_DIGITS = re.compile(r"\b\d{6,18}\b")

# ── Step 0.4: date / time patterns ──────────────────────────────────────

_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b"), "YYYY-MM-DD HH:MM:SS"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "YYYY-MM-DD"),
    (re.compile(r"\b\d{4}/\d{2}/\d{2}\b"), "YYYY/MM/DD"),
    (re.compile(r"\b\d{2}/\d{2}/\d{4}\b"), "MM/DD/YYYY"),
    (re.compile(r"\b\d{2}-\d{2}-\d{4}\b"), "DD-MM-YYYY"),
    (re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b"), "HH:MM:SS"),
)

# Column names carry the temporal signal in words far more often than in a
# literal example value, so keywords count too.
#
# Duration words (day, month, year, hour) are deliberately absent. They mark a
# *quantity of elapsed time*, not a point in time: "Length of stay in days" is
# a measurement, and flagging it temporal restricted it to the Observation
# domain and lost the measurement concepts entirely.
_TEMPORAL_KEYWORDS = frozenset({
    "date", "datetime", "timestamp", "time", "dob", "birthdate",
    "admitted", "onset",
})

# ── Step 0.5: operators and ranges ──────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_RANGE = re.compile(rf"\b(?P<lo>{_NUM})\s*(?:-|to|–)\s*(?P<hi>{_NUM})\b", re.IGNORECASE)
_RATIO = re.compile(rf"\b(?P<num>{_NUM})\s*/\s*(?P<den>{_NUM})\b")
_COMPARISON = re.compile(rf"(?P<op><=|>=|<|>|=)\s*(?P<val>{_NUM})")
_BETWEEN = re.compile(rf"\bbetween\s+(?P<lo>{_NUM})\s+and\s+(?P<hi>{_NUM})\b", re.IGNORECASE)

# ── Step 0.3: units ─────────────────────────────────────────────────────

# Units whose spelling collides with an ordinary word or a clinical
# abbreviation. These are only read as units when the surrounding text says so
# (inside brackets, right after a number, or after "in"/"per").
_UNITS_NEEDING_CONTEXT = frozenset({
    "m", "in", "ft", "s", "sec", "d", "h", "hr", "k", "c", "f", "g", "l",
    "mo", "min", "ms", "cc", "yr", "wk", "oz", "lb", "lbs", "pa", "bar",
    "atm", "percent", "degc", "degf", "celsius", "fahrenheit",
})


def _unit_pattern() -> re.Pattern[str]:
    """Alternation over every known unit, longest first so mg/dL beats mg."""
    keys = sorted(resources.unit_table(), key=len, reverse=True)
    alts = "|".join(re.escape(k) for k in keys)
    # A unit ends at a boundary that is not another unit character; using a
    # lookahead rather than \b lets '%' and '/'-bearing units match.
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", re.IGNORECASE)


_UNIT_RE: re.Pattern[str] | None = None


def _units_regex() -> re.Pattern[str]:
    global _UNIT_RE
    if _UNIT_RE is None:
        _UNIT_RE = _unit_pattern()
    return _UNIT_RE


def _normalize_unit_token(raw: str) -> str:
    token = raw.strip().lower()
    token = token.replace("µ", "u").replace("μ", "u")
    token = token.replace("²", "2").replace("³", "3")
    return token


# ── the stage ───────────────────────────────────────────────────────────


class Preprocessor:
    """Stage 0. Stateless apart from cached dictionaries; safe to share."""

    def __init__(self, config: PipelineConfig, spacy_model: str | None = None) -> None:
        self.config = config
        self.lemmatizer = text_utils.default_lemmatizer(spacy_model)
        self._concept_token_cache: dict[str, tuple[str, ...]] = {}

    # Steps 0.3 / 0.4 / 0.5 ------------------------------------------------
    def _strip_metadata(
        self, working: str
    ) -> tuple[str, tuple[list[ExtractedCode], list[str],
                          list[DetectedUnit], list[NumericConstraint]]]:
        """Pull codes, dates, units and numerics out, returning the remainder.

        Split out of ``run`` so the same treatment can be applied to a second
        base text (the description on its own) without the extraction order
        drifting between the two.
        """
        # Codes first: a LOINC code like 2160-0 would otherwise be eaten by the
        # numeric-range rule as "2160 to 0".
        codes, working = self._extract_codes(working)

        # ── Step 0.4: dates and times ───────────────────────────────────
        dates, working = self._extract_dates(working)

        # ── Steps 0.3 and 0.5: units, then operators and ranges ─────────
        #
        # Both are *detected* against the same pre-stripping text, because
        # stripping the unit takes the measured value with it: run sequentially
        # and "> 100 bpm" loses the 100 to the unit rule before the comparison
        # rule ever sees it, and the ">" operator goes unrecorded. Spans are
        # offsets into that shared string, so blanking can be deferred.
        units, unit_spans = self._extract_units(working)
        numerics, numeric_spans = self._extract_numerics(working)

        working = text_utils.squash(_blank_spans(working, [*unit_spans, *numeric_spans]))
        return working, (codes, dates, units, numerics)

    def _description_base(self, description: str, name_text: str) -> str:
        """The description on its own, or "" if searching it separately is moot.

        Worth a second pass only when the description is prose in its own right
        *and* the column name adds words it does not already contain. When the
        name adds nothing the two queries are identical, and when the
        description is a stub ("Result value") the name is the only real
        signal — in both cases the extra pass is pure cost.
        """
        if not self.config.description_only_variant:
            return ""
        if not description or len(description.split()) < _MIN_DESCRIPTION_WORDS:
            return ""
        described = set(text_utils.tokenize(description.lower()))
        if not set(text_utils.tokenize(name_text.lower())) - described:
            return ""

        folded = text_utils.collapse_dotted_acronyms(
            text_utils.fold_characters(description))
        stripped, _ = self._strip_metadata(text_utils.squash(folded.lower()))
        return stripped

    # Step 0.9 ------------------------------------------------------------
    def run(
        self,
        column_name: str,
        column_description: str | None = None,
        source_table: str | None = None,
        corpus_stopwords: frozenset[str] = frozenset(),
    ) -> ContextBundle:
        """Build the context bundle for one source column.

        ``corpus_stopwords`` is the Step 0.7 adaptive set, computed by Stage 8
        across the whole batch; empty for a single-column run.
        """
        ctx = ContextBundle(
            column_name=column_name,
            column_description=column_description,
            source_table=source_table,
        )

        # ── Step 0.1: raw input normalization ───────────────────────────
        description = _strip_instrument_prefix(column_description, column_name)
        name_text = text_utils.split_identifier(column_name)
        if description:
            name_text = _drop_opaque_code_tokens(name_text, description)
        ctx.raw_input = " ".join(p for p in (name_text, description) if p).strip()

        working = text_utils.fold_characters(ctx.raw_input)
        working = text_utils.collapse_dotted_acronyms(working)   # W.B.C. -> WBC
        ctx.normalized_input = text_utils.squash(working.lower())
        ctx.lookup_key = text_utils.normalize_for_exact_match(ctx.raw_input)

        working, metadata = self._strip_metadata(ctx.normalized_input)
        ctx.codes, ctx.date_formats, ctx.units, ctx.numeric_constraints = metadata

        # Step 0.1: the logical components the input decomposed into.
        ctx.components = self._components(ctx, working)

        # ── Step 0.2: abbreviation expansion (may fan out) ──────────────
        #
        # Two base texts where the description justifies it: name +
        # description, and the description alone. A column name is an
        # identifier, not prose — DOC_PAT_HAIR contributes "doc" and "pat",
        # which match nothing and dilute every score, while the description
        # "Hair sample has been collected" says the whole thing. Dropping the
        # name outright is wrong too (plenty of descriptions are "Result
        # value" and the name carries everything), so both are searched and
        # Stage 6 keeps whichever scored a concept higher.
        bases = [(working, "")]
        description_base = self._description_base(description, name_text)
        if description_base:
            bases.append((description_base, "description"))

        # ── Steps 0.6 / 0.7 / 0.8 per variant ───────────────────────────
        built: list[SearchVariant] = []
        seen_tokens: set[tuple[str, ...]] = set()
        for base_text, base_label in bases:
            for label, text, expansions in self._expand_abbreviations(base_text):
                phrases, phrase_text = self._protect_phrases(text)
                tokens = self._tokens(phrase_text, corpus_stopwords)
                if not tokens or tuple(tokens) in seen_tokens:
                    continue
                seen_tokens.add(tuple(tokens))
                built.append(SearchVariant(
                    search_string=" ".join(text_utils.split_phrase(t) for t in tokens),
                    tokens=tuple(tokens),
                    protected_phrases=tuple(phrases),
                    label=f"{base_label}/{label}" if base_label else label,
                    expansions=tuple(expansions),
                ))

        if not built:
            # Everything was stripped (a column called "value_1", say). Fall
            # back to the bare normalized input so later stages still get
            # something to work with rather than an empty query.
            fallback = text_utils.tokenize(ctx.normalized_input) or [ctx.lookup_key or column_name.lower()]
            built = [SearchVariant(
                search_string=" ".join(fallback),
                tokens=tuple(fallback),
                protected_phrases=(),
                label="fallback",
            )]

        ctx.variants = built
        primary = built[0]
        ctx.search_string = primary.search_string
        ctx.tokens = list(primary.tokens)
        ctx.protected_phrases = list(primary.protected_phrases)
        # De-duplicated: the same unambiguous expansion is re-derived once per
        # variant, and logging it three times says nothing extra.
        ctx.abbreviations = _unique_expansions(e for v in built for e in v.expansions)

        # ── Step 0.3 / 0.4: domain inference ────────────────────────────
        ctx.numeric_flag = bool(ctx.numeric_constraints) or bool(ctx.units)
        ctx.temporal_flag = self._is_temporal(ctx)

        if ctx.units:
            # "If a column description contains a unit, automatically infer the
            # domain as Measurement and boost concepts from LOINC."
            ctx.domain_hint = "Measurement"
            ctx.vocabulary_hint = "LOINC"
        elif ctx.temporal_flag:
            ctx.domain_hint = "Observation"

        ctx.eligible_domains = (
            (ctx.domain_hint,) if ctx.domain_hint else tuple(self.config.eligible_domains)
        )
        return ctx

    # ── Step 0.1: code extraction ───────────────────────────────────────
    def _extract_codes(self, text: str) -> tuple[list[ExtractedCode], str]:
        codes: list[ExtractedCode] = []
        spans: list[tuple[int, int]] = []

        for m in _LABELED_CODE.finditer(text):
            label = re.sub(r"[\s\-]+", "", m.group("vocab").lower())
            vocab = _VOCAB_LABELS.get(label) or _VOCAB_LABELS.get(m.group("vocab").lower())
            if not vocab:
                continue
            # Every vocabulary here codes with digits somewhere. Without this,
            # the column "loinc_code" described as "LOINC: 2160-0" matches with
            # code="code", because the optional literal "code" in the pattern
            # is happy to be the payload instead.
            if not any(ch.isdigit() for ch in m.group("code")):
                continue
            codes.append(ExtractedCode(
                vocabulary_id=vocab,
                code=m.group("code").upper().strip(".-"),
                raw=m.group(0),
                labeled=True,
                confidence=1.0,
            ))
            spans.append(m.span())

        def _unclaimed(span: tuple[int, int]) -> bool:
            return not any(s <= span[0] and span[1] <= e for s, e in spans)

        for m in _BARE_LOINC.finditer(text):
            if _unclaimed(m.span()):
                codes.append(ExtractedCode("LOINC", m.group(0), m.group(0),
                                           labeled=False, confidence=0.6))
                spans.append(m.span())

        for m in _BARE_ICD10.finditer(text.upper()):
            if _unclaimed(m.span()):
                codes.append(ExtractedCode("ICD10CM", m.group(0), m.group(0),
                                           labeled=False, confidence=0.5))
                spans.append(m.span())

        for m in _BARE_DIGITS.finditer(text):
            if _unclaimed(m.span()) and len(m.group(0)) >= self.config.min_bare_code_digits:
                # Could be SNOMED or an RxNorm ingredient id; Stage 1 tries both.
                codes.append(ExtractedCode("SNOMED", m.group(0), m.group(0),
                                           labeled=False, confidence=0.35))
                spans.append(m.span())

        return codes, _blank_spans(text, spans)

    # ── Step 0.4 ────────────────────────────────────────────────────────
    def _extract_dates(self, text: str) -> tuple[list[str], str]:
        formats: list[str] = []
        spans: list[tuple[int, int]] = []
        for pattern, fmt in _DATE_PATTERNS:
            for m in pattern.finditer(text):
                if any(s <= m.start() and m.end() <= e for s, e in spans):
                    continue
                formats.append(fmt)
                spans.append(m.span())
        # Preserve first-seen order without duplicates.
        seen: list[str] = []
        for fmt in formats:
            if fmt not in seen:
                seen.append(fmt)
        return seen, _blank_spans(text, spans)

    # ── Step 0.3 ────────────────────────────────────────────────────────
    def _extract_units(self, text: str) -> tuple[list[DetectedUnit], list[tuple[int, int]]]:
        table = resources.unit_table()
        found: list[DetectedUnit] = []
        spans: list[tuple[int, int]] = []

        for m in _units_regex().finditer(text):
            token = _normalize_unit_token(m.group(0))
            entry = table.get(token)
            if entry is None:
                continue
            start, end = m.span()
            if token in _UNITS_NEEDING_CONTEXT and not _has_unit_context(text, start, end):
                continue

            found.append(DetectedUnit(
                raw=m.group(0),
                canonical=token,
                unit_type=entry["type"],
                expansion=entry["expansion"],
            ))
            # "Remove units from the main search string to avoid token noise":
            # take the measured value with it, so "glucose 120 mg/dl" becomes
            # "glucose" and not "glucose 120".
            spans.append((_value_start(text, start), end))

        # De-duplicate by canonical form, keeping first occurrence.
        unique: list[DetectedUnit] = []
        seen: set[str] = set()
        for unit in found:
            if unit.canonical not in seen:
                seen.add(unit.canonical)
                unique.append(unit)

        return unique, spans

    # ── Step 0.5 ────────────────────────────────────────────────────────
    def _extract_numerics(self, text: str) -> tuple[list[NumericConstraint], list[tuple[int, int]]]:
        constraints: list[NumericConstraint] = []
        spans: list[tuple[int, int]] = []

        for m in _BETWEEN.finditer(text):
            constraints.append(NumericConstraint(
                "range", "between",
                (float(m.group("lo")), float(m.group("hi"))), m.group(0)))
            spans.append(m.span())

        for m in _COMPARISON.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            constraints.append(NumericConstraint(
                "comparison", m.group("op"), (float(m.group("val")),), m.group(0)))
            spans.append(m.span())

        for m in _RATIO.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            # 140/90 — a paired reading, the classic blood-pressure shape.
            constraints.append(NumericConstraint(
                "ratio", "/", (float(m.group("num")), float(m.group("den"))), m.group(0)))
            spans.append(m.span())

        for m in _RANGE.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue
            constraints.append(NumericConstraint(
                "range", "-", (float(m.group("lo")), float(m.group("hi"))), m.group(0)))
            spans.append(m.span())

        return constraints, spans

    # ── Step 0.1: logical components ────────────────────────────────────
    def _components(self, ctx: ContextBundle, remaining: str) -> list[str]:
        parts = [p for p in remaining.split() if p]
        parts += [c.raw for c in ctx.numeric_constraints]
        parts += [u.raw for u in ctx.units]
        parts += [c.raw for c in ctx.codes]
        return parts

    # ── Step 0.2 ────────────────────────────────────────────────────────
    def _expand_abbreviations(
        self, text: str
    ) -> list[tuple[str, str, list[AbbreviationExpansion]]]:
        """Return ``(label, expanded_text, expansions)`` per interpretation.

        An abbreviation with a single expansion is substituted in place. One
        with several is ambiguous: the spec says to "carry both possibilities
        forward as separate candidate strings", so the token position becomes a
        choice point and the cartesian product of choice points becomes the
        variant list (capped by ``max_abbreviation_variants``).

        The literal, unexpanded string is always kept as a final variant. The
        dictionary is a heuristic — ``map``, ``ca`` and ``pt`` are real words
        too — and keeping the original costs one extra pass while removing the
        chance that a bad expansion is the only thing Stage 2 ever sees.
        """
        table = resources.abbreviations()
        tokens = text.split()
        if not tokens:
            return [("primary", text, [])]

        choices: list[list[tuple[str, AbbreviationExpansion | None]]] = []
        any_ambiguous = False
        any_expanded = False

        for token in tokens:
            key = token.strip(".,;:()[]{}").lower()
            options = table.get(key)
            if not options:
                choices.append([(token, None)])
                continue

            any_expanded = True
            if len(options) == 1:
                choices.append([(options[0], AbbreviationExpansion(key, options[0]))])
            else:
                any_ambiguous = True
                alts = tuple(options)
                choices.append([
                    (opt, AbbreviationExpansion(key, opt, ambiguous=True, alternatives=alts))
                    for opt in options
                ])

        if not any_expanded:
            return [("primary", text, [])]

        variants: list[tuple[str, str, list[AbbreviationExpansion]]] = []
        limit = max(1, self.config.max_abbreviation_variants)

        for index, combo in enumerate(itertools.product(*choices)):
            if len(variants) >= limit:
                break
            words = [word for word, _ in combo]
            used = [exp for _, exp in combo if exp is not None]
            label = "primary" if index == 0 else f"ambiguous_{index}"
            variants.append((label, " ".join(words), used))
            if not any_ambiguous:
                break

        # Insurance against a wrong expansion (see docstring).
        variants.append(("literal", text, []))
        return variants[: limit + 1]

    # ── Step 0.6 ────────────────────────────────────────────────────────
    def _protect_phrases(self, text: str) -> tuple[list[str], str]:
        """Replace known multi-word phrases with a single joined token.

        Uses a boundary regex rather than a padded ``str.replace``: when the
        same phrase occurs twice back to back ("systolic blood pressure
        systolic blood pressure", which is exactly what expanding an
        abbreviation in the column name against a spelled-out description
        produces), replace() consumes the separating space with the first hit
        and never sees the second.
        """
        found: list[str] = []
        for phrase in resources.protected_phrases():   # longest first
            pattern = _phrase_regex(phrase)
            text, count = pattern.subn(text_utils.join_phrase(phrase), text)
            if count:
                found.append(phrase)
        return found, text

    # ── Steps 0.7 and 0.8 ───────────────────────────────────────────────
    def _tokens(self, text: str, corpus_stopwords: frozenset[str],
                drop_boilerplate: bool = True) -> list[str]:
        words = resources.stopwords()
        drop = words["generic"] | words["metadata"] | words["structural"] | corpus_stopwords
        if drop_boilerplate:
            drop = drop | words["instrument_boilerplate"]
        keep = words["keep_always"]

        raw = text_utils.tokenize(text)
        # Single characters are identifier debris, not content: splitting
        # "temp_c" yields a bare "c" which, ANDed into the Stage 2 query,
        # matches only concepts that happen to contain a standalone letter and
        # buries everything relevant. Nothing meaningful survives Stage 0 as one
        # character — "vitamin d" and "troponin i" are protected phrases and
        # arrive here already joined.
        kept = [t for t in raw
                if "_" in t or (len(t) > 1 and (t in keep or t not in drop))]
        if not kept:
            # Never let stopword removal empty the query — a noisy match beats
            # no match at all.
            kept = raw

        lemmas: list[str] = []
        for token in kept:
            lemma = self.lemmatizer.lemmatize(token)
            if lemma and lemma not in lemmas:
                lemmas.append(lemma)

        # Drop a phrase that is wholly contained in a longer phrase we also
        # matched: "white blood cell" adds nothing next to "white blood cell
        # count", and counting both would inflate the Stage 3 union.
        phrase_tokens = [t for t in lemmas if "_" in t]
        redundant = {
            short for short in phrase_tokens
            for long in phrase_tokens
            if short != long and _is_subphrase(short, long)
        }
        return [t for t in lemmas if t not in redundant]

    # ── used by Stage 3 ─────────────────────────────────────────────────
    def concept_tokens(self, concept_name: str) -> tuple[str, ...]:
        """Tokenize a *concept* name exactly as the input side is tokenized.

        Stage 3 intersects the two token sets, so both have to go through the
        same phrase protection, stopword removal and lemmatization or the
        overlap is meaningless — "heart_failure" on one side would never meet
        "heart" + "failure" on the other.

        Cached because a batch run re-sees the same candidate concepts across
        columns (Step 8.2).
        """
        cached = self._concept_token_cache.get(concept_name)
        if cached is not None:
            return cached

        folded = text_utils.squash(text_utils.fold_characters(concept_name).lower())
        _, protected = self._protect_phrases(folded)
        # Instrument boilerplate is stripped from the query but kept here:
        # 'above', 'average' and 'general' are survey scaffolding in a
        # column description and the entire meaning in a concept name.
        tokens = tuple(self._tokens(protected, frozenset(),
                                    drop_boilerplate=False))

        if len(self._concept_token_cache) > 200_000:   # keep the cache bounded
            self._concept_token_cache.clear()
        self._concept_token_cache[concept_name] = tokens
        return tokens

    # ── Step 0.4 ────────────────────────────────────────────────────────
    def _is_temporal(self, ctx: ContextBundle) -> bool:
        if ctx.date_formats:
            return True
        words = set(text_utils.tokenize(ctx.normalized_input))
        return bool(words & _TEMPORAL_KEYWORDS)


# ── helpers ─────────────────────────────────────────────────────────────


# Survey exports (LimeSurvey, REDCap, Qualtrics) prefix the item text with the
# item's own code and its position in the form: "HAQ01[HAQ01SQ2]. 1a. Tick the
# one response ...". None of that is content — the code merely repeats the
# column name, and the enumeration is form layout.
_INSTRUMENT_CODE = re.compile(r"^\s*[\w.]+\s*(?:\[[^\]]*\])?\s*\.\s*")
_ITEM_NUMBER = re.compile(r"^\s*\d{1,3}\s*[a-z]?\s*[.)]\s*", re.IGNORECASE)


def _strip_instrument_prefix(description: str | None, column_name: str) -> str | None:
    """Drop a leading survey item code that just restates the column name.

    Conservative on purpose: the code is only removed when it actually looks
    like this column's identifier, so a description that genuinely begins with
    an abbreviation ("ALT. measured in serum") is left alone. The item number
    that usually follows ("1a.", "12.") is then stripped too, but only once and
    only directly after a code we recognized.
    """
    if not description:
        return description

    stripped = description.strip()
    match = _INSTRUMENT_CODE.match(stripped)
    if not match:
        return description

    head = match.group(0).strip().rstrip(".").strip()
    key = re.sub(r"[^a-z0-9]", "", head.lower())
    column_key = re.sub(r"[^a-z0-9]", "", column_name.lower())
    # The code must be this column's identifier, or contain it, or be contained
    # by it — LimeSurvey writes "HAQ01[HAQ01SQ2]" for column "HAQ01HAQ01SQ2".
    if not key or not column_key:
        return description
    if not (key == column_key or key in column_key or column_key in key):
        return description

    remainder = stripped[match.end():]
    remainder = _ITEM_NUMBER.sub("", remainder, count=1)
    return remainder.strip() or description


# A description this long can carry the column's meaning on its own, so an
# opaque code in the name is pure noise rather than the only available signal.
_MIN_DESCRIPTION_WORDS = 4


def _drop_opaque_code_tokens(name_text: str, description: str) -> str:
    """Remove meaningless identifier fragments from a column name.

    A survey column is called ``HAQ01HAQ01SQ2`` or ``PSS01PDPID0201``. Split
    into words those become tokens like "haq01" and "sq2", which no concept
    contains — they inflate the query, dilute the Jaccard union, and drag the
    FTS coverage down, all while the description right beside them says exactly
    what the column means.

    The test is deliberately narrow: a token is dropped only if it contains a
    digit AND is not a known abbreviation or unit AND there is a real
    description to fall back on. That keeps "hba1c", "spo2", "co2" and "t4",
    which carry their digits meaningfully and are in the dictionaries.
    """
    if len(description.split()) < _MIN_DESCRIPTION_WORDS:
        return name_text

    known = resources.abbreviations()
    units = resources.unit_table()
    kept = [
        word for word in name_text.split()
        if not any(ch.isdigit() for ch in word)
        or word.lower() in known
        or word.lower() in units
    ]
    return " ".join(kept)


def _unique_expansions(
    expansions: Iterable[AbbreviationExpansion],
) -> list[AbbreviationExpansion]:
    seen: set[tuple[str, str]] = set()
    out: list[AbbreviationExpansion] = []
    for exp in expansions:
        key = (exp.abbreviation, exp.expansion)
        if key not in seen:
            seen.add(key)
            out.append(exp)
    return out


@lru_cache(maxsize=2048)
def _phrase_regex(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w_]){re.escape(phrase)}(?![\w_])")


def _is_subphrase(short: str, long: str) -> bool:
    """Is joined phrase ``short`` a contiguous run of words inside ``long``?"""
    a, b = short.split("_"), long.split("_")
    if len(a) >= len(b):
        return False
    return any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


def _blank_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """Replace character ranges with spaces, preserving every other offset.

    Offsets have to survive because several extractors run over the same string
    and each records spans against the original indices.
    """
    spans = sorted(spans)
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(max(0, start), min(len(chars), end)):
            chars[i] = " "
    return "".join(chars)


def _has_unit_context(text: str, start: int, end: int) -> bool:
    """Does the text around ``text[start:end]`` support a unit reading?

    True when the token sits inside brackets, directly follows a number, or
    follows "in"/"per" — the three ways a column actually spells a unit.
    """
    before = text[:start].rstrip()
    after = text[end:].lstrip()

    if before.endswith(("(", "[", "{", "/")) or after.startswith((")", "]", "}")):
        return True
    if re.search(r"\d\s*$", before):
        return True
    if re.search(r"\b(?:in|per)$", before):
        return True
    return False


def _value_start(text: str, unit_start: int) -> int:
    """Start offset of a numeric literal immediately preceding a unit, if any."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*$", text[:unit_start])
    return match.start(1) if match else unit_start
