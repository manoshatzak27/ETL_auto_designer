"""Orchestration: one source column, all the way through.

The funnel, and what survives each narrowing:

    Stage 0   preprocess               -> 1..N interpretations of the input
    Stage 1   exact / code             -> a hit exits at 100, or nothing
    Stage 2   full-text search         -> <= 30 candidates per interpretation
    Stage 3   IDF-weighted Jaccard     -> <= 15
    Stage 4   fuzzy                    -> <= 10
    Stage 5   embeddings               -> re-scores, eliminates nothing
    Stage 6   composite + decision     -> one winner, with a status
    Stage 7   validation + logging     -> the status, made honest

Stage 6.4's "attempt a broader search without domain filters" is implemented
here rather than in Stage 6, because retrying means re-entering Stages 2-5 —
which is orchestration, not scoring. It fires only when the first pass came
back unmapped *and* a domain hint was actually applied, since a retry with an
identical filter would just do the same work twice.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator, Sequence

from .config import PipelineConfig
from .db import Database, shared_database
from .models import Candidate, ContextBundle, MatchResult
from .stage0_preprocess import Preprocessor
from .stage1_exact import ExactMatcher
from .stage2_fts import FullTextSearcher
from .stage3_jaccard import IdfIndex, JaccardScorer
from .stage4_fuzzy import FuzzyScorer
from .stage5_embeddings import EmbeddingModel, EmbeddingScorer
from .stage6_composite import CompositeScorer, merge_candidates
from .stage7_validate import MappingLogger, Validator

log = logging.getLogger(__name__)


class ConceptMatcher:
    """The pipeline, assembled. Thread-safe: share one instance across workers."""

    def __init__(self, config: PipelineConfig | None = None,
                 db: Database | None = None,
                 embedding_model: EmbeddingModel | None = None) -> None:
        self.config = config or PipelineConfig.load()
        self.db = db or shared_database(self.config)

        # Step 7.5: weights tuned by the feedback loop outrank file defaults.
        try:
            self.config.apply_learned_weights(self.db.learned_weights())
        except Exception as exc:
            log.debug("no learned weights available: %s", exc)

        self.preprocessor = Preprocessor(self.config)
        self.idf = IdfIndex(self.db)

        self.stage1 = ExactMatcher(self.config, self.db)
        self.stage2 = FullTextSearcher(self.config, self.db)
        self.stage3 = JaccardScorer(self.config, self.db, self.preprocessor, self.idf)
        self.stage4 = FuzzyScorer(self.config, self.db, self.preprocessor)
        self.stage5 = EmbeddingScorer(self.config, self.db, embedding_model)
        self.stage6 = CompositeScorer(self.config, self.db)

        self.validator = Validator(self.config, self.db)
        self.logger = MappingLogger(self.config, self.db)

    # ────────────────────────────────────────────────────────────────────
    def match(
        self,
        column_name: str,
        column_description: str | None = None,
        source_table: str | None = None,
        source_system: str | None = None,
        corpus_stopwords: frozenset[str] = frozenset(),
        log_result: bool = True,
    ) -> MatchResult:
        started = time.perf_counter()

        with _timed() as elapsed:
            ctx = self.preprocessor.run(column_name, column_description,
                                        source_table, corpus_stopwords)
        result = MatchResult(context=ctx)
        result.add_timing("stage0_preprocess", elapsed(), candidates_out=len(ctx.variants))

        try:
            self._run_stages(ctx, result, source_system)
        except Exception as exc:                       # one bad column must not
            log.exception("matching failed for %r", column_name)   # kill a batch
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            result.decision_reason = result.error

        result.elapsed_ms = (time.perf_counter() - started) * 1000.0

        if log_result and self.config.log_to_db:
            try:
                self.logger.log(result)
            except Exception as exc:
                log.warning("could not log mapping for %r: %s", column_name, exc)
        return result

    # ────────────────────────────────────────────────────────────────────
    def _run_stages(self, ctx: ContextBundle, result: MatchResult,
                    source_system: str | None) -> None:
        # ── Stage 1 ─────────────────────────────────────────────────────
        with _timed() as elapsed:
            exact, reason, flags = self.stage1.run(ctx, source_system)
        result.add_timing("stage1_exact", elapsed(), candidates_out=1 if exact else 0)
        # An extracted-but-unresolvable code is worth logging whether or not
        # the column went on to match by name.
        result.validation_flags.extend(flags)

        if exact is not None:
            result.candidates = [exact]
            result.selected = exact
            result.confidence = 100.0
            result.status = "manual" if exact.source_stage == "stage1_manual" else "auto_accept"
            result.decided_at_stage = "stage1"
            result.decision_reason = reason
            # Even a deterministic hit goes through Stage 7 — a stored manual
            # mapping can still collide with another column's concept.
            self.validator.validate(result)
            return

        # ── Stages 2-6, once per interpretation ─────────────────────────
        ranked = self._score_all(ctx, result, ctx.eligible_domains, pass_label="")
        status, confidence, reason = self.stage6.decide(ranked)

        # ── Step 6.4 retry ──────────────────────────────────────────────
        if status == "unmapped" and ctx.domain_hint is not None:
            retry = self._score_all(ctx, result, (), pass_label="_retry")
            retry_status, retry_confidence, retry_reason = self.stage6.decide(retry)
            if retry_confidence > confidence:
                ranked, status = retry, retry_status
                confidence, reason = retry_confidence, retry_reason
                reason += " (domain filter dropped, Step 6.4 retry)"

        result.candidates = ranked
        result.decided_at_stage = "stage6"
        result.status = status
        result.confidence = confidence
        result.decision_reason = reason

        if ranked:
            selected, replaced_from = self.stage6.check_replacement(ranked[0])
            result.selected = selected
            result.replaced_from_concept_id = replaced_from
            if replaced_from is not None:
                result.validation_flags.append(
                    f"redirected from deprecated concept {replaced_from}")

        self.validator.validate(result)

    def _score_all(self, ctx: ContextBundle, result: MatchResult,
                   domains: Sequence[str], pass_label: str) -> list[Candidate]:
        """Run Stages 2-6 over every Stage 0 interpretation and merge."""
        groups: list[list[Candidate]] = []

        fts_total = jaccard_total = fuzzy_total = embed_total = 0.0
        fts_out = jaccard_out = fuzzy_out = embed_out = 0

        for variant in ctx.variants:
            with _timed() as elapsed:
                candidates = self.stage2.run(ctx, variant, domains)
            fts_total += elapsed()
            fts_out += len(candidates)
            if not candidates:
                continue

            with _timed() as elapsed:
                candidates = self.stage3.run(ctx, variant, candidates)
            jaccard_total += elapsed()
            jaccard_out += len(candidates)

            with _timed() as elapsed:
                candidates = self.stage4.run(ctx, variant, candidates)
            fuzzy_total += elapsed()
            fuzzy_out += len(candidates)

            with _timed() as elapsed:
                candidates = self.stage5.run(ctx, variant, candidates)
            embed_total += elapsed()
            embed_out += len(candidates)

            groups.append(self.stage6.score(ctx, candidates))

        result.add_timing(f"stage2_fts{pass_label}", fts_total, candidates_out=fts_out)
        result.add_timing(f"stage3_jaccard{pass_label}", jaccard_total,
                          candidates_in=fts_out, candidates_out=jaccard_out)
        result.add_timing(f"stage4_fuzzy{pass_label}", fuzzy_total,
                          candidates_in=jaccard_out, candidates_out=fuzzy_out)
        result.add_timing(f"stage5_embedding{pass_label}", embed_total,
                          candidates_in=fuzzy_out, candidates_out=embed_out)

        merged = merge_candidates(groups)
        result.add_timing(f"stage6_composite{pass_label}", 0.0,
                          candidates_in=embed_out, candidates_out=len(merged))
        return merged

    # ────────────────────────────────────────────────────────────────────
    def start_run(self, source_system: str | None = None,
                  note: str | None = None) -> int:
        self.validator.reset()
        return self.logger.start_run(source_system, note)

    def finish_run(self) -> None:
        self.stage4.flush_discoveries()      # Step 4.2, once per run
        self.logger.finish_run()

    def close(self) -> None:
        self.db.close()


@contextmanager
def _timed() -> Iterator["_Elapsed"]:
    started = time.perf_counter()
    holder = _Elapsed()
    try:
        yield holder
    finally:
        holder.value = (time.perf_counter() - started) * 1000.0


class _Elapsed:
    """Milliseconds, readable after the ``with`` block closes."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value
