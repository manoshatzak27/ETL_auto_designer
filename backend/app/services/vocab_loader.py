"""
Athena vocabulary bundle loader.

Athena exports come as a directory of tab-delimited CSVs:
  CONCEPT.csv, CONCEPT_RELATIONSHIP.csv, CONCEPT_ANCESTOR.csv, CONCEPT_SYNONYM.csv,
  VOCABULARY.csv, DOMAIN.csv, CONCEPT_CLASS.csv, RELATIONSHIP.csv, DRUG_STRENGTH.csv

The loader is idempotent: it TRUNCATEs the vocab tables (CASCADE) and re-loads.
After loading it (re)builds the few indexes the wizard's concept search relies
on — see _SEARCH_INDEXES.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.services.db import connect as _connect, sql


# Athena export filename → OMOP table (lowercase) — exact filenames vary; we
# match case-insensitively when looking files up.
_VOCAB_FILES: list[tuple[str, str]] = [
    ("CONCEPT.csv", "concept"),
    ("VOCABULARY.csv", "vocabulary"),
    ("DOMAIN.csv", "domain"),
    ("CONCEPT_CLASS.csv", "concept_class"),
    ("RELATIONSHIP.csv", "relationship"),
    ("CONCEPT_RELATIONSHIP.csv", "concept_relationship"),
    ("CONCEPT_SYNONYM.csv", "concept_synonym"),
    ("CONCEPT_ANCESTOR.csv", "concept_ancestor"),
    ("DRUG_STRENGTH.csv", "drug_strength"),
]


# Indexes the table steps' concept search needs, per table, with the names the
# OHDSI indices file gives them (omop_ddl/OMOPCDM_postgresql_5.4_indices.sql) so
# a later full OHDSI index run finds them already there. Without them every
# search reads all of CONCEPT (~10M rows) and CONCEPT_ANCESTOR (~85M rows),
# ~1.4 s each, however few concepts the searched domain has.
_SEARCH_INDEXES: dict[str, list[tuple[str, str]]] = {
    "concept": [("idx_concept_domain", "domain_id")],
    "concept_ancestor": [("idx_concept_ancestor_dec", "descendant_concept_id")],
}


class VocabFileStatus(BaseModel):
    file: str
    table: str
    status: str = "pending"  # pending | loading | success | error | skipped
    rows: int = 0
    started_at: float = 0.0
    elapsed: float = 0.0
    error: str = ""


class VocabLoadStatus(BaseModel):
    schema: str = ""
    overall: str = "idle"
    started_at: float = 0.0
    finished_at: float = 0.0
    log: str = ""
    files: list[VocabFileStatus] = []


_vocab_status: VocabLoadStatus = VocabLoadStatus()
_vocab_lock = threading.Lock()


def get_vocab_status() -> VocabLoadStatus:
    with _vocab_lock:
        return _vocab_status.copy(deep=True)


def _set_status(status: VocabLoadStatus) -> None:
    global _vocab_status
    with _vocab_lock:
        _vocab_status = status


def _append_log(line: str) -> None:
    global _vocab_status
    with _vocab_lock:
        _vocab_status.log = (_vocab_status.log + "\n" + line).strip()


def _find_file(bundle_dir: Path, filename: str) -> Path | None:
    """Case-insensitive lookup so CONCEPT.csv / concept.csv both work."""
    target = filename.lower()
    for child in bundle_dir.iterdir():
        if child.is_file() and child.name.lower() == target:
            return child
    return None


def load_vocabulary(bundle_path: str, schema: str) -> None:
    """Background task. Updates module-level _vocab_status as it runs."""
    bundle = Path(bundle_path)
    file_statuses = [VocabFileStatus(file=fn, table=tbl) for fn, tbl in _VOCAB_FILES]
    _set_status(
        VocabLoadStatus(
            schema=schema,
            overall="running",
            started_at=time.time(),
            files=file_statuses,
        )
    )

    # Ensure the vocab schema and its DDL exist before truncating tables.
    try:
        from app.services.ddl_applier import apply_schema_ddl
        apply_schema_ddl(schema, "vocab")
    except Exception as exc:  # noqa: BLE001
        _append_log(f"[DDL] FATAL: {exc}")
        _set_status(_vocab_status.copy(update={"overall": "error", "finished_at": time.time()}))
        return

    try:
        with _connect() as conn:
            conn.autocommit = False

            # Disable triggers/constraints during bulk load
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SET search_path TO {schema}, public").format(
                        schema=sql.Identifier(schema)
                    )
                )

            for entry in file_statuses:
                src = _find_file(bundle, entry.file)
                if src is None:
                    entry.status = "skipped"
                    entry.error = "file not found in bundle"
                    _append_log(f"[{entry.table}] skipped (no {entry.file})")
                    _set_status(_vocab_status.copy(update={"files": file_statuses}))
                    continue

                entry.status = "loading"
                entry.started_at = time.time()
                _set_status(_vocab_status.copy(update={"files": file_statuses}))
                t0 = time.monotonic()
                try:
                    with conn.cursor() as cur:
                        # Dropped for the bulk load and rebuilt after it: one
                        # build is far cheaper than maintaining the index for
                        # each of tens of millions of COPYed rows.
                        for index, _column in _SEARCH_INDEXES.get(entry.table, []):
                            cur.execute(
                                sql.SQL("DROP INDEX IF EXISTS {schema}.{index}").format(
                                    schema=sql.Identifier(schema), index=sql.Identifier(index),
                                )
                            )
                        cur.execute(
                            sql.SQL("TRUNCATE TABLE {schema}.{table} CASCADE").format(
                                schema=sql.Identifier(schema),
                                table=sql.Identifier(entry.table),
                            )
                        )
                        copy_sql = sql.SQL(
                            "COPY {schema}.{table} FROM STDIN "
                            "WITH (FORMAT csv, DELIMITER E'\\t', NULL '', HEADER, "
                            "QUOTE E'\\b', ENCODING 'UTF8')"
                        ).format(
                            schema=sql.Identifier(schema),
                            table=sql.Identifier(entry.table),
                        )
                        with src.open("r", encoding="utf-8") as fh:
                            cur.copy_expert(copy_sql.as_string(conn), fh)
                        cur.execute(
                            sql.SQL("SELECT COUNT(*) FROM {schema}.{table}").format(
                                schema=sql.Identifier(schema),
                                table=sql.Identifier(entry.table),
                            )
                        )
                        entry.rows = int(cur.fetchone()[0])
                    conn.commit()
                    entry.status = "success"
                    _append_log(f"[{entry.table}] loaded {entry.rows} rows from {entry.file}")
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    entry.status = "error"
                    entry.error = str(exc)
                    _append_log(f"[{entry.table}] ERROR: {exc}")
                finally:
                    entry.elapsed = round(time.monotonic() - t0, 2)
                    _set_status(_vocab_status.copy(update={"files": file_statuses}))

            # Search indexes, for every table that was (re)loaded. A table whose
            # load failed was rolled back, index included, so it is left alone.
            for entry in file_statuses:
                if entry.status != "success":
                    continue
                for index, column in _SEARCH_INDEXES.get(entry.table, []):
                    t0 = time.monotonic()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SET LOCAL maintenance_work_mem = '1GB'")
                            cur.execute(
                                sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {schema}.{table} ({column})").format(
                                    index=sql.Identifier(index), schema=sql.Identifier(schema),
                                    table=sql.Identifier(entry.table), column=sql.Identifier(column),
                                )
                            )
                        conn.commit()
                        _append_log(f"[{entry.table}] index {index} built in {time.monotonic() - t0:.1f}s")
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        _append_log(f"[{entry.table}] index {index} failed: {exc}")

            # ANALYZE for query planner
            try:
                with conn.cursor() as cur:
                    for entry in file_statuses:
                        if entry.status == "success":
                            cur.execute(
                                sql.SQL("ANALYZE {schema}.{table}").format(
                                    schema=sql.Identifier(schema),
                                    table=sql.Identifier(entry.table),
                                )
                            )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                _append_log(f"[ANALYZE] {exc}")
    except Exception as exc:  # noqa: BLE001
        _append_log(f"[FATAL] {exc}")
        _set_status(_vocab_status.copy(update={"overall": "error", "finished_at": time.time()}))
        return

    overall = "success" if all(f.status in {"success", "skipped"} for f in file_statuses) else "error"
    _set_status(
        _vocab_status.copy(update={"overall": overall, "finished_at": time.time(), "files": file_statuses})
    )
