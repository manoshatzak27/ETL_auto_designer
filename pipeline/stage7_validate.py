"""Stage 7 — post-match validation, logging and the feedback loop.

Three independent pieces:

* :class:`Validator` — the Step 7.1-7.3 checks. These run *after* Stage 6 has
  picked a winner and can only ever make the outcome more conservative: they
  lower confidence and downgrade status, never the reverse.
* :class:`MappingLogger` — Step 7.4. Writes the full decision trail, including
  every candidate's per-stage scores, so a mapping can be explained months
  later without re-running anything.
* :class:`FeedbackStore` — Step 7.5. Records human corrections so Stage 1 finds
  them next time, promotes discovered abbreviations, and persists tuned
  scoring weights.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from . import resources
from .config import PipelineConfig
from .db import Database
from .models import MatchResult
from .stage6_composite import candidate_rows


class Validator:
    """Steps 7.1 - 7.3."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._assigned: dict[int, str] = {}      # concept_id -> first column that took it
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._assigned.clear()

    # ────────────────────────────────────────────────────────────────────
    def validate(self, result: MatchResult) -> None:
        if result.selected is None:
            return

        self._check_ambiguity(result)
        self._check_units(result)
        self._check_concept(result)

        # Confidence may have been reduced below the accept threshold by the
        # checks above; re-derive the status so the two never disagree.
        if result.status == "auto_accept" and (
            result.confidence < self.config.auto_accept_threshold or result.ambiguous
        ):
            result.status = "review" if result.confidence >= self.config.review_threshold \
                else "unmapped"

    # ── Step 7.1 ────────────────────────────────────────────────────────
    def _check_ambiguity(self, result: MatchResult) -> None:
        """Two different concepts within 5 points is not a decision."""
        if len(result.candidates) < 2:
            return
        best, runner_up = result.candidates[0], result.candidates[1]
        if best.concept_id == runner_up.concept_id:
            return

        gap = best.composite_score - runner_up.composite_score
        if gap > self.config.ambiguity_margin:
            return

        result.ambiguous = True
        result.validation_flags.append(
            f"ambiguous: {runner_up.concept_id} ({runner_up.concept_name}) "
            f"scores {runner_up.composite_score:.1f} vs "
            f"{best.composite_score:.1f}, gap {gap:.1f}")
        if result.status == "auto_accept":
            result.status = "review"
            result.decision_reason += "; downgraded to review (ambiguous match)"

    # ── Step 7.2 ────────────────────────────────────────────────────────
    def _check_units(self, result: MatchResult) -> None:
        ctx, selected = result.context, result.selected
        if not ctx.units or selected is None:
            return

        penalized = False
        if selected.domain_id != "Measurement":
            result.validation_flags.append(
                f"unit/domain mismatch: units {ctx.unit_types} detected but "
                f"concept domain is {selected.domain_id}")
            penalized = True

        expected = _expected_unit_types(selected.concept_name)
        if expected:
            detected = set(ctx.unit_types)
            if detected and not (detected & expected):
                result.validation_flags.append(
                    f"unit type mismatch: {sorted(detected)} for "
                    f"{selected.concept_name!r}, expected {sorted(expected)}")
                penalized = True

        if penalized:
            result.confidence = max(0.0, result.confidence - self.config.unit_mismatch_penalty)
            selected.notes.append(
                f"confidence reduced by {self.config.unit_mismatch_penalty:g} "
                "(Step 7.2 unit check)")

    # ── Step 7.3 ────────────────────────────────────────────────────────
    def _check_concept(self, result: MatchResult) -> None:
        selected = result.selected
        if selected is None:
            return

        expected_vocabs = self.config.expected_source_vocabularies
        if expected_vocabs and selected.vocabulary_id not in expected_vocabs:
            result.validation_flags.append(
                f"vocabulary {selected.vocabulary_id} outside the configured set "
                f"{list(expected_vocabs)}")

        if not selected.concept_code:
            # An ETL cannot emit a source code it does not have.
            result.validation_flags.append("selected concept has no concept_code")

        with self._lock:
            previous = self._assigned.get(selected.concept_id)
            if previous is not None and previous != result.context.column_name:
                result.validation_flags.append(
                    f"concept {selected.concept_id} already mapped to column "
                    f"{previous!r} in this run")
            else:
                self._assigned[selected.concept_id] = result.context.column_name


def _expected_unit_types(concept_name: str) -> set[str]:
    """Plausible unit types for a concept, by keyword.

    Absence of a rule means "not checked" — the table covers common
    measurements only, and silence there must never read as a mismatch.
    """
    name = concept_name.lower()
    expected: set[str] = set()
    for keyword, types in resources.expected_unit_types().items():
        if keyword in name:
            expected.update(types)
    return expected


