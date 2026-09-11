"""Stage 2 — full-text search with phrase boosting.

Runs against ``pipeline.concept_search``, the materialized view whose
``search_vector`` is ``setweight(name,'A') || setweight(synonyms,'B')``. That
weighting is what makes a hit in the concept name outrank the same hit in a
synonym without any extra bookkeeping here.

Recall vs. precision is handled with a ladder rather than a single query. A
strict query ("every protected phrase AND every token") has excellent
precision and dreadful recall; a loose one ("any token") is the reverse. The
ladder runs strict first and only descends while it is short of candidates, so
a well-specified column never pays for the loose query and a sparse one still
finds something. Candidates keep the rung they were found on, and a strict rung
carries a bonus — satisfying more of the query is real evidence, and it is
exactly the signal Step 2.3 asks for ("tokens appearing together in the same
order rank significantly higher").
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

from . import resources, text_utils
from .config import PipelineConfig
from .db import Database
from .models import Candidate, ContextBundle, SearchVariant

_RUNG_NAMES = ("all_terms", "phrases_and_any", "all_words_relaxed", "any_term")

# Score band per ladder rung: (floor, ceiling). A candidate's position inside
# its band comes from ts_rank_cd relative to the best rank that rung returned.
#
# Why banded and relative, rather than a direct rescale of ts_rank_cd:
# the raw value is not calibrated to anything. It answers "how densely do the
# query terms cover this document", which for a one-word query against a
# vocabulary full of that word saturates — every glucose concept scored a flat
# 100 and the stage discriminated nothing. What *is* calibrated is which rung a
# candidate reached: rung 0 means the concept contains every term the input
# asked for, which is a real, absolute statement about match quality. The band
# encodes that, and the relative rank orders within it.
#
# Note the ceiling on the last rung: a candidate that matched merely *some*
# term cannot reach the review threshold on FTS evidence alone.
_RUNG_BANDS = ((78.0, 100.0), (68.0, 90.0), (62.0, 85.0), (40.0, 65.0))

# Score a candidate that covers none of the query would get from coverage
# alone; full coverage reaches 100. Interpolated linearly in between.
_COVERAGE_FLOOR = 40.0

_BASE_SELECT = """
    SELECT cs.concept_id,
           cs.concept_name,
           cs.domain_id,
           cs.vocabulary_id,
           cs.concept_class_id,
           cs.concept_code,
           cs.synonyms,
           -- Normalization 16|32: divide by 1 + log(unique words), then map
           -- into [0,1). Without the 16, cover density rewards a concept for
           -- repeating a query term, so "Glucose measurements in range out of
           -- Total glucose" outranks "Glucose [Mass/volume] in Serum or
           -- Plasma" purely for saying glucose twice.
           ts_rank_cd(cs.search_vector, q.query, 16|32) AS rank,
           -- Fraction of the query's lexemes this concept actually contains.
           -- Needed because the rung alone cannot distinguish "matched 2 of 3
           -- terms" from "matched 6 of 15": both fall to the loosest rung, but
           -- they are not remotely the same quality of evidence.
           -- Counted per *group*, not per lexeme, so that an equivalence
           -- (sample|specimen) counts once whichever member matched, and a
           -- concept containing both is not credited twice.
           CASE WHEN cardinality(q.groups) = 0 THEN 0::float8
                ELSE (SELECT count(*) FROM unnest(q.groups) AS g
                      WHERE EXISTS (
                          SELECT 1
                          FROM unnest(string_to_array(g, ' ')) AS lex
                          WHERE lex = ANY(tsvector_to_array(cs.search_vector))
                      ))::float8 / cardinality(q.groups)
           END AS coverage,
           -- Step 2.3b: the mirror of coverage. Coverage is recall — how much
           -- of the query the concept accounts for. This is precision — how
           -- much of the concept NAME the query accounts for. Without it,
           -- "Burial occupation" and "Occupation [Type]" are indistinguishable
           -- for the column "What is your profession?": both contain the query
           -- word once, but one is a specific occupation (a value) and the
           -- other names the variable being asked for.
           --
           -- Generic words are excluded from the denominator, so "[Type]",
           -- "- finding", "code" and "value" do not count as unrelated
           -- content. They are exactly the words OMOP appends when naming a
           -- variable rather than a value.
           CASE WHEN cardinality(n.content) = 0 THEN 1.0::float8
                ELSE cardinality(ARRAY(SELECT unnest(n.content)
                                       INTERSECT
                                       SELECT unnest(q.lexemes)))::float8
                     / cardinality(n.content)
           END AS specificity
    FROM pipeline.concept_search cs
    CROSS JOIN (
        SELECT ({expr})::tsquery AS query,
               (SELECT array_agg(array_to_string(
                           tsvector_to_array(to_tsvector('english', g)), ' '))
                  FROM unnest(%(group_texts)s::text[]) AS g
                 WHERE to_tsvector('english', g) <> ''::tsvector) AS groups,
               tsvector_to_array(to_tsvector('english', %(query_all)s)) AS lexemes,
               (SELECT array_agg((ts_lexize('english_stem', w))[1])
                  FROM unnest(%(generic_words)s::text[]) AS w
                 WHERE ts_lexize('english_stem', w) <> '{{}}'::text[]) AS generic
    ) q
    CROSS JOIN LATERAL (
        SELECT ARRAY(SELECT unnest(cs.name_lexemes)
                     EXCEPT
                     SELECT unnest(q.generic)) AS content
    ) n
    WHERE cs.search_vector @@ q.query
      {domain_filter}
    -- Length is the tie-break, and there are a lot of ties: every
    -- "<qualifier> systolic blood pressure" scores exactly what plain
    -- "Systolic blood pressure" does, and without this the plain concept can
    -- be pushed out of the shortlist by its own qualified variants. Shorter
    -- means fewer words the query did not ask for.
    ORDER BY rank DESC, length(cs.concept_name), cs.concept_id
    LIMIT %(limit)s
