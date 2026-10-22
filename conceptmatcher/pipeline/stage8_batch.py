"""Stage 8 — batch processing and performance management.

Runs many columns through :class:`~pipeline.runner.ConceptMatcher` with a
thread pool. Threads rather than processes because the work is dominated by
waiting on Postgres and, when embeddings are on, by torch — both of which
release the GIL — while processes would each need their own connection pool and
their own copy of the embedding model.

Step 8.1's connection pooling comes from the shared ``Database``; the pool size
is raised to cover the workers so a worker never blocks waiting for a
connection. Step 8.2's caching is the IDF index, the concept-token cache, the
embedding cache and the shortlist cache below. Step 8.3 lives in
:func:`refresh_derived_structures`.
"""

from __future__ import annotations

import csv
import json
import logging
import threading
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import resources, text_utils
from .config import PipelineConfig
from .models import MatchResult
from .runner import ConceptMatcher

log = logging.getLogger(__name__)


class ShortlistCache:
    """Step 8.2: memoize Stage 2 shortlists for repeated column patterns.

    Keyed by the *normalized* input, so ``PATIENT_SBP`` and ``sbp_patient``
    share an entry. Bounded LRU — an unbounded cache over a large batch would
    hold every candidate list in memory for no benefit.
    """

    def __init__(self, max_size: int = 512) -> None:
        self.max_size = max_size
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._entries)}


class BatchMatcher:
    """Stage 8."""

    def __init__(self, matcher: ConceptMatcher | None = None,
                 config: PipelineConfig | None = None) -> None:
        self.config = config or (matcher.config if matcher else PipelineConfig.load())
        # Enough connections for every worker plus the caller.
        self.config.pool_max_size = max(self.config.pool_max_size,
                                        self.config.batch_workers + 2)
        self.matcher = matcher or ConceptMatcher(self.config)
        self.cache = ShortlistCache(self.config.shortlist_cache_size)

    # ────────────────────────────────────────────────────────────────────
    def run(
        self,
        columns: Sequence[dict[str, Any]],
        source_system: str | None = None,
        note: str | None = None,
        progress: Callable[[int, int, MatchResult], None] | None = None,
    ) -> tuple[int, list[MatchResult]]:
        """Map every column. Returns ``(run_id, results)`` in input order.

        Each entry needs a ``name``; ``description`` and ``table`` are optional.
        """
        if not columns:
            return 0, []

        run_id = self.matcher.start_run(source_system, note)

        # Load the embedding model before any worker needs it. Otherwise the
        # first column to reach Stage 5 pays several seconds for the load while
        # every other worker blocks on the same lock, which both slows the
        # batch and charges the whole load to one column's Stage 5 timing.
        if self.matcher.stage5.enabled:
            self.matcher.stage5.model.encode(["warmup"])

        # Step 0.7's adaptive stopwords need the whole batch before the first
        # column can be processed, so they are computed up front.
        corpus_stopwords = self._corpus_stopwords(columns)
        if corpus_stopwords:
            log.info("corpus-adaptive stopwords for this batch: %s",
                     sorted(corpus_stopwords))

        results: list[MatchResult | None] = [None] * len(columns)
        workers = max(1, self.config.batch_workers)

        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="match") as pool:
            futures = {
                pool.submit(
                    self.matcher.match,
                    column["name"],
                    column.get("description"),
                    column.get("table"),
                    source_system,
                    corpus_stopwords,
                ): index
                for index, column in enumerate(columns)
            }
            done = 0
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except Exception as exc:      # already handled inside match();
                    log.exception("worker failed")   # this is belt and braces
                    from .models import ContextBundle

                    result = MatchResult(
                        context=ContextBundle(
                            column_name=columns[index]["name"],
                            column_description=columns[index].get("description"),
                        ),
                        status="error",
                        error=str(exc),
                    )
                results[index] = result
                done += 1
                if progress:
                    progress(done, len(columns), result)

        self.matcher.finish_run()
        return run_id, [r for r in results if r is not None]

    # ── Step 0.7 ────────────────────────────────────────────────────────
    def _corpus_stopwords(self, columns: Sequence[dict[str, Any]]) -> frozenset[str]:
        """Words in ``corpus_adaptive`` that exceed the 80% batch threshold.

        The spec's rule: a domain-ish word only becomes noise once nearly every
        column carries it. In a table of lab results where every column says
        "level", "level" stops distinguishing anything and should go; in a
        mixed batch it stays.
        """
        candidates = resources.stopwords()["corpus_adaptive"]
        if not candidates or len(columns) < 5:
            # Below a handful of columns the frequency estimate is noise.
            return frozenset()

        counts: Counter[str] = Counter()
        for column in columns:
            text = " ".join(filter(None, [
                text_utils.split_identifier(column["name"]),
                column.get("description") or "",
            ]))
            counts.update(set(text_utils.tokenize(text.lower())) & candidates)

        threshold = self.config.stopword_corpus_threshold * len(columns)
        return frozenset(word for word, count in counts.items() if count >= threshold)

    def close(self) -> None:
        self.matcher.close()


# ── Step 8.3 ────────────────────────────────────────────────────────────


