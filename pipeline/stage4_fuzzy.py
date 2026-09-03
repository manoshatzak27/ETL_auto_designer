"""Stage 4 — fuzzy string similarity with synonym expansion.

Catches what Stages 2 and 3 structurally cannot: typos, spacing differences,
and abbreviations the Stage 0 dictionary has never heard of.

Three signals are computed and the best one wins:

* **string similarity** (Step 4.1) — pg_trgm ``similarity()`` between the whole
  input and the concept name, and separately its synonyms. Run in Postgres,
  one round trip for the entire shortlist.
* **token similarity** (Step 4.3) — per input token, the best match among the
  candidate's tokens, using Levenshtein for tokens of four characters or fewer
  and trigrams for longer ones. Short strings are where trigrams break down:
  "bp" and "b p" share no trigram at all, yet are one edit apart.
* **acronym match** (Step 4.2) — does an unexpanded short token spell out the
  candidate's initials? "hr" against "Heart rate". Discoveries are recorded in
  ``pipeline.discovered_abbreviation`` for later promotion into the dictionary.

Only Stage 3's survivors are scored, so this is at most ~15 rows of work.
"""

from __future__ import annotations

import threading
from typing import Any

from . import resources, text_utils
from .config import PipelineConfig
from .db import Database
from .models import Candidate, ContextBundle, SearchVariant
from .scoring import make_adjuster
from .stage0_preprocess import Preprocessor

# An acronym shorter than this is too collision-prone to trust: two letters
# match the initials of a great many concepts.
_MIN_ACRONYM_LEN = 2
_ACRONYM_SCORE = 88.0


class FuzzyScorer:
    """Stage 4."""

    def __init__(self, config: PipelineConfig, db: Database,
                 preprocessor: Preprocessor) -> None:
        self.config = config
        self.db = db
        self.pre = preprocessor
        self._discovery_lock = threading.Lock()
        self._pending_discoveries: list[tuple[str, str, float]] = []

    # ────────────────────────────────────────────────────────────────────
    def run(self, ctx: ContextBundle, variant: SearchVariant,
            candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []

        probe = text_utils.normalize_for_exact_match(variant.search_string)
        similarity = self._string_similarity(probe, [c.concept_id for c in candidates])
        adjust = make_adjuster(self.config, ctx, candidates)

        for cand in candidates:
            row = similarity.get(cand.concept_id, {})
            name_sim = float(row.get("name_sim") or 0.0)
            synonym_sim = float(row.get("synonym_sim") or 0.0)

            score = max(name_sim, synonym_sim) * 100.0
            if synonym_sim > name_sim:
                cand.notes.append("stage4 matched on synonym")

            token_score = self._token_similarity(variant, cand)
            score = max(score, token_score)

            acronym = self._acronym_match(ctx, variant, cand)
            if acronym is not None:
                score = max(score, _ACRONYM_SCORE)
                cand.notes.append(f"stage4 acronym match: {acronym!r}")
                cand.boosts["acronym"] = 1.0

            cand.fuzzy_score = adjust(cand, score)

        ranked = sorted(candidates, key=lambda c: -(c.fuzzy_score or 0.0))
        return ranked[: self.config.fuzzy_keep]

    # ── Step 4.1 ────────────────────────────────────────────────────────
    def _string_similarity(self, probe: str,
                           concept_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Trigram similarity for the whole shortlist in one statement."""
        if not probe or not concept_ids:
            return {}
        rows = self.db.fetch_all(
            """
            SELECT cs.concept_id,
                   similarity(cs.concept_name_norm, %(probe)s) AS name_sim,
                   COALESCE((
                       SELECT max(similarity(
                           public.pipeline_normalize(s.concept_synonym_name), %(probe)s))
                       FROM vocab.concept_synonym s
                       WHERE s.concept_id = cs.concept_id
                         AND s.language_concept_id = 4180186
                   ), 0) AS synonym_sim
            FROM pipeline.concept_search cs
            WHERE cs.concept_id = ANY(%(ids)s)
            """,
            {"probe": probe, "ids": concept_ids},
        )
        return {r["concept_id"]: r for r in rows}

    # ── Step 4.3 ────────────────────────────────────────────────────────
    def _token_similarity(self, variant: SearchVariant, cand: Candidate) -> float:
        """Mean over input tokens of the best match among candidate tokens."""
        candidate_tokens = list(self.pre.concept_tokens(cand.concept_name))
        for synonym in cand.synonyms[:8]:
            candidate_tokens.extend(self.pre.concept_tokens(synonym))
        if not candidate_tokens or not variant.tokens:
            return 0.0

        totals: list[float] = []
        for token in variant.tokens:
            plain = text_utils.split_phrase(token)
            short = len(plain) <= self.config.short_token_max_len
            best = 0.0
            for other in candidate_tokens:
                other_plain = text_utils.split_phrase(other)
                if short:
                    value = text_utils.levenshtein_similarity(plain, other_plain)
                else:
                    value = text_utils.trigram_similarity(plain, other_plain)
                if value > best:
                    best = value
                    if best >= 1.0:
                        break
            totals.append(best)
        return 100.0 * (sum(totals) / len(totals))

    # ── Step 4.2 ────────────────────────────────────────────────────────
    def _acronym_match(self, ctx: ContextBundle, variant: SearchVariant,
                       cand: Candidate) -> str | None:
        """Spot an abbreviation Stage 0 did not know, and record it."""
        known = resources.abbreviations()
        # Only tokens that survived Stage 0 unexpanded are interesting; an
        # expansion that already happened is not a discovery.
        probes = [
            text_utils.split_phrase(t) for t in variant.tokens
            if "_" not in t and _MIN_ACRONYM_LEN <= len(t) <= 6 and t not in known
        ]
        if not probes:
            return None

        targets = [cand.concept_name, *cand.synonyms[:8]]
        for probe in probes:
            for target in targets:
                initials = text_utils.acronym_of(target)
                if len(initials) < _MIN_ACRONYM_LEN:
                    continue
                if probe == initials:
                    self._record_discovery(probe, target, 95.0)
                    return probe
                # One transposition/typo away, but only for longer acronyms
                # where a single edit is not most of the string.
                if len(probe) >= 3 and text_utils.levenshtein(probe, initials) == 1:
                    self._record_discovery(probe, target, 75.0)
                    return probe
        return None

    def _record_discovery(self, abbreviation: str, expansion: str,
                          confidence: float) -> None:
        if confidence < self.config.abbreviation_discovery_threshold:
            return
        with self._discovery_lock:
            self._pending_discoveries.append(
                (abbreviation.lower(), expansion.lower(), confidence))

    def flush_discoveries(self) -> int:
        """Persist buffered discoveries. Called once per run, not per column.

        Buffered rather than written inline because a batch run would otherwise
        issue an UPSERT per candidate per column, and because these rows are
        advisory — nothing downstream reads them until a human promotes them
        into ``abbreviations_local.json``.
        """
        with self._discovery_lock:
            pending, self._pending_discoveries = self._pending_discoveries, []
        if not pending:
            return 0

        with self.db.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO pipeline.discovered_abbreviation
                       (abbreviation, expansion, confidence)
                VALUES (%s, %s, %s)
                ON CONFLICT (abbreviation, expansion) DO UPDATE
                    SET times_seen = pipeline.discovered_abbreviation.times_seen + 1,
                        last_seen  = now(),
                        confidence = GREATEST(
                            pipeline.discovered_abbreviation.confidence,
                            EXCLUDED.confidence)
                """,
                pending,
            )
        return len(pending)