"""


@lru_cache(maxsize=1)
def _generic_words() -> list[str]:
    """Words that do not count as "unrelated" in the specificity measure.

    OMOP names a variable by appending exactly these to the subject:
    "Occupation [Type]", "Type of job - finding", "Employment status".
    Counting them as unrelated content is what buried the concept naming
    the variable beneath several hundred concepts naming its values.
    """
    return sorted(resources.stopwords().get("generic", ()))


def _expand(term: str) -> list[str]:
    """A query term plus every word it may be swapped for, itself first.

    Multi-word terms are left alone: the equivalence table is a word table, and
    substituting inside a phrase ("blood pressure" -> "blood tension") is not
    something a flat word list can be trusted to do.
    """
    if " " in term:
        return [term]
    group = resources.equivalents().get(term.lower())
    if not group:
        return [term]
    return [term] + [word for word in group if word != term.lower()]


def _coverage_groups(phrases: list[str], plain: list[str]) -> list[str]:
    """One coverage slot per query *word*, with equivalents sharing a slot.

    Coverage is "how much of the query did this concept account for", and the
    unit has to stay the word. Counting a protected phrase as a single slot
    instead of one per word silently reweights the whole query: for the input
    "egfr / Estimated glomerular filtration rate" it made the four words of the
    phrase worth as much as the single word "receptor", and the wrong
    expansion of the acronym — "Epidermal growth factor receptor" — overtook
    the right concept.

    So a phrase contributes its words individually, exactly as the old
    lexeme-based count did, and only an equivalence group collapses several
    words into one slot, because only one of them was ever asked for.
    """
    groups = [[word] for phrase in phrases for word in phrase.split()]
    for term in plain:
        if " " in term:
            groups.extend([word] for word in term.split())
        else:
            groups.append(_expand(term))
    return [" ".join(group) for group in groups]


def _or_group(size: int) -> str:
    """SQL skeleton for one query term: a bare term, or its members ORed."""
    one = "plainto_tsquery('english', %s)"
    return one if size == 1 else "(" + " || ".join([one] * size) + ")"


class FullTextSearcher:
    """Stage 2."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db

    # ────────────────────────────────────────────────────────────────────
    def run(
        self,
        ctx: ContextBundle,
        variant: SearchVariant,
        domains: Sequence[str] | None = None,
    ) -> list[Candidate]:
        """Return up to ``fts_candidate_limit`` candidates for one variant.

        ``domains`` overrides Step 2.1's domain pre-filter; the Stage 6.4
        retry passes an empty sequence to search every domain. Either way
        ``config.restrict_domains`` is applied on top — that is the whole point
        of it being a restriction rather than a hint, and it is why the 6.4
        retry cannot widen past it.
        """
        if domains is None:
            domains = ctx.eligible_domains
        domains = self.config.domain_filter(domains)

        phrases = [text_utils.split_phrase(p) for p in variant.protected_phrases]
        plain = [text_utils.split_phrase(t) for t in variant.tokens
                 if t not in set(variant.protected_phrases)
                 and text_utils.split_phrase(t) not in phrases]
        if not phrases and not plain:
            return []

        group_texts = _coverage_groups(phrases, plain)

        found: dict[int, Candidate] = {}
        for rung, (expr, params) in enumerate(self._ladder(phrases, plain)):
            if expr is None:
                continue
            rows = self._query(expr, params, domains, group_texts)
            if not rows:
                continue
            best_rank = max((float(r["rank"] or 0.0) for r in rows), default=0.0)
            for row in rows:
                self._absorb(found, row, ctx, variant, rung, best_rank)
            if len(found) >= self.config.fts_candidate_limit:
                break

        if not found:
            rows = self._trigram_fallback(variant, domains)
            best_rank = max((float(r["rank"] or 0.0) for r in rows), default=0.0)
            for row in rows:
                self._absorb(found, row, ctx, variant, 2, best_rank, trigram=True)

        ranked = sorted(found.values(), key=lambda c: -(c.fts_score or 0.0))
        return ranked[: self.config.fts_candidate_limit]

    # ── query construction ──────────────────────────────────────────────
    def _ladder(
        self, phrases: list[str], plain: list[str]
    ) -> list[tuple[str | None, list[str]]]:
        """Four tsquery expressions, strictest first.

        Text values stay parameters — only the operator skeleton is built as
        SQL — so nothing here is injectable.

        Rung 2 is the one that needs justifying. Step 2.2 is emphatic that a
        protected phrase must match contiguously, and it is right for the case
        it names: "heart" and "failure" in different halves of a concept name
        do not mean heart failure. But OMOP frequently writes the same idea
        with the words apart — "serum creatinine" is filed as "Creatinine
        measurement, serum" — and under contiguity-only that correct concept
        drops to the any-term rung and loses to whatever *does* contain the
        literal phrase, which in practice is a survey instrument that quotes it.
        So rung 2 requires every word to be present but drops the adjacency
        constraint, and sits in a band below the contiguous rungs and above the
        any-term one. Precision order is preserved; the recall hole is not.
        """
        phrase_terms = ["phraseto_tsquery('english', %s)"] * len(phrases)
        # One OR group per plain term, over that term and its equivalents.
        # A single-member group renders as the bare term, so a query with
        # no equivalences produces byte-identical SQL to before.
        plain_groups = [_expand(t) for t in plain]
        plain_terms = [_or_group(len(g)) for g in plain_groups]
        plain = [word for group in plain_groups for word in group]
        # A phrase, relaxed: its words ANDed together with no adjacency.
        # plainto_tsquery already ANDs the words of a multi-word string.
        relaxed_terms = ["plainto_tsquery('english', %s)"] * len(phrases)

        rungs: list[tuple[str | None, list[str]]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()

        def add(expr: str | None, values: list[str]) -> None:
            # Without phrases, several rungs collapse to the same query; running
            # it twice would only re-do the work at a lower band.
            if expr is None:
                rungs.append((None, []))
                return
            key = (expr, tuple(values))
            rungs.append((None, []) if key in seen else (expr, values))
            seen.add(key)

        # 0. every phrase contiguous, every plain token present
        add(" && ".join(phrase_terms + plain_terms) or None, phrases + plain)

        # 1. every phrase contiguous, at least one plain token
        if phrase_terms and plain_terms:
            add(" && ".join(phrase_terms) + " && (" + " || ".join(plain_terms) + ")",
                phrases + plain)
        elif phrase_terms:
            add(" && ".join(phrase_terms), list(phrases))
        else:
            add(None, [])

        # 2. every word present, adjacency dropped
        add(" && ".join(relaxed_terms + plain_terms) or None, phrases + plain)

        # 3. anything at all. Phrases stay phrases here, so "heart failure"
        #    still cannot be satisfied by a stray "heart".
        loose = phrase_terms + plain_terms
        add(" || ".join(loose) or None, phrases + plain)

        return rungs

    def _query(self, expr: str, values: list[str], domains: Sequence[str],
               group_texts: list[str]) -> list[dict[str, Any]]:
        domain_filter = "AND cs.domain_id = ANY(%(domains)s)" if domains else ""

        # The expression carries positional %s placeholders while the rest of
        # the statement uses named ones, which psycopg will not mix. Number the
        # term values instead and rewrite the skeleton to match.
        named_expr = expr
        params: dict[str, Any] = {
            "limit": self.config.fts_candidate_limit * 2,
            "domains": list(domains),
            "group_texts": group_texts,
            "query_all": " ".join(group_texts),
            "generic_words": _generic_words(),
        }
        for index, value in enumerate(values):
            key = f"t{index}"
            named_expr = named_expr.replace("%s", f"%({key})s", 1)
            params[key] = value
        sql = _BASE_SELECT.format(expr=named_expr, domain_filter=domain_filter)
        return self.db.fetch_all(sql, params)

    # ── recall net ──────────────────────────────────────────────────────
    def _trigram_fallback(self, variant: SearchVariant,
                          domains: Sequence[str]) -> list[dict[str, Any]]:
        """Used only when FTS matched nothing at all.

        That happens when every input token stems to something no concept
        contains — in practice, a typo. The GIN trigram index on
        concept_name_norm makes this a lookup rather than a 3.5M-row scan.
        """
        probe = text_utils.normalize_for_exact_match(variant.search_string)
        if not probe:
            return []
        domain_filter = "AND cs.domain_id = ANY(%(domains)s)" if domains else ""
        return self.db.fetch_all(
            f"""
            SELECT cs.concept_id, cs.concept_name, cs.domain_id, cs.vocabulary_id,
                   cs.concept_class_id, cs.concept_code, cs.synonyms,
                   similarity(cs.concept_name_norm, %(probe)s) AS rank
            FROM pipeline.concept_search cs
            WHERE cs.concept_name_norm %% %(probe)s
              {domain_filter}
            ORDER BY rank DESC
            LIMIT %(limit)s
            """,
            {"probe": probe, "domains": list(domains),
             "limit": self.config.trigram_fallback_limit},
        )

    # ── scoring ─────────────────────────────────────────────────────────
    def _absorb(self, found: dict[int, Candidate], row: dict[str, Any],
                ctx: ContextBundle, variant: SearchVariant,
                rung: int, best_rank: float, trigram: bool = False) -> None:
        raw = float(row["rank"] or 0.0)

        if trigram:
            if raw < self.config.trigram_fallback_threshold:
                return
            score = raw * 100.0
        else:
            # Step 2.4's threshold, read relative to the best rank this rung
            # returned rather than as an absolute ts_rank_cd value — the raw
            # scale shifts with query length and term rarity, so a fixed cutoff
            # keeps everything for one query and nothing for the next.
            if best_rank <= 0.0 or raw < self.config.fts_rank_threshold * best_rank:
                return
            floor, ceiling = _RUNG_BANDS[rung]
            # Raise the ceiling for a candidate that covers a large share of a
            # long query. Without this the bands are a trap for verbose input:
            # a 15-token questionnaire item can never satisfy "every term", so
            # every candidate lands on the loosest rung and is capped at 65 —
            # below the review threshold — no matter how good the match is.
            # Coverage says how much of the query was actually accounted for,
            # which is the evidence the rung was standing in for.
            coverage = float(row.get("coverage") or 0.0)
            ceiling = max(ceiling, _COVERAGE_FLOOR
                          + (100.0 - _COVERAGE_FLOOR) * coverage)
            floor = min(floor, ceiling * 0.8)
            score = floor + (ceiling - floor) * (raw / best_rank)
            # Step 2.3b: discount a concept whose name is mostly words the
            # query never asked for. Bounded below by _SPECIFICITY_FLOOR so
            # this re-orders within a rung rather than overriding it — a
            # concept that satisfies every term is still better evidence
            # than a short one that satisfies one.
            floor_ = self.config.specificity_floor
            specificity = float(row.get("specificity") or 0.0)
            score *= floor_ + (1.0 - floor_) * specificity

        boosts: dict[str, float] = {"fts_rung": float(rung)}

        # Step 2.3: vocabulary preference.
        vocab_bonus = self.config.vocab_multiplier(row["vocabulary_id"])
        if vocab_bonus != 1.0:
            boosts["vocabulary"] = vocab_bonus
            score *= vocab_bonus

        # Step 0.3 said units imply LOINC; honour that here rather than
        # letting it silently do nothing.
        if ctx.vocabulary_hint and row["vocabulary_id"] == ctx.vocabulary_hint:
            boosts["vocabulary_hint"] = 1.05
            score *= 1.05

        score = min(score, 100.0)

        existing = found.get(row["concept_id"])
        if existing is not None and (existing.fts_score or 0.0) >= score:
            return

        candidate = Candidate(
            concept_id=row["concept_id"],
            concept_name=row["concept_name"],
            domain_id=row["domain_id"],
            vocabulary_id=row["vocabulary_id"],
            concept_class_id=row.get("concept_class_id") or "",
            concept_code=row.get("concept_code") or "",
            synonyms=tuple(row.get("synonyms") or ()),
            raw_rank=raw,
            variant_label=variant.label,
            source_stage="stage2_trigram" if trigram else f"stage2_{_RUNG_NAMES[rung]}",
        )
        candidate.fts_score = score
        candidate.boosts.update(boosts)
        found[row["concept_id"]] = candidate
