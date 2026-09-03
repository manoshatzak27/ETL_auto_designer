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
                        kwargs={"options": f"-c statement_timeout={self.config.statement_timeout_ms}"},
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
            "pipeline.concept_search (run ./setup_pipeline.sh)":
                "SELECT to_regclass('pipeline.concept_search') IS NOT NULL",
            "pipeline.token_idf (run ./setup_pipeline.sh)":
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
