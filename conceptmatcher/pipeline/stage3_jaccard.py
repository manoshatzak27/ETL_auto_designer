"""Stage 3 — token-set overlap with IDF weighting.

Plain Jaccard treats every token as equally informative, which is wrong in
this vocabulary: "level", "count" and "measurement" appear in hundreds of
thousands of concept names, while "ventricular" or "urobilinogen" appear in a
handful. Weighting the intersection and union by inverse document frequency
means the rare, meaning-bearing tokens decide the score and the filler cannot
drown them out.

Document frequencies come from ``pipeline.token_idf``, built once by
``sql/13_pipeline_idf.sql`` over the whole corpus.

One wrinkle worth knowing about: that table is keyed by Postgres *Snowball
stems* ("measur", "solut"), because it was built from the same tsvectors Stage
2 searches. The tokens on this side are *lemmas* ("measurement", "solution").
The two never line up by string equality, so :class:`IdfIndex` stems through
``ts_lexize('english_stem', ...)`` in the database before looking anything up,
and caches the result per process.
"""

from __future__ import annotations

import math
import threading
from typing import Iterable, Sequence

from . import resources, text_utils
from .config import PipelineConfig
from .db import Database
from .models import Candidate, ContextBundle, SearchVariant
from .scoring import make_adjuster
from .stage0_preprocess import Preprocessor


