"""Shared domain and vocabulary adjustments for Stages 3, 4 and 5.

All three stages apply the same two multipliers (Steps 3.3 and 3.4, repeated by
Stages 4 and 5), so they are defined once here.

The one piece of judgement in this module: a multiplier is skipped when it
would apply *equally* to every candidate in the set. Multiplying a whole
shortlist by 1.10 cannot change its order — but it can push most of it into the
0-100 cap, at which point the ordering is destroyed rather than preserved. That
is not hypothetical: Step 2.1 pre-filters to a single domain, so by Stage 3
every surviving candidate usually shares it, and the domain boost was flattening
whole shortlists to a uniform 100.

So the rule is: apply an adjustment when it discriminates, skip it when it is a
constant. The ranking is identical either way; only the saturation differs.
"""

from __future__ import annotations

from typing import Callable, Sequence

from .config import PipelineConfig, domain_relation
from .models import Candidate, ContextBundle

Adjuster = Callable[[Candidate, float], float]


def make_adjuster(config: PipelineConfig, ctx: ContextBundle,
                  candidates: Sequence[Candidate]) -> Adjuster:
    """Build the per-stage score adjustment for one candidate set."""
    domains = {c.domain_id for c in candidates}
    vocabularies = {c.vocabulary_id for c in candidates}

    # A single distinct value means the multiplier is a constant factor.
    discriminates_domain = len(domains) > 1 and ctx.domain_hint is not None
    discriminates_vocab = len({config.vocab_multiplier(v) for v in vocabularies}) > 1

    def adjust(cand: Candidate, score: float) -> float:
        if discriminates_domain:
            relation = domain_relation(ctx.domain_hint, cand.domain_id)
            if relation == "match":
                score *= config.domain_match_boost
                cand.boosts["domain"] = config.domain_match_boost
            elif relation == "wrong":
                score *= config.domain_mismatch_penalty
                cand.boosts["domain"] = config.domain_mismatch_penalty

        if discriminates_vocab:
            multiplier = config.vocab_multiplier(cand.vocabulary_id)
            if multiplier != 1.0:
                score *= multiplier
                cand.boosts["vocabulary"] = multiplier

        return min(score, 100.0)

    return adjust
