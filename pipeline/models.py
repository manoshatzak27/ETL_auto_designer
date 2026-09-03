"""Data carried between stages.

``ContextBundle`` is the Step 0.9 output; ``Candidate`` accumulates one score
per stage as it survives down the funnel; ``MatchResult`` is what Stage 6
decides and Stage 7 logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedCode:
    """Step 0.1 / 1.1: a vocabulary code spotted in the input."""

    vocabulary_id: str          # OMOP vocabulary_id, e.g. 'LOINC', 'SNOMED'
    code: str
    raw: str                    # the substring as it appeared
    labeled: bool = False       # True when the source spelled the vocabulary out
    confidence: float = 0.5     # 1.0 labeled, lower for pattern-only guesses

    def as_dict(self) -> dict[str, Any]:
        return {
            "vocabulary_id": self.vocabulary_id,
            "code": self.code,
            "raw": self.raw,
            "labeled": self.labeled,
            "confidence": self.confidence,
        }


@dataclass
class DetectedUnit:
    """Step 0.3: a measurement unit, kept as metadata rather than discarded."""

    raw: str
    canonical: str
    unit_type: str              # concentration | pressure | mass | ...
    expansion: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw, "canonical": self.canonical,
            "unit_type": self.unit_type, "expansion": self.expansion,
        }


@dataclass
class AbbreviationExpansion:
    """Step 0.2: one expansion applied, flagged when the source was ambiguous."""

    abbreviation: str
    expansion: str
    ambiguous: bool = False
    alternatives: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "abbreviation": self.abbreviation, "expansion": self.expansion,
            "ambiguous": self.ambiguous, "alternatives": list(self.alternatives),
        }


@dataclass
class NumericConstraint:
    """Step 0.5: an operator/range stripped from the string but kept as metadata."""

    kind: str                   # comparison | range | ratio | plain
    operator: str | None
    values: tuple[float, ...]
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "operator": self.operator,
            "values": list(self.values), "raw": self.raw,
        }


@dataclass
class SearchVariant:
    """One candidate interpretation of the input.

    An unambiguous input yields exactly one. Every ambiguous abbreviation
    (Step 0.2) multiplies the variants, and each is matched independently —
    the best-scoring one wins in Stage 6.
    """

    search_string: str
    tokens: tuple[str, ...]
    protected_phrases: tuple[str, ...]
    label: str = "primary"
    expansions: tuple[AbbreviationExpansion, ...] = ()

    @property
    def token_set(self) -> frozenset[str]:
        return frozenset(self.tokens)


@dataclass
class ContextBundle:
    """Step 0.9: everything Stage 0 learned about one source column."""

    column_name: str
    column_description: str | None
    source_table: str | None = None

    raw_input: str = ""
    normalized_input: str = ""
    search_string: str = ""                 # the primary variant
    variants: list[SearchVariant] = field(default_factory=list)

    # Step 0.1: the logical pieces the input decomposed into, e.g.
    # "Glucose 120 mg/dL" -> ["glucose", "120", "mg/dl"].
    components: list[str] = field(default_factory=list)

    tokens: list[str] = field(default_factory=list)
    protected_phrases: list[str] = field(default_factory=list)
    units: list[DetectedUnit] = field(default_factory=list)
    codes: list[ExtractedCode] = field(default_factory=list)
    abbreviations: list[AbbreviationExpansion] = field(default_factory=list)
    numeric_constraints: list[NumericConstraint] = field(default_factory=list)
    date_formats: list[str] = field(default_factory=list)

    domain_hint: str | None = None
    eligible_domains: tuple[str, ...] = ()
    vocabulary_hint: str | None = None      # e.g. LOINC when units were found
    numeric_flag: bool = False
    temporal_flag: bool = False

    lookup_key: str = ""                    # Step 7.5 manual-mapping key

    @property
    def token_set(self) -> frozenset[str]:
        return frozenset(self.tokens)

    @property
    def unit_types(self) -> list[str]:
        return sorted({u.unit_type for u in self.units})

    def as_log_dict(self) -> dict[str, Any]:
        return {
            "search_string": self.search_string,
            "tokens": list(self.tokens),
            "protected_phrases": list(self.protected_phrases),
            "units": [u.canonical for u in self.units],
            "unit_types": self.unit_types,
            "domain_hint": self.domain_hint,
            "numeric_flag": self.numeric_flag,
            "temporal_flag": self.temporal_flag,
            "extracted_codes": [c.as_dict() for c in self.codes],
            "abbreviations_expanded": [a.as_dict() for a in self.abbreviations],
        }


@dataclass
class Candidate:
    """A concept in contention, with whatever scores it has collected so far.

    A ``None`` score means the stage never ran for this candidate (it was
    already eliminated, or the stage is disabled) — distinct from 0.0, which
    means the stage ran and found nothing. Stage 6 redistributes the weight of
    ``None`` scores rather than treating them as zeros.
    """

    concept_id: int
    concept_name: str
    domain_id: str
    vocabulary_id: str
    concept_class_id: str = ""
    concept_code: str = ""
    synonyms: tuple[str, ...] = ()

    fts_score: float | None = None
    jaccard_score: float | None = None
    fuzzy_score: float | None = None
    embedding_score: float | None = None
    composite_score: float = 0.0

    raw_rank: float = 0.0                   # unadjusted ts_rank_cd / similarity
    boosts: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    variant_label: str = "primary"
    source_stage: str = ""                  # where this candidate entered

    def stage_scores(self) -> dict[str, float | None]:
        return {
            "fts": self.fts_score,
            "jaccard": self.jaccard_score,
            "fuzzy": self.fuzzy_score,
            "embedding": self.embedding_score,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "concept_name": self.concept_name,
            "domain_id": self.domain_id,
            "vocabulary_id": self.vocabulary_id,
            "concept_class_id": self.concept_class_id,
            "concept_code": self.concept_code,
            "fts_score": self.fts_score,
            "jaccard_score": self.jaccard_score,
            "fuzzy_score": self.fuzzy_score,
            "embedding_score": self.embedding_score,
            "composite_score": self.composite_score,
            "boosts": dict(self.boosts),
            "notes": list(self.notes),
        }


@dataclass
class StageTiming:
    stage: str
    elapsed_ms: float
    candidates_in: int | None = None
    candidates_out: int | None = None


@dataclass
class MatchResult:
    """What the pipeline decided for one column."""

    context: ContextBundle
    candidates: list[Candidate] = field(default_factory=list)
    selected: Candidate | None = None

    status: str = "unmapped"                # auto_accept | review | unmapped | manual | error
    confidence: float = 0.0
    decided_at_stage: str = ""
    decision_reason: str = ""
    ambiguous: bool = False
    validation_flags: list[str] = field(default_factory=list)
    replaced_from_concept_id: int | None = None

    timings: list[StageTiming] = field(default_factory=list)
    elapsed_ms: float = 0.0
    mapping_id: int | None = None
    error: str | None = None

    @property
    def concept_id(self) -> int | None:
        return self.selected.concept_id if self.selected else None

    def add_timing(self, stage: str, elapsed_ms: float,
                   candidates_in: int | None = None,
                   candidates_out: int | None = None) -> None:
        self.timings.append(StageTiming(stage, elapsed_ms, candidates_in, candidates_out))

    def as_dict(self) -> dict[str, Any]:
        return {
            "column_name": self.context.column_name,
            "column_description": self.context.column_description,
            "status": self.status,
            "confidence": round(self.confidence, 2),
            "concept_id": self.concept_id,
            "concept_name": self.selected.concept_name if self.selected else None,
            "domain_id": self.selected.domain_id if self.selected else None,
            "vocabulary_id": self.selected.vocabulary_id if self.selected else None,
            "concept_code": self.selected.concept_code if self.selected else None,
            "decided_at_stage": self.decided_at_stage,
            "decision_reason": self.decision_reason,
            "ambiguous": self.ambiguous,
            "validation_flags": list(self.validation_flags),
            "replaced_from_concept_id": self.replaced_from_concept_id,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "context": self.context.as_log_dict(),
            "candidates": [c.as_dict() for c in self.candidates[:10]],
            "error": self.error,
        }
