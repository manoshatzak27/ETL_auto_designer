"""Stage 5 — semantic similarity via sentence embeddings.

The stage that catches matches sharing no literal tokens at all: "LVEF" against
"left ventricular ejection fraction", "sugar level" against "Glucose".

Scope, straight from Step 5.2: embeddings are computed for the *shortlist*, not
the vocabulary. Embedding 3.5M concepts would take hours on CPU and buy nothing
— by the time a concept reaches this stage, four earlier stages have already
agreed it is plausible. The only exception is the widening rule: when Stage 4
hands over fewer than ``embedding_widen_below`` candidates, a bounded set of
domain-matched concepts is pulled in and embedded so the stage has something to
work with.

Concept-name vectors are cached in ``pipeline.concept_embedding`` as raw
float32 bytes (4 bytes per dimension, so a 384-dim vector is 1.5kB), keyed by
model and by a hash of the text. The hash is what lets Step 8.3 invalidate only
the concepts whose names actually changed after a vocabulary refresh.

The whole stage is optional. If ``sentence-transformers`` is not installed, or
the model cannot be downloaded, ``embedding_score`` stays ``None`` and Stage 6
redistributes its 20% weight across the stages that did run — rather than
scoring every candidate zero, which would be a lie about the evidence.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Sequence

import numpy as np

from . import text_utils
from .config import PipelineConfig
from .db import Database
from .models import Candidate, ContextBundle, SearchVariant
from .scoring import make_adjuster

log = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class EmbeddingModel:
    """Lazy wrapper around sentence-transformers.

    Loading is deferred to the first encode so that importing the pipeline,
    running ``--help``, or doing a run with embeddings disabled never pays for
    a model load.
    """

    def __init__(self, model_name: str, batch_size: int = 64,
                 query_prefix: str = "", document_prefix: str = "") -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self._model: Any = None
        self._unavailable_reason: str | None = None
        self._lock = threading.Lock()

    @property
    def cache_key(self) -> str:
        """Value stored in ``pipeline.concept_embedding.model``.

        The document prefix is folded in, because changing it changes what the
        stored vectors mean while leaving the model name identical — cached
        vectors would otherwise be silently reused against a different encoding.
        The concept-name hash cannot carry this: ``refresh_derived_structures``
        recomputes that hash in SQL, which knows nothing about prefixes.
        """
        if not self.document_prefix:
            return self.model_name
        digest = hashlib.sha256(self.document_prefix.encode("utf-8")).hexdigest()[:8]
        return f"{self.model_name}#doc:{digest}"

    @property
    def available(self) -> bool:
        return self._load() is not None

    @property
    def unavailable_reason(self) -> str | None:
        self._load()
        return self._unavailable_reason

    def _load(self) -> Any:
        if self._model is not None or self._unavailable_reason is not None:
            return self._model
        with self._lock:
            if self._model is not None or self._unavailable_reason is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                self._unavailable_reason = (
                    "sentence-transformers is not installed "
                    "(pip install -r requirements-embeddings.txt)")
                log.warning("Stage 5 disabled: %s", self._unavailable_reason)
            except Exception as exc:
                self._unavailable_reason = f"could not load {self.model_name!r}: {exc}"
                log.warning("Stage 5 disabled: %s", self._unavailable_reason)
        return self._model

    @property
    def dimension(self) -> int:
        model = self._load()
        if model is None:
            return 0
        # sentence-transformers 6 renamed this; support both spellings so the
        # pipeline is not pinned to one release of the library.
        getter = (getattr(model, "get_embedding_dimension", None)
                  or getattr(model, "get_sentence_embedding_dimension", None))
        return int(getter()) if getter else 0

    def encode(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray | None:
        """L2-normalized float32 vectors, one row per text.

        ``is_query`` selects the instruction prefix. Concept names are
        documents; the column being mapped is the query. For a symmetric model
        both prefixes are empty and the distinction costs nothing.
        """
        model = self._load()
        if model is None or not texts:
            return None
        prefix = self.query_prefix if is_query else self.document_prefix
        if prefix:
            texts = [prefix + text for text in texts]
        vectors = model.encode(
            list(texts),
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,   # so cosine is a plain dot product
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class EmbeddingScorer:
    """Stage 5."""

    def __init__(self, config: PipelineConfig, db: Database,
                 model: EmbeddingModel | None = None) -> None:
        self.config = config
        self.db = db
        self.model = model or EmbeddingModel(
            config.embedding_model, config.embedding_batch_size,
            config.embedding_query_prefix, config.embedding_document_prefix)
        self._synonym_cache: dict[str, np.ndarray] = {}
        self._cache_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.embeddings_enabled and self.model.available

    # ────────────────────────────────────────────────────────────────────
    def run(self, ctx: ContextBundle, variant: SearchVariant,
            candidates: list[Candidate]) -> list[Candidate]:
        if not self.enabled:
            return candidates

        # Step 5.2: too few survivors to choose between — widen the net.
        if len(candidates) < self.config.embedding_widen_below:
            candidates = candidates + self._widen(ctx, candidates)

        if not candidates:
            return candidates

        query_text = " ".join(
            text_utils.split_phrase(t) for t in variant.tokens) or variant.search_string
        query = self.model.encode([query_text], is_query=True)
        if query is None:
            return candidates
        query_vec = query[0]

        vectors = self._concept_vectors([c.concept_id for c in candidates],
                                        {c.concept_id: c.concept_name for c in candidates})

        # Step 5.3: a synonym that matches better than the name lifts the score.
        synonym_vectors = self._synonym_vectors(candidates)
        adjust = make_adjuster(self.config, ctx, candidates)

        for cand in candidates:
            vector = vectors.get(cand.concept_id)
            if vector is None:
                continue
            cosine = float(np.dot(query_vec, vector))
            best_synonym = 0.0
            for synonym_vec in synonym_vectors.get(cand.concept_id, ()):
                best_synonym = max(best_synonym, float(np.dot(query_vec, synonym_vec)))

            score = max(cosine, 0.0) * 100.0
            if best_synonym > cosine:
                boosted = min(best_synonym * 100.0 * self.config.synonym_embedding_boost,
                              100.0)
                if boosted > score:
                    score = boosted
                    cand.boosts["synonym_embedding"] = self.config.synonym_embedding_boost
                    cand.notes.append("stage5 matched on synonym")

            cand.embedding_score = adjust(cand, score)

        # "Keep all candidates" — Stage 5 re-scores, it does not eliminate.
        return sorted(candidates, key=lambda c: -(c.embedding_score or 0.0))

    # ── cache ───────────────────────────────────────────────────────────
    def _concept_vectors(self, concept_ids: list[int],
                         names: dict[int, str]) -> dict[int, np.ndarray]:
        """Fetch cached vectors, encode and persist whatever is missing."""
        if not concept_ids:
            return {}

        dim = self.model.dimension
        rows = self.db.fetch_all(
            """
            SELECT concept_id, embedding, dim, name_hash
            FROM pipeline.concept_embedding
            WHERE concept_id = ANY(%(ids)s) AND model = %(model)s
            """,
            {"ids": concept_ids, "model": self.model.cache_key},
        )

        vectors: dict[int, np.ndarray] = {}
        for row in rows:
            concept_id = row["concept_id"]
            # A stale row (the concept was renamed by a vocabulary refresh, or
            # the model's dimension changed) is ignored and re-encoded below.
            if row["dim"] != dim or row["name_hash"] != _text_hash(names[concept_id]):
                continue
            vectors[concept_id] = np.frombuffer(row["embedding"], dtype=np.float32)

        missing = [cid for cid in concept_ids if cid not in vectors]
        if missing:
            encoded = self.model.encode([names[cid] for cid in missing])
            if encoded is not None:
                for concept_id, vector in zip(missing, encoded):
                    vectors[concept_id] = vector
                self._persist(missing, names, encoded, dim)
        return vectors

    def _persist(self, concept_ids: list[int], names: dict[int, str],
                 vectors: np.ndarray, dim: int) -> None:
        payload = [
            (cid, self.model.cache_key, dim, _text_hash(names[cid]),
             vectors[index].astype(np.float32).tobytes())
            for index, cid in enumerate(concept_ids)
        ]
        try:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO pipeline.concept_embedding
                           (concept_id, model, dim, name_hash, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (concept_id, model) DO UPDATE
                        SET dim = EXCLUDED.dim,
                            name_hash = EXCLUDED.name_hash,
                            embedding = EXCLUDED.embedding,
                            updated_at = now()
                    """,
                    payload,
                )
        except Exception as exc:   # caching is an optimization, never fatal
            log.warning("could not cache embeddings: %s", exc)

    def _synonym_vectors(
        self, candidates: list[Candidate]
    ) -> dict[int, list[np.ndarray]]:
        wanted: dict[int, list[str]] = {}
        texts: list[str] = []
        for cand in candidates[: self.config.synonym_embedding_candidates]:
            synonyms = list(cand.synonyms[: self.config.synonym_embedding_max])
            if not synonyms:
                continue
            wanted[cand.concept_id] = synonyms
            texts.extend(s for s in synonyms if s not in self._synonym_cache)

        unique = sorted(set(texts))
        if unique:
            encoded = self.model.encode(unique)
            if encoded is not None:
                with self._cache_lock:
                    if len(self._synonym_cache) > 50_000:
                        self._synonym_cache.clear()
                    for text, vector in zip(unique, encoded):
                        self._synonym_cache[text] = vector

        return {
            concept_id: [self._synonym_cache[s] for s in synonyms
                         if s in self._synonym_cache]
            for concept_id, synonyms in wanted.items()
        }

    # ── Step 5.2 widening ───────────────────────────────────────────────
    def _widen(self, ctx: ContextBundle,
               existing: list[Candidate]) -> list[Candidate]:
        """Pull in extra domain-matched concepts when the shortlist is thin.

        Still filtered — by domain, and by sharing at least one trigram with
        the input — so this is a bounded lookup, not a scan of the vocabulary.
        """
        probe = text_utils.normalize_for_exact_match(ctx.search_string)
        if not probe:
            return []
        known = {c.concept_id for c in existing}
        domains = list(ctx.eligible_domains)

        rows = self.db.fetch_all(
            f"""
            SELECT cs.concept_id, cs.concept_name, cs.domain_id, cs.vocabulary_id,
                   cs.concept_class_id, cs.concept_code, cs.synonyms
            FROM pipeline.concept_search cs
            WHERE cs.concept_name_norm %% %(probe)s
              {"AND cs.domain_id = ANY(%(domains)s)" if domains else ""}
            ORDER BY similarity(cs.concept_name_norm, %(probe)s) DESC
            LIMIT %(limit)s
            """,
            {"probe": probe, "domains": domains,
             "limit": self.config.embedding_widen_limit},
        )
        return [
            Candidate(
                concept_id=row["concept_id"],
                concept_name=row["concept_name"],
                domain_id=row["domain_id"],
                vocabulary_id=row["vocabulary_id"],
                concept_class_id=row.get("concept_class_id") or "",
                concept_code=row.get("concept_code") or "",
                synonyms=tuple(row.get("synonyms") or ()),
                source_stage="stage5_widened",
            )
            for row in rows if row["concept_id"] not in known
        ]
