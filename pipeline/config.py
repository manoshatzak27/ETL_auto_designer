"""Tunable configuration for the matching pipeline.

Every threshold, weight and boost the specification calls for lives here rather
than being scattered through the stage modules, so that tuning is a
config change and the Stage 7.5 feedback loop has one place to write to.

Precedence, lowest to highest:

    1. the defaults in this file
    2. ``pipeline.json`` / the file named by ``PIPELINE_CONFIG``
    3. the ``pipeline.scoring_weight`` table (Stage 7.5 learned adjustments)
    4. explicit keyword arguments / CLI flags
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(__file__).resolve().parent / "resources"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader — the same file docker-compose.yml consumes."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


@dataclass
class PipelineConfig:
    # ── Database ────────────────────────────────────────────────────────
    host: str = "localhost"
    port: int = 5433
    dbname: str = "athena_vocab"
    user: str = "vocab"
    password: str = "vocab_pwd"
    pool_min_size: int = 1
    pool_max_size: int = 8
    statement_timeout_ms: int = 120_000

    # ── Stage 0: preprocessing ──────────────────────────────────────────
    # Ambiguous abbreviations fan the input out into several search strings.
    # Each variant costs a full Stage 2-5 pass, so the count is capped.
    max_abbreviation_variants: int = 4
    # Step 0.7: a "corpus_adaptive" stopword is only dropped once it appears in
    # at least this fraction of the batch (the spec's 80% rule).
    stopword_corpus_threshold: float = 0.8
    # Below this, a bare digit run is not taken to be a SNOMED/RxNorm id.
    min_bare_code_digits: int = 6
    # When a column has a real description, also search the description on its
    # own, as a second interpretation alongside name+description. A column name
    # is an identifier, not prose: DOC_PAT_HAIR contributes "doc" and "pat",
    # which match nothing and drag every candidate's score down.
    #
    # It is a trade, not a free win — see "Measured behaviour" in the README.
    # Columns whose name carries the signal and whose description is a vague
    # paraphrase (clotting_time / "How long blood takes to clot") get worse.
    # Set false to search name+description only.
    description_only_variant: bool = True

    # ── Domain restriction (caller-supplied) ────────────────────────────
    # A hard allow-list: when non-empty, no concept outside these OMOP domains
    # can be returned by any stage. Empty means no restriction. Set it with
    # `--domain Measurement` on the CLI, `:domain` in the shell, `"domains"` in
    # a /match request body, or `restrict_domains` in pipeline.json.
    #
    # Deliberately separate from `eligible_domains` below. That one is a
    # *default* pre-filter: Stage 0's inferred hint narrows it and the Stage 6.4
    # retry drops it entirely. This one is never relaxed — it outranks an
    # inferred hint it disagrees with, survives the 6.4 retry, and applies to
    # Stage 1's deterministic hits, which exit before Stage 6 and so are beyond
    # the reach of every score-based preference.
    #
    # It is a filter, not a boost: the restriction is not fed in as a domain
    # hint, because every surviving candidate satisfies it and a multiplier
    # applied to the whole shortlist only costs headroom (see scoring.py).
    restrict_domains: tuple[str, ...] = ()

    # ── Shortlist behind a Stage 1 hit (caller-supplied) ────────────────
    # A Stage 1 hit exits at 100 with that one concept as the whole shortlist,
    # which is the right *answer* but an empty list of *alternatives*: a caller
    # showing the user a pick-list (the ETL Auto-Designer's concept search) got
    # "Native Hawaiian" alone and never "Native Hawaiian or Other Pacific
    # Islander". When True, Stages 2-6 still run after the hit, only to fill
    # `candidates` behind it. The decision — selected concept, status 100 /
    # auto_accept, decided_at_stage — is exactly what it is with this off, so
    # no benchmark answer can change; the cost is the Stage 2-6 latency on
    # columns that would otherwise exit early. Off by default; `"shortlist":
    # true` in a /match body turns it on for one request.
    exact_hit_shortlist: bool = False

    # ── Stage 2: full-text search ───────────────────────────────────────
    # Step 2.1. Device/Specimen/etc. are deliberately absent: they stay
    # reachable through the Stage 6.4 no-domain-filter retry.
    eligible_domains: tuple[str, ...] = (
        "Measurement", "Observation", "Condition", "Procedure", "Drug",
    )
    fts_candidate_limit: int = 30          # Step 2.4: top 20-30
    # Step 2.4's rank threshold, applied *relative* to the best ts_rank_cd in
    # the same result set: keep candidates ranking at least this fraction of
    # the best. Absolute ts_rank_cd values are not comparable across queries
    # (they move with query length and term rarity), so a fixed cutoff admits
    # everything for one column and nothing for the next.
    fts_rank_threshold: float = 0.2
    # Trigram recall net for when FTS matches nothing at all.
    trigram_fallback_limit: int = 30
    trigram_fallback_threshold: float = 0.35
    # Step 2.3b: least a candidate keeps when every content word of its name
    # is one the query never asked for. 1.0 disables the precision discount.
    #
    # DEFAULT IS OFF, on measurement. The idea is sound — a concept full of
    # words the query never asked for is a worse match — but at 0.70 it cost
    # 2.2pp of gold-set literal accuracy and 4pp of top-5, because a long
    # LOINC name ('Glucose [Mass/volume] in Serum or Plasma') is *specifying*
    # the measurement, not padding it. Stage 3's Jaccard expresses the same
    # preference through its union denominator without that cost.
    specificity_floor: float = 1.0

    # ── Stage 3: weighted Jaccard ───────────────────────────────────────
    jaccard_keep: int = 15                 # Stage 3 scoring line
    # Fold equivalent words onto one representative before comparing token
    # sets, so a candidate Stage 2 found through an equivalence
    # (profession -> occupation) does not then score a flat Jaccard 0.
    # Measured cost and benefit are in the README; set false to compare
    # token sets literally.
    equivalence_folding: bool = True
    # Compare token sets as Snowball stems — the same folding Postgres already
    # applied in Stage 2 — rather than as the Python lemmatizer's lemmas.
    #
    # Fixes a real asymmetry: the lemmatizer does not touch verb inflection, so
    # a concept that reached Stage 3 *because* Postgres matched started/start
    # was then scored as though the two words were unrelated. The evidence is
    # genuinely mixed, because Snowball also strips derivational suffixes:
    # 'calculated' and 'calculator' both become 'calcul', which is wrong.
    # Measured on the 206 curated columns: -1 exact (PAT_HOURS, which moves to
    # an equally valid paraphrase), -2 agreed-on-meaning (BSA_Total, PHQ9PHQ2),
    # +1 fewer unmapped, top-5 unchanged, gold set unchanged. Set false to
    # compare lemmas.
    stem_tokens: bool = True

    # ── Stage 4: fuzzy ──────────────────────────────────────────────────
    fuzzy_keep: int = 10                   # Stage 4 scoring line
    # Step 4.3: Levenshtein for short tokens, trigram for long ones.
    short_token_max_len: int = 4
    # Step 4.2: only record a discovered abbreviation this similar or better.
    abbreviation_discovery_threshold: float = 70.0

    # ── Stage 5: embeddings ─────────────────────────────────────────────
    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = 64
    # Asymmetric encoding. BGE- and E5-family models (which most medical
    # embedding models are built on) are trained with an instruction prefix on
    # the query side only, and lose accuracy without it. Symmetric models such
    # as MiniLM and PubMedBERT want both empty. See EMBEDDING_MODEL_PRESETS.
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    # Step 5.2: below this many Stage 4 survivors, widen the embedding search.
    embedding_widen_below: int = 5
    embedding_widen_limit: int = 200
    # Step 5.3: how much a better-matching synonym may lift a candidate.
    synonym_embedding_boost: float = 1.10
    # Synonym texts are embedded on the fly rather than cached in Postgres
    # (the cache is keyed by concept, one row per concept), so the work is
    # bounded to the few candidates where it can still change the ranking.
    synonym_embedding_candidates: int = 5
    synonym_embedding_max: int = 3

    # ── Shared boosts (Stages 2, 3, 4, 5) ───────────────────────────────
    vocabulary_boost: dict[str, float] = field(default_factory=lambda: {
        "SNOMED": 1.10,
        "LOINC": 1.10,
        "RxNorm": 1.05,
        "RxNorm Extension": 1.05,
    })
    domain_match_boost: float = 1.10       # Step 3.3
    domain_mismatch_penalty: float = 0.80  # Step 3.3 (-20%)

    # ── Stage 6: composite ──────────────────────────────────────────────
    stage_weights: dict[str, float] = field(default_factory=lambda: {
        "fts": 0.35,
        "jaccard": 0.25,
        "fuzzy": 0.20,
        "embedding": 0.20,
    })
    final_vocabulary_boost: float = 1.05   # Step 6.1
    final_domain_boost: float = 1.05       # Step 6.1
    hierarchy_depth_boost: float = 1.02    # Step 6.2
    hierarchy_tie_margin: float = 2.0      # "nearly identical scores"
    # Step 1.2/1.3: classes an exact name match must never resolve to. This is
    # a HARD filter, so it is deliberately much narrower than
    # broad_concept_classes below (which is only a 3% Stage 6 penalty).
    #
    # A Qualifier Value is a modifier word, not a variable: the column "Weight"
    # matched SNOMED 37111521 'Weight' (Qualifier Value) exactly and was
    # auto-accepted at 100, when the ETL target is 'Body weight' (Measurement).
    # The classification containers below are branches of a hierarchy, never a
    # column's target.
    #
    # "Staging / Scales" and "Answer" are deliberately ABSENT even though both
    # sit in broad_concept_classes. Blocking Staging / Scales cost 3 exact
    # agreements on the curated set — 'Swollen joint count', 'Tender joint
    # count' and 'Enthesitis index' are SNOMED/Measurement/Staging-Scales and
    # are exactly the right target for a rheumatology study. A clinical score
    # is a measurement, not a classification.
    never_variable_classes: tuple[str, ...] = (
        "Qualifier Value",
        "Classification", "ICD10 Chapter", "ICD10 SubChapter", "ICD9CM Chapter",
        "ATC 1st", "ATC 2nd", "ATC 3rd", "LOINC Class", "LOINC Hierarchy",
    )

    # Step 6.2: concept classes that are broad classifications, not findings.
    broad_concept_classes: tuple[str, ...] = (
        "Classification", "ICD10 Chapter", "ICD10 SubChapter",
        "ICD9CM Chapter", "ATC 1st", "ATC 2nd", "ATC 3rd",
        "LOINC Class", "LOINC Hierarchy", "Context-dependent",
        "Linkage Concept", "Qualifier Value", "Staging / Scales",
        # LOINC classes that are never a column's ETL target: an answer-list
        # entry (that is a *value*, not a variable) and a claims attachment.
        #
        # "Clinical Observation" and "Survey" are deliberately absent. Clinical
        # Observation is the class of LOINC vital signs. Survey is the class of
        # every PROMIS / Veterans RAND / PHQ-9 / HAQ item — for questionnaire
        # data those concepts are precisely the right answer, and penalizing
        # them was an over-fit to a lab-and-vitals test set.
        "Answer", "Claims Attachment",
    )
    broad_class_penalty: float = 0.97

    # Step 6.4
    auto_accept_threshold: float = 90.0
    review_threshold: float = 70.0

    # ── Stage 7: validation ─────────────────────────────────────────────
    ambiguity_margin: float = 5.0          # Step 7.1
    unit_mismatch_penalty: float = 10.0    # Step 7.2 (absolute points)
    expected_source_vocabularies: tuple[str, ...] = ()   # Step 7.3, empty = any

    # ── Stage 8: batch ──────────────────────────────────────────────────
    batch_workers: int = 4
    shortlist_cache_size: int = 512        # Step 8.2

    # ── Bookkeeping ─────────────────────────────────────────────────────
    log_to_db: bool = True
    created_by: str | None = None

    # ────────────────────────────────────────────────────────────────────
    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password} "
            f"application_name=omop_concept_matcher"
        )

    def weight_for(self, stage: str) -> float:
        return float(self.stage_weights.get(stage, 0.0))

    def vocab_multiplier(self, vocabulary_id: str | None) -> float:
        if not vocabulary_id:
            return 1.0
        return float(self.vocabulary_boost.get(vocabulary_id, 1.0))

    # ── the domain restriction ──────────────────────────────────────────
    def domain_allowed(self, domain_id: str | None) -> bool:
        """May a candidate from this domain be returned at all?

        Always true with no restriction set. A candidate whose domain is
        unknown (``None``) is *not* admitted under a restriction — "only these
        domains" cannot be satisfied by a domain nobody knows.
        """
        if not self.restrict_domains:
            return True
        return domain_id in set(self.restrict_domains)

    def domain_filter(self, domains: Sequence[str] | None) -> list[str]:
        """A stage's domain pre-filter, narrowed by the restriction.

        An empty list means "no filter" on both sides, in and out — which is
        what Stage 2 and Stage 5 already use to mean "search every domain".

        When the restriction and the stage's own filter do not overlap, the
        restriction wins outright rather than the intersection emptying the
        result: a stage filter comes from an inferred hint (units imply
        Measurement), the restriction comes from the caller, and the caller's
        explicit choice is the better evidence of the two.
        """
        restriction = list(self.restrict_domains)
        if not restriction:
            return list(domains or ())
        if not domains:
            return restriction
        overlap = [d for d in domains if d in set(restriction)]
        return overlap or restriction

    def to_json(self) -> dict[str, Any]:
        """Serializable snapshot, minus the password, for run logging."""
        data = asdict(self)
        data.pop("password", None)
        data["eligible_domains"] = list(self.eligible_domains)
        data["restrict_domains"] = list(self.restrict_domains)
        data["broad_concept_classes"] = list(self.broad_concept_classes)
        data["expected_source_vocabularies"] = list(self.expected_source_vocabularies)
        return data

    # ────────────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, overrides: dict[str, Any] | None = None) -> "PipelineConfig":
        """Build a config from .env, an optional JSON file, then overrides."""
        cfg = cls()

        env = {**_load_dotenv(PROJECT_ROOT / ".env"), **os.environ}
        if "POSTGRES_HOST" in env:
            cfg.host = env["POSTGRES_HOST"]
        if "POSTGRES_PORT" in env:
            cfg.port = int(env["POSTGRES_PORT"])
        if "POSTGRES_DB" in env:
            cfg.dbname = env["POSTGRES_DB"]
        if "POSTGRES_USER" in env:
            cfg.user = env["POSTGRES_USER"]
        if "POSTGRES_PASSWORD" in env:
            cfg.password = env["POSTGRES_PASSWORD"]

        config_path = Path(env.get("PIPELINE_CONFIG", PROJECT_ROOT / "pipeline.json"))
        if config_path.is_file():
            cfg.apply(json.loads(config_path.read_text(encoding="utf-8")))

        if overrides:
            cfg.apply(overrides)
        return cfg

    def apply(self, values: dict[str, Any]) -> None:
        """Apply a dict of overrides, ignoring unknown keys and comments."""
        known = {f.name for f in fields(self)}
        for key, value in values.items():
            if key.startswith("_") or key not in known:
                continue
            current = getattr(self, key)
            if isinstance(current, tuple) and isinstance(value, list):
                value = tuple(value)
            elif isinstance(current, dict) and isinstance(value, dict):
                merged = dict(current)
                merged.update(value)
                value = merged
            setattr(self, key, value)

    def apply_learned_weights(self, rows: list[tuple[str, float]]) -> None:
        """Stage 7.5: fold `pipeline.scoring_weight` rows into the config.

        Keys are dotted paths so the feedback loop can address nested values:
        ``stage_weights.fts``, ``vocabulary_boost.SNOMED``, or a plain scalar
        field name such as ``auto_accept_threshold``.
        """
        for key, weight in rows:
            head, _, tail = key.partition(".")
            if tail:
                target = getattr(self, head, None)
                if isinstance(target, dict):
                    target[tail] = float(weight)
            elif hasattr(self, head):
                setattr(self, head, float(weight))


# Known-good settings for the embedding models worth considering, and which of
# them need an instruction prefix. `embedding_model` may name anything
# sentence-transformers can load, preset or not.
#
# The `measured` figures come from benchmarks/stage5_rerank.py over the 44
# winnable cases of benchmarks/gold_set.csv: top-1 accuracy reranking the real
# Stage 2 shortlist by cosine alone, against 93.2% for the unreranked lexical
# ordering. The headline result is that a biomedical model does NOT beat
# general-purpose MiniLM here and costs 2-5x the time — concept names are short
# noun phrases, which is what MiniLM is trained for, while PubMedBERT-family
# models are trained on biomedical prose. Re-measure before switching; that is
# what the benchmark is for.
EMBEDDING_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "sentence-transformers/all-MiniLM-L6-v2": {
        "query_prefix": "", "document_prefix": "",
        "note": "general purpose, 384 dims, ~90MB",
        "measured": "97.7% top-1, 0.982 MRR, 41 ms/case — default",
    },
    "NeuML/pubmedbert-base-embeddings": {
        "query_prefix": "", "document_prefix": "",
        "note": "PubMedBERT trained for sentence embeddings, 768 dims, ~420MB",
        "measured": "95.5% top-1, 0.977 MRR, 193 ms/case — worse and 4.7x slower",
    },
    "abhinand/MedEmbed-small-v0.1": {
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "document_prefix": "",
        "note": "BGE-small fine-tuned on medical retrieval, 384 dims, ~130MB",
        "measured": "97.7% top-1, 0.981 MRR, 95 ms/case — ties MiniLM, 2.3x slower",
    },
    "abhinand/MedEmbed-base-v0.1": {
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "document_prefix": "",
        "note": "BGE-base fine-tuned on medical retrieval, 768 dims, ~440MB",
        "measured": "not benchmarked",
    },
    "pritamdeka/S-PubMedBert-MS-MARCO": {
        "query_prefix": "", "document_prefix": "",
        "note": "PubMedBERT fine-tuned on MS-MARCO retrieval, 768 dims, ~420MB",
        "measured": "not benchmarked",
    },
}


# Step 3.3 / 6.1: which candidate domains count as a match, as merely
# plausible (no adjustment), or as clearly wrong (penalty) for a given hint.
DOMAIN_COMPATIBILITY: dict[str, dict[str, frozenset[str]]] = {
    "Measurement": {
        "match": frozenset({"Measurement"}),
        "plausible": frozenset({"Observation", "Meas Value", "Meas Value Operator",
                                "Specimen", "Spec Anatomic Site", "Unit", "Procedure"}),
    },
    "Observation": {
        "match": frozenset({"Observation"}),
        "plausible": frozenset({"Measurement", "Meas Value", "Condition", "Episode",
                                "Type Concept", "Metadata", "Visit"}),
    },
    "Condition": {
        "match": frozenset({"Condition"}),
        "plausible": frozenset({"Observation", "Condition Status", "Episode",
                                "Spec Anatomic Site", "Measurement"}),
    },
    "Procedure": {
        "match": frozenset({"Procedure"}),
        "plausible": frozenset({"Observation", "Measurement", "Device", "Visit",
                                "Spec Anatomic Site", "Regimen"}),
    },
    "Drug": {
        "match": frozenset({"Drug"}),
        "plausible": frozenset({"Device", "Route", "Unit", "Regimen", "Procedure"}),
    },
    "Device": {
        "match": frozenset({"Device"}),
        "plausible": frozenset({"Procedure", "Drug", "Observation"}),
    },
}


def domain_relation(hint: str | None, candidate_domain: str | None) -> str:
    """Return 'match', 'plausible', 'wrong' or 'unknown' for a domain pair."""
    if not hint or not candidate_domain:
        return "unknown"
    rules = DOMAIN_COMPATIBILITY.get(hint)
    if rules is None:
        return "match" if hint == candidate_domain else "plausible"
    if candidate_domain in rules["match"]:
        return "match"
    if candidate_domain in rules["plausible"]:
        return "plausible"
    return "wrong"