def refresh_derived_structures(matcher: ConceptMatcher,
                               full: bool = False) -> dict[str, Any]:
    """Bring the derived structures back in step with the vocabulary.

    Refreshes the search view, rebuilds the IDF table, and invalidates the
    embedding cache *selectively*: only concepts whose name hash no longer
    matches are dropped, so a vocabulary update re-embeds the concepts that
    changed rather than all 3.5M.

    ``full=True`` drops the whole embedding cache instead, for a model change.
    """
    db = matcher.db
    stats: dict[str, Any] = {}

    # CONCURRENTLY needs the unique index from 12_pipeline_indexes.sql and
    # keeps the view queryable while it rebuilds.
    db.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY pipeline.concept_search")
    stats["concept_search_rows"] = db.fetch_value(
        "SELECT count(*) FROM pipeline.concept_search")

    # One connection for the whole IDF rebuild: SET work_mem is per-session, and
    # issuing it through the pool would tune a connection that the aggregate
    # below is then free to not be given.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SET work_mem = '512MB'")
        cur.execute("TRUNCATE pipeline.token_idf")
        cur.execute(
            """
            INSERT INTO pipeline.token_idf (token, doc_freq)
            SELECT u.lexeme, count(*)
            FROM pipeline.concept_search cs, LATERAL unnest(cs.search_vector) AS u
            GROUP BY u.lexeme
            HAVING count(*) > 1
            """
        )
        cur.execute("TRUNCATE pipeline.corpus_stats")
        cur.execute(
            """
            INSERT INTO pipeline.corpus_stats (stat_key, stat_value)
            SELECT 'total_concepts', count(*) FROM pipeline.concept_search
            UNION ALL SELECT 'idf_tokens', count(*) FROM pipeline.token_idf
            UNION ALL SELECT 'max_doc_freq', COALESCE(max(doc_freq), 1)
                      FROM pipeline.token_idf
            """
        )
    stats["idf_tokens"] = db.fetch_value("SELECT count(*) FROM pipeline.token_idf")

    if full:
        stats["embeddings_dropped"] = db.execute(
            "DELETE FROM pipeline.concept_embedding")
    else:
        # Only the renamed concepts. The hash is computed the same way
        # stage5_embeddings does it, in SQL, so no round trip is needed.
        stats["embeddings_dropped"] = db.execute(
            """
            DELETE FROM pipeline.concept_embedding e
            USING pipeline.concept_search cs
            WHERE cs.concept_id = e.concept_id
              AND e.name_hash <> left(encode(sha256(cs.concept_name::bytea), 'hex'), 32)
            """
        )
        # Concepts that left the vocabulary entirely.
        stats["embeddings_orphaned"] = db.execute(
            """
            DELETE FROM pipeline.concept_embedding e
            WHERE NOT EXISTS (SELECT 1 FROM pipeline.concept_search cs
                              WHERE cs.concept_id = e.concept_id)
            """
        )

    db.execute("ANALYZE pipeline.concept_search")
    db.execute("ANALYZE pipeline.token_idf")

    version = db.fetch_value(
        """
        SELECT COALESCE(
            (SELECT vocabulary_version FROM vocab.vocabulary WHERE vocabulary_id = 'None'),
            'unknown')
        """
    )
    db.execute(
        """
        INSERT INTO pipeline.vocabulary_version
               (vocabulary_version, concept_count, note)
        VALUES (%s, (SELECT count(*) FROM pipeline.concept_search), %s)
        """,
        (version, "refreshed via stage8_batch.refresh_derived_structures"),
    )
    stats["vocabulary_version"] = version
    return stats


# ── input / output helpers ──────────────────────────────────────────────


def read_columns(path: Path) -> list[dict[str, Any]]:
    """Read a column list from CSV or JSON.

    CSV wants a header with ``name`` and optionally ``description``/``table``;
    JSON wants a list of objects with the same keys, or a list of bare strings.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a JSON list of columns")
        return [
            {"name": item} if isinstance(item, str) else {
                "name": item["name"],
                "description": item.get("description"),
                "table": item.get("table"),
            }
            for item in data
        ]

    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        return []
    fields = {name.lower().strip(): name for name in rows[0]}
    if "name" not in fields:
        raise ValueError(
            f"{path}: CSV needs a 'name' column, found {sorted(fields)}")
    return [
        {
            "name": (row[fields["name"]] or "").strip(),
            "description": (row.get(fields.get("description", ""), "") or "").strip() or None,
            "table": (row.get(fields.get("table", ""), "") or "").strip() or None,
        }
        for row in rows if (row[fields["name"]] or "").strip()
    ]


def write_results(results: Iterable[MatchResult], path: Path) -> None:
    """Write mappings as CSV or JSON, chosen by file extension."""
    results = list(results)
    if path.suffix.lower() == ".json":
        path.write_text(
            json.dumps([r.as_dict() for r in results], indent=2, default=str),
            encoding="utf-8")
        return

    columns = [
        "column_name", "column_description", "status", "confidence",
        "concept_id", "concept_name", "domain_id", "vocabulary_id",
        "concept_code", "decided_at_stage", "ambiguous", "validation_flags",
        "decision_reason", "elapsed_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = result.as_dict()
            row["validation_flags"] = "; ".join(row["validation_flags"])
            writer.writerow(row)