# ─────────────────────────────────────────────────────────────────────────


class MappingLogger:
    """Step 7.4 — the audit trail."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self.run_id: int | None = None

    def start_run(self, source_system: str | None = None,
                  note: str | None = None) -> int:
        self.run_id = self.db.execute_returning(
            """
            INSERT INTO pipeline.mapping_run
                   (source_system, vocabulary_version, config, created_by, note)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (source_system, self.db.vocabulary_version(),
             Jsonb(self.config.to_json()), self.config.created_by, note),
        )
        return self.run_id

    def finish_run(self) -> None:
        if self.run_id is None:
            return
        self.db.execute(
            "UPDATE pipeline.mapping_run SET finished_at = now() WHERE run_id = %s",
            (self.run_id,),
        )

    def log(self, result: MatchResult) -> int | None:
        if not self.config.log_to_db:
            return None

        ctx = result.context
        selected = result.selected
        payload = ctx.as_log_dict()

        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline.mapping_log (
                        run_id, column_name, column_description, source_table,
                        search_string, tokens, protected_phrases, units, unit_types,
                        domain_hint, numeric_flag, temporal_flag,
                        extracted_codes, abbreviations_expanded,
                        selected_concept_id, selected_concept_name,
                        selected_domain_id, selected_vocabulary_id,
                        selected_concept_code, confidence, status,
                        decided_at_stage, decision_reason, ambiguous,
                        validation_flags, replaced_from_concept_id,
                        elapsed_ms, created_by
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s
                    ) RETURNING mapping_id
                    """,
                    (
                        self.run_id, ctx.column_name, ctx.column_description,
                        ctx.source_table,
                        payload["search_string"], payload["tokens"],
                        payload["protected_phrases"], payload["units"],
                        payload["unit_types"],
                        payload["domain_hint"], payload["numeric_flag"],
                        payload["temporal_flag"],
                        Jsonb(payload["extracted_codes"]),
                        Jsonb(payload["abbreviations_expanded"]),
                        selected.concept_id if selected else None,
                        selected.concept_name if selected else None,
                        selected.domain_id if selected else None,
                        selected.vocabulary_id if selected else None,
                        selected.concept_code if selected else None,
                        round(result.confidence, 2), result.status,
                        result.decided_at_stage, result.decision_reason[:2000],
                        result.ambiguous,
                        result.validation_flags, result.replaced_from_concept_id,
                        round(result.elapsed_ms, 2), self.config.created_by,
                    ),
                )
                row = cur.fetchone()
                mapping_id = row[0] if row else None

                rows = candidate_rows(result.candidates,
                                      selected.concept_id if selected else None)
                if rows:
                    cur.executemany(
                        """
                        INSERT INTO pipeline.mapping_candidate (
                            mapping_id, rank, concept_id, concept_name,
                            domain_id, vocabulary_id, fts_score, jaccard_score,
                            fuzzy_score, embedding_score, composite_score,
                            boosts, selected
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (mapping_id, r["rank"], r["concept_id"], r["concept_name"],
                             r["domain_id"], r["vocabulary_id"],
                             _round(r["fts_score"]), _round(r["jaccard_score"]),
                             _round(r["fuzzy_score"]), _round(r["embedding_score"]),
                             _round(r["composite_score"]), Jsonb(r["boosts"]),
                             r["selected"])
                            for r in rows
                        ],
                    )

                # Step 9.3 feeds off this.
                if result.timings:
                    cur.executemany(
                        """
                        INSERT INTO pipeline.stage_timing
                               (run_id, mapping_id, stage, elapsed_ms,
                                candidates_in, candidates_out)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (self.run_id, mapping_id, t.stage, round(t.elapsed_ms, 2),
                             t.candidates_in, t.candidates_out)
                            for t in result.timings
                        ],
                    )

        result.mapping_id = mapping_id
        return mapping_id


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


# ─────────────────────────────────────────────────────────────────────────


class FeedbackStore:
    """Step 7.5 — turning human corrections into pipeline behaviour."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db

    def record_manual_mapping(self, column_name: str, concept_id: int,
                              column_description: str | None = None,
                              source_system: str | None = None,
                              created_by: str | None = None,
                              note: str | None = None) -> int:
        """Store a human decision so Stage 1 short-circuits to it next time.

        The key is the Stage 0 normalized form of the column, which is what
        Stage 1 looks up — so a correction generalizes across trivial spelling
        and punctuation differences in the source.
        """
        from .stage0_preprocess import Preprocessor   # local: avoids a cycle

        ctx = Preprocessor(self.config).run(column_name, column_description)
        return self.db.execute_returning(
            """
            INSERT INTO pipeline.manual_mapping
                   (lookup_key, column_name, column_description, source_system,
                    concept_id, created_by, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lookup_key, source_system) DO UPDATE
                SET concept_id = EXCLUDED.concept_id,
                    column_description = EXCLUDED.column_description,
                    active = true,
                    created_at = now(),
                    created_by = EXCLUDED.created_by,
                    note = EXCLUDED.note
            RETURNING manual_id
            """,
            (ctx.lookup_key, column_name, column_description, source_system,
             concept_id, created_by, note),
        )

    def pending_abbreviations(self, min_times_seen: int = 2) -> list[dict[str, Any]]:
        """Discovered abbreviations worth promoting into the dictionary."""
        return self.db.fetch_all(
            """
            SELECT abbreviation, expansion, confidence, times_seen, first_seen, last_seen
            FROM pipeline.discovered_abbreviation
            WHERE NOT promoted AND times_seen >= %s
            ORDER BY times_seen DESC, confidence DESC
            """,
            (min_times_seen,),
        )

    def promote_abbreviations(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Mark discoveries as promoted once they are in a *_local.json file."""
        pairs = list(pairs)
        if not pairs:
            return 0
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE pipeline.discovered_abbreviation
                SET promoted = true
                WHERE abbreviation = %s AND expansion = %s
                """,
                pairs,
            )
        return len(pairs)

    def set_weight(self, key: str, weight: float, updated_by: str | None = None,
                   note: str | None = None) -> None:
        """Persist a tuned weight (see PipelineConfig.apply_learned_weights)."""
        self.db.execute(
            """
            INSERT INTO pipeline.scoring_weight (weight_key, weight, updated_by, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (weight_key) DO UPDATE
                SET weight = EXCLUDED.weight,
                    updated_at = now(),
                    updated_by = EXCLUDED.updated_by,
                    note = EXCLUDED.note
            """,
            (key, weight, updated_by, note),
        )

    def suggest_weight_adjustments(self, run_id: int | None = None) -> list[dict[str, Any]]:
        """Which stage over- or under-rated the concept a human actually chose.

        For every column that has since been corrected by hand, compare the
        stage scores the pipeline gave the *corrected* concept against those it
        gave the concept it picked. A stage that consistently scored the wrong
        concept higher is a stage whose weight is too high.

        Returned as a suggestion, never applied automatically — a weight change
        silently altering every future mapping is not something a pipeline
        should do to itself.
        """
        return self.db.fetch_all(
            """
            WITH corrected AS (
                SELECT l.mapping_id, l.selected_concept_id AS picked_id,
                       m.concept_id AS human_id
                FROM pipeline.mapping_log l
                JOIN pipeline.manual_mapping m
                     ON m.lookup_key = public.pipeline_normalize(
                            l.column_name || ' ' || COALESCE(l.column_description, ''))
                    AND m.active
                WHERE l.selected_concept_id IS DISTINCT FROM m.concept_id
                  AND (%(run_id)s IS NULL OR l.run_id = %(run_id)s)
            )
            SELECT s.stage,
                   count(*)                                                   AS comparisons,
                   round(avg(picked_score.score - human_score.score), 2)      AS avg_overrating
            FROM corrected c
            JOIN LATERAL (VALUES
                ('fts'), ('jaccard'), ('fuzzy'), ('embedding')
            ) AS s(stage) ON TRUE
            JOIN pipeline.mapping_candidate picked
                 ON picked.mapping_id = c.mapping_id AND picked.concept_id = c.picked_id
            JOIN pipeline.mapping_candidate human
                 ON human.mapping_id = c.mapping_id AND human.concept_id = c.human_id
            CROSS JOIN LATERAL (
                SELECT CASE s.stage
                           WHEN 'fts' THEN picked.fts_score
                           WHEN 'jaccard' THEN picked.jaccard_score
                           WHEN 'fuzzy' THEN picked.fuzzy_score
                           ELSE picked.embedding_score END AS score
            ) picked_score(score)
            CROSS JOIN LATERAL (
                SELECT CASE s.stage
                           WHEN 'fts' THEN human.fts_score
                           WHEN 'jaccard' THEN human.jaccard_score
                           WHEN 'fuzzy' THEN human.fuzzy_score
                           ELSE human.embedding_score END AS score
            ) human_score(score)
            WHERE picked_score.score IS NOT NULL AND human_score.score IS NOT NULL
            GROUP BY s.stage
            ORDER BY avg_overrating DESC
            """,
            {"run_id": run_id},
        )
