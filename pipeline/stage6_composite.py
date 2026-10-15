"""Stage 6 — composite scoring and final selection.

Combines the four stage scores into one number, applies the tie-breakers, and
turns that number into a decision: accept, review, or unmapped.

The one design decision worth stating outright is how missing stage scores are
treated. A candidate can legitimately reach this point without a score from
every stage — embeddings may be switched off, or a candidate pulled in by the
Stage 5 widening rule never went through Stage 2. Scoring those as zero would
punish a candidate for evidence that was never gathered, so instead the
weights of the stages that did run are renormalized to sum to 1. A candidate
scoring 80 on three stages gets 80, not 64.
"""

from __future__ import annotations

from typing import Any, Iterable

from .config import PipelineConfig, domain_relation
from .db import Database
from .models import Candidate, ContextBundle


class CompositeScorer:
    """Stage 6."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db

    # ── Step 6.1 ────────────────────────────────────────────────────────
    def score(self, ctx: ContextBundle, candidates: list[Candidate]) -> list[Candidate]:
        for cand in candidates:
            cand.composite_score = self._composite(ctx, cand)

        ranked = sorted(candidates, key=lambda c: -c.composite_score)
        ranked = self._apply_hierarchy_preference(ranked)
        return sorted(ranked, key=lambda c: -c.composite_score)

    def _composite(self, ctx: ContextBundle, cand: Candidate) -> float:
        scores = cand.stage_scores()
        available = {name: value for name, value in scores.items() if value is not None}
        if not available:
            return 0.0

        total_weight = sum(self.config.weight_for(name) for name in available)
        if total_weight <= 0:
            return 0.0

        score = sum(
            value * self.config.weight_for(name) for name, value in available.items()
        ) / total_weight

        if len(available) < len(scores):
            cand.notes.append(
                "composite over " + ", ".join(sorted(available)) +
                " (weights renormalized)")

        # Step 6.1: final vocabulary and domain boosts, on top of the per-stage
        # ones. Deliberately smaller (5%) than the per-stage boosts, since this
        # compounds with them.
        vocab_bonus = self.config.vocab_multiplier(cand.vocabulary_id)
        if vocab_bonus > 1.0:
            score *= self.config.final_vocabulary_boost
            cand.boosts["final_vocabulary"] = self.config.final_vocabulary_boost

        if domain_relation(ctx.domain_hint, cand.domain_id) == "match":
            score *= self.config.final_domain_boost
            cand.boosts["final_domain"] = self.config.final_domain_boost

        # Step 6.2, second half: a broad classification concept is rarely the
        # right ETL target — "Clinical finding" is not a diagnosis.
        if cand.concept_class_id in self.config.broad_concept_classes:
            score *= self.config.broad_class_penalty
            cand.boosts["broad_class"] = self.config.broad_class_penalty

        return min(score, 100.0)

    # ── Step 6.2 ────────────────────────────────────────────────────────
    def _apply_hierarchy_preference(self, ranked: list[Candidate]) -> list[Candidate]:
        """Among near-ties, prefer the more specific (deeper) concept."""
        if len(ranked) < 2:
            return ranked

        top = ranked[0].composite_score
        contenders = [c for c in ranked
                      if top - c.composite_score <= self.config.hierarchy_tie_margin]
        if len(contenders) < 2:
            return ranked

        depths = self._depths([c.concept_id for c in contenders])
        if not depths:
            return ranked

        deepest = max(depths.values())
        if deepest <= 0:
            return ranked

        for cand in contenders:
            if depths.get(cand.concept_id, 0) == deepest:
                cand.composite_score = min(
                    cand.composite_score * self.config.hierarchy_depth_boost, 100.0)
                cand.boosts["hierarchy_depth"] = self.config.hierarchy_depth_boost
                cand.notes.append(f"hierarchy depth {deepest} (most specific of the tie)")
        return ranked

    def _depths(self, concept_ids: list[int]) -> dict[int, int]:
        """How far below the top of its hierarchy each concept sits.

        max(min_levels_of_separation) over all ancestors: the longest shortest
        path up, which is a workable proxy for specificity.
        """
        if not concept_ids:
            return {}
        rows = self.db.fetch_all(
            """
            SELECT a.descendant_concept_id AS concept_id,
                   max(a.min_levels_of_separation) AS depth
            FROM vocab.concept_ancestor a
            JOIN vocab.concept p ON p.concept_id = a.ancestor_concept_id
            WHERE a.descendant_concept_id = ANY(%s)
              AND p.standard_concept = 'S' AND p.invalid_reason IS NULL
            GROUP BY a.descendant_concept_id
            """,
            (concept_ids,),
        )
        return {r["concept_id"]: int(r["depth"] or 0) for r in rows}

    # ── Step 6.3 ────────────────────────────────────────────────────────
    def check_replacement(self, cand: Candidate) -> tuple[Candidate, int | None]:
        """Redirect a deprecated concept to its replacement.

        In practice the check always passes: candidates come from
        ``pipeline.concept_search``, which holds only standard, valid concepts
        even when ``vocab`` is the full Athena export, so a deprecated concept
        is never there to be selected. The lookup is kept because it is cheap
        and because it is the assertion that the invariant still holds.
        """
        row = self.db.fetch_one(
            """
            SELECT c.invalid_reason,
                   r.concept_id_2 AS replacement_id
            FROM vocab.concept c
            LEFT JOIN vocab.concept_relationship r
                   ON r.concept_id_1 = c.concept_id
                  AND r.relationship_id = 'Maps to'
                  AND r.invalid_reason IS NULL
                  AND r.concept_id_2 <> c.concept_id
            WHERE c.concept_id = %s
            LIMIT 1
            """,
            (cand.concept_id,),
        )
        if not row or row["invalid_reason"] is None:
            return cand, None
        if not row["replacement_id"]:
            cand.notes.append("selected concept is deprecated and has no replacement")
            return cand, None

        replacement = self.db.fetch_one(
            """
            SELECT concept_id, concept_name, domain_id, vocabulary_id,
                   concept_class_id, concept_code
            FROM vocab.concept WHERE concept_id = %s
              AND standard_concept = 'S' AND invalid_reason IS NULL
            """,
            (row["replacement_id"],),
        )
        if not replacement:
            return cand, None

        original_id = cand.concept_id
        redirected = Candidate(
            concept_id=replacement["concept_id"],
            concept_name=replacement["concept_name"],
            domain_id=replacement["domain_id"],
            vocabulary_id=replacement["vocabulary_id"],
            concept_class_id=replacement["concept_class_id"] or "",
            concept_code=replacement["concept_code"] or "",
            source_stage="stage6_replacement",
        )
        redirected.fts_score = cand.fts_score
        redirected.jaccard_score = cand.jaccard_score
        redirected.fuzzy_score = cand.fuzzy_score
        redirected.embedding_score = cand.embedding_score
        redirected.composite_score = cand.composite_score
        redirected.boosts = dict(cand.boosts)
        redirected.notes = [*cand.notes,
                            f"redirected from deprecated concept {original_id}"]
        return redirected, original_id

    # ── Step 6.4 ────────────────────────────────────────────────────────
    def decide(self, ranked: list[Candidate]) -> tuple[str, float, str]:
        """Map the top score onto accept / review / unmapped."""
        if not ranked:
            return "unmapped", 0.0, "no candidates found"

        best = ranked[0]
        score = best.composite_score

        if score >= self.config.auto_accept_threshold:
            return "auto_accept", score, (
                f"composite {score:.1f} >= {self.config.auto_accept_threshold:g}")
        if score >= self.config.review_threshold:
            return "review", score, (
                f"composite {score:.1f} in review band "
                f"[{self.config.review_threshold:g}, "
                f"{self.config.auto_accept_threshold:g})")
        return "unmapped", score, (
            f"composite {score:.1f} < {self.config.review_threshold:g}")


def merge_candidates(groups: Iterable[list[Candidate]]) -> list[Candidate]:
    """Fold per-variant candidate lists into one, keeping the best of each.

    Stage 0 can produce several interpretations of an ambiguous input, each
    scored independently. The same concept may surface under more than one; the
    interpretation that scored it highest is the one that stands.
    """
    best: dict[int, Candidate] = {}
    for group in groups:
        for cand in group:
            current = best.get(cand.concept_id)
            if current is None or cand.composite_score > current.composite_score:
                best[cand.concept_id] = cand
    return sorted(best.values(), key=lambda c: -c.composite_score)


def candidate_rows(candidates: list[Candidate], selected_id: int | None,
                   limit: int = 10) -> list[dict[str, Any]]:
    """Shape candidates for the Stage 7.4 ``mapping_candidate`` insert."""
    rows: list[dict[str, Any]] = []
    for rank, cand in enumerate(candidates[:limit], start=1):
        rows.append({
            "rank": rank,
            "concept_id": cand.concept_id,
            "concept_name": cand.concept_name,
            "domain_id": cand.domain_id,
            "vocabulary_id": cand.vocabulary_id,
            "fts_score": cand.fts_score,
            "jaccard_score": cand.jaccard_score,
            "fuzzy_score": cand.fuzzy_score,
            "embedding_score": cand.embedding_score,
            "composite_score": cand.composite_score,
            "boosts": {**cand.boosts, "_notes": cand.notes} if cand.notes else cand.boosts,
            "selected": cand.concept_id == selected_id,
        })
    return rows