class IdfIndex:
    """Corpus token weights, cached across columns and threads (Step 8.2)."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._idf: dict[str, float] = {}
        self._stem: dict[str, str] = {}
        self._total: float | None = None

    @property
    def total_concepts(self) -> float:
        if self._total is None:
            with self._lock:
                value = self.db.fetch_value(
                    "SELECT stat_value FROM pipeline.corpus_stats "
                    "WHERE stat_key = 'total_concepts'")
                self._total = float(value or 1.0)
        return self._total

    def weights(self, tokens: Iterable[str]) -> dict[str, float]:
        """IDF weight per token, fetching and caching anything unseen."""
        wanted = {t for t in tokens if t}
        missing = [t for t in wanted if t not in self._idf]
        if missing:
            self._load(missing)
        return {t: self._idf.get(t, self._max_idf()) for t in wanted}

    def prime(self, tokens: Iterable[str]) -> None:
        """Load stems and document frequencies for these tokens in one query."""
        missing = [t for t in {t for t in tokens if t} if t not in self._stem]
        if missing:
            self._load(missing)

    def stem(self, token: str) -> str:
        """The Snowball stem Postgres would produce, for the same token.

        Stage 2 searches a tsvector, so Postgres has already folded
        started/start/starting onto `start` and smoking/smoke/smoked onto
        `smoke` before a candidate is ever returned. The Python side lemmatizes
        instead, and the built-in rule-based lemmatizer is deliberately
        conservative: it de-pluralizes nouns but leaves verb inflection alone.

        The result was an asymmetry rather than a difference of opinion. For
        the column "How old were you when you started smoking?" the concept
        "At which age did you start smoking [PhenX]" reached Stage 3 precisely
        because Postgres matched started/start — and Stage 3 then scored it as
        though the two words were unrelated, penalizing it for a difference
        Stage 2 had already resolved.

        Stemming here rather than in the lemmatizer keeps the change where the
        comparison happens: `token_idf` is itself keyed by these stems, so this
        also makes the weight lookups exact instead of approximate. Falls back
        to the token unchanged when it has not been primed.
        """
        if "_" not in token:
            return self._stem.get(token, token)
        parts = token.split("_")
        return "_".join(self._stem.get(p, p) for p in parts)

    # ────────────────────────────────────────────────────────────────────
    def _max_idf(self) -> float:
        """Weight for a token the corpus has never seen: maximally rare."""
        return math.log(1.0 + self.total_concepts)

    def _load(self, tokens: Sequence[str]) -> None:
        # A joined phrase ("heart_failure") has no stem of its own. Its
        # document frequency is bounded above by that of its rarest component
        # — a phrase cannot occur more often than its least common word — so
        # the components are looked up and the minimum is taken.
        lookup: dict[str, list[str]] = {}
        for token in tokens:
            lookup[token] = text_utils.split_phrase(token).split()

        words = sorted({w for parts in lookup.values() for w in parts})
        if not words:
            return

        rows = self.db.fetch_all(
            """
            SELECT w.word,
                   COALESCE(i.doc_freq, 1) AS doc_freq,
                   -- english_stem returns an empty array (not NULL) for a word
                   -- it recognizes as a stopword, and {word} for one it simply
                   -- does not know. The two must not be conflated: see below.
                   cardinality(s.lexemes) = 0 AS is_stopword,
                   s.lexemes[1] AS stem
            FROM unnest(%s::text[]) AS w(word)
            LEFT JOIN LATERAL (
                SELECT ts_lexize('english_stem', w.word) AS lexemes
            ) s ON TRUE
            LEFT JOIN pipeline.token_idf i ON i.token = s.lexemes[1]
            """,
            (words,),
        )
        doc_freq = {r["word"]: float(r["doc_freq"]) for r in rows}
        stopword = {r["word"]: bool(r["is_stopword"]) for r in rows}
        stems = {r["word"]: (r["stem"] or r["word"]) for r in rows}

        total = self.total_concepts
        with self._lock:
            self._stem.update(stems)
            for token, parts in lookup.items():
                # A stopword is absent from token_idf because it was never
                # indexed, not because it is rare. Falling through to the
                # unseen-word weight made it the *most* discriminating token in
                # the query: "What is your profession?" scored 'your' at 14.38
                # against 'profession' at 12.03, so Stage 3 ranked candidates
                # mainly on a word Postgres had already thrown away.
                content = [p for p in parts if not stopword.get(p, False)]
                if not content:
                    self._idf[token] = 0.0
                    continue
                freqs = [doc_freq.get(p, 1.0) for p in content] or [1.0]
                df = min(freqs)
                # Smoothed IDF: strictly decreasing in df, never zero or
                # negative even for a token present in every concept.
                value = math.log(1.0 + total / (1.0 + df))
                self._idf[token] = value
                # Also key it by the stemmed form: the comparison below
                # canonicalizes to stems, and without this every canonical
                # token would miss the cache and trigger a second query.
                stemmed = "_".join(stems.get(p, p) for p in parts)
                self._idf.setdefault(stemmed, value)


class JaccardScorer:
    """Stage 3."""

    def __init__(self, config: PipelineConfig, db: Database,
                 preprocessor: Preprocessor, idf: IdfIndex) -> None:
        self.config = config
        self.db = db
        self.pre = preprocessor
        self.idf = idf

    # ────────────────────────────────────────────────────────────────────
    def run(self, ctx: ContextBundle, variant: SearchVariant,
            candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []

        # Raw tokens first, so every word can be stemmed in one round trip
        # before any comparison happens.
        raw_input = list(variant.tokens)
        raw_candidates: dict[int, tuple[tuple[str, ...], list[tuple[str, ...]]]] = {}
        raw_all: set[str] = set(raw_input)
        for cand in candidates:
            name_tokens = self.pre.concept_tokens(cand.concept_name)
            synonym_tokens = [self.pre.concept_tokens(s)
                              for s in cand.synonyms[:12]]
            raw_candidates[cand.concept_id] = (name_tokens, synonym_tokens)
            raw_all.update(name_tokens)
            for tokens in synonym_tokens:
                raw_all.update(tokens)

        # Equivalence first, then the Snowball stem Stage 2 already applied:
        # 'profession' -> 'employment' -> 'employ' on both sides.
        if self.config.stem_tokens:
            self.idf.prime(_equivalence_inputs(raw_all)
                           if self.config.equivalence_folding else raw_all)
        use_equivalents = self.config.equivalence_folding

        use_stems = self.config.stem_tokens

        def fold(token: str) -> str:
            if use_equivalents:
                token = _canonical(token)
            return self.idf.stem(token) if use_stems else token

        input_tokens = {fold(t) for t in raw_input}

        # Step 3.1: token sets for every candidate, name and synonyms alike.
        candidate_tokens: dict[int, tuple[tuple[str, ...], list[tuple[str, ...]]]] = {}
        vocabulary: set[str] = set(input_tokens)
        for cand in candidates:
            raw_name, raw_synonyms = raw_candidates[cand.concept_id]
            name_tokens = tuple(fold(t) for t in raw_name)
            synonym_tokens = [tuple(fold(t) for t in tokens)
                              for tokens in raw_synonyms]
            candidate_tokens[cand.concept_id] = (name_tokens, synonym_tokens)
            vocabulary.update(name_tokens)
            for tokens in synonym_tokens:
                vocabulary.update(tokens)

        # One batched IDF fetch for every token in play.
        weights = self.idf.weights(vocabulary)
        adjust = make_adjuster(self.config, ctx, candidates)

        for cand in candidates:
            name_tokens, synonym_tokens = candidate_tokens[cand.concept_id]

            best = _weighted_jaccard(input_tokens, set(name_tokens), weights)
            matched_on = "name"
            # A concept whose synonym matches better than its name is still a
            # good match — Stage 5.3 makes the same allowance for embeddings.
            for tokens in synonym_tokens:
                score = _weighted_jaccard(input_tokens, set(tokens), weights)
                if score > best:
                    best, matched_on = score, "synonym"

            if matched_on == "synonym":
                cand.notes.append("stage3 matched on synonym")

            # Steps 3.3 and 3.4: domain and vocabulary adjustments.
            cand.jaccard_score = adjust(cand, best * 100.0)

        ranked = sorted(candidates, key=lambda c: -(c.jaccard_score or 0.0))
        return ranked[: self.config.jaccard_keep]


def _equivalence_inputs(tokens: Iterable[str]) -> set[str]:
    """Every token plus its equivalence representative, for one priming query.

    Both forms need a stem: the raw token because a candidate may carry it
    verbatim, and the representative because that is what the comparison
    actually compares on.
    """
    folded: set[str] = set()
    for token in tokens:
        folded.add(token)
        folded.add(_canonical(token))
    return folded


def _canonical(token: str) -> str:
    """Fold a token onto one representative of its equivalence group.

    Stage 2 expands "profession" into (profession|occupation|...) on the query
    side, but Stage 3 compares token *sets*: without the same folding,
    {profession} and {occupation, type} intersect in nothing and every
    candidate Stage 2 found through an equivalence scores a flat 0.0 — a
    quarter of the composite weight contributing nothing while still being
    averaged in.

    Multi-word tokens are folded word by word, so "manual_job" and
    "manual_occupation" meet. Stage 4 is deliberately NOT folded: it is a
    string-similarity stage, and making it equivalence-aware would turn a
    typo detector into a second semantic stage.
    """
    parts = token.split("_")
    folded = [resources.equivalents().get(p, (p,))[0] for p in parts]
    return "_".join(folded)


def _weighted_jaccard(left: set[str], right: set[str],
                      weights: dict[str, float]) -> float:
    """(sum of weights in intersection) / (sum of weights in union)."""
    if not left or not right:
        return 0.0
    intersection = left & right
    union = left | right
    numerator = sum(weights.get(t, 0.0) for t in intersection)
    denominator = sum(weights.get(t, 0.0) for t in union)
    if denominator <= 0.0:
        return 0.0
    return numerator / denominator
