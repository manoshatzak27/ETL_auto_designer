"""Database access: a shared connection pool plus small query helpers.

Step 8.1 asks for connection pooling so a parallel batch does not open one
connection per column. ``psycopg_pool.ConnectionPool`` is thread-safe, so a
single ``Database`` instance is shared by every worker.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import PipelineConfig


class Database:
    """Thin wrapper over a psycopg3 pool.

    Opened lazily so that importing the package (for `--help`, say) does not
    require a running Postgres.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────
    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        self.config.conninfo,
                        min_size=self.config.pool_min_size,
                        max_size=self.config.pool_max_size,
                        kwargs={
                            "options": f"-c statement_timeout={self.config.statement_timeout_ms}",
                            # No server-side prepared statements. psycopg
                            # prepares a query after 5 runs on a connection,
                            # and Postgres then moves the prepared Stage 2 FTS
                            # queries to generic plans that ignore the search
                            # terms — so a long-running matcher got slower the
                            # more it was used: the same 206 columns took 110,
                            # 149, 212, 230 ms/column on four successive runs
                            # in one process, all of it Stage 2. Without
                            # preparing they hold at ~93. Planning these
                            # queries costs far less than a bad plan does.
                            "prepare_threshold": None,
                        },
                        open=True,
                    )
                    self._pool.wait(timeout=30)
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            yield conn

    # ── queries ─────────────────────────────────────────────────────────
    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_value(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        row = self.fetch_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def execute_returning(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

    # ── health / metadata ───────────────────────────────────────────────
    def check_ready(self) -> list[str]:
        """Return a list of human-readable problems; empty means good to go."""
        problems: list[str] = []
        try:
            with self.pool.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as exc:  # pragma: no cover - depends on environment
            return [f"cannot connect to {self.config.host}:{self.config.port} "
                    f"as {self.config.user}: {exc}"]

        checks = {
            "vocab.concept":
                "SELECT to_regclass('vocab.concept') IS NOT NULL",
            "pipeline.concept_search (run scripts/setup-conceptmatcher.sh)":
                "SELECT to_regclass('pipeline.concept_search') IS NOT NULL",
            "pipeline.token_idf (run scripts/setup-conceptmatcher.sh)":
                "SELECT to_regclass('pipeline.token_idf') IS NOT NULL",
            "pg_trgm extension":
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')",
        }
        for label, sql in checks.items():
            if not self.fetch_value(sql):
                problems.append(f"missing: {label}")

        if not problems:
            if not self.fetch_value("SELECT count(*) > 0 FROM pipeline.token_idf"):
                problems.append("pipeline.token_idf is empty (re-run sql/13_pipeline_idf.sql)")
        return problems

    def vocabulary_version(self) -> str:
        return self.fetch_value(
            "SELECT vocabulary_version FROM pipeline.vocabulary_version "
            "ORDER BY version_id DESC LIMIT 1"
        ) or "unknown"

    # ── domains ─────────────────────────────────────────────────────────
    def domain_ids(self) -> list[str]:
        """Every domain_id this vocabulary defines, alphabetically.

        ``vocab.domain`` is loaded unfiltered (it is a small lookup table), so
        this lists names that are spellable even where no standard concept
        carries them — which is why `domain_counts` exists.
        """
        return [row["domain_id"] for row in self.fetch_all(
            "SELECT domain_id FROM vocab.domain ORDER BY domain_id")]

    def domain_counts(self) -> list[dict[str, Any]]:
        """Standard concepts per domain, largest first.

        One grouped index-only scan of the matview. Not cheap enough to run per
        match, which is why only the `domains` command calls it.
        """
        return self.fetch_all(
            """
            SELECT domain_id, count(*) AS concepts
            FROM pipeline.concept_search
            GROUP BY domain_id
            ORDER BY concepts DESC, domain_id
            """)

    def resolve_domains(self, requested: Sequence[str]) -> list[str]:
        """Canonicalize user-typed domain names, or say why they are wrong.

        Matching is case-insensitive because the OMOP spellings are not
        guessable ("Meas Value", "Spec Anatomic Site"), and a restriction that
        silently matches nothing is the worst possible failure mode for this
        feature: every column would come back unmapped and the filter would
        look like a pipeline bug. So an unknown name raises, and a known name
        holding no standard concept raises too.
        """
        wanted = [name.strip() for name in requested if name and name.strip()]
        if not wanted:
            return []

        rows = self.fetch_all(
            """
            SELECT d.domain_id,
                   EXISTS (SELECT 1 FROM pipeline.concept_search cs
                            WHERE cs.domain_id = d.domain_id) AS populated
            FROM vocab.domain d
            WHERE lower(d.domain_id) = ANY(%(names)s)
            """,
            {"names": [name.lower() for name in wanted]},
        )
        found = {row["domain_id"].lower(): row for row in rows}

        unknown = [name for name in wanted if name.lower() not in found]
        if unknown:
            raise ValueError(
                f"unknown domain(s): {', '.join(sorted(unknown))}. "
                f"Valid domains are: {', '.join(self.domain_ids())}")

        empty = [row["domain_id"] for row in rows if not row["populated"]]
        if empty:
            raise ValueError(
                f"domain(s) {', '.join(sorted(empty))} hold no standard, valid "
                "concept in this database, so restricting to them can only "
                "return nothing")

        # De-duplicated, and ordered as the caller wrote them.
        canonical: list[str] = []
        for name in wanted:
            domain_id = found[name.lower()]["domain_id"]
            if domain_id not in canonical:
                canonical.append(domain_id)
        return canonical

    def learned_weights(self) -> list[tuple[str, float]]:
        """Stage 7.5 weight overrides stored in the database."""
        rows = self.fetch_all(
            "SELECT weight_key, weight FROM pipeline.scoring_weight")
        return [(r["weight_key"], float(r["weight"])) for r in rows]


_SHARED: dict[int, Database] = {}
_SHARED_LOCK = threading.Lock()


def shared_database(config: PipelineConfig) -> Database:
    """One pool per config object, so stages can grab the DB without plumbing."""
    key = id(config)
    with _SHARED_LOCK:
        db = _SHARED.get(key)
        if db is None:
            db = Database(config)
            _SHARED[key] = db
        return db
