"""
OMOP Postgres loader.

Reads the per-project ETL output CSVs (semicolon-delimited, UTF-8) under
outputs/{project_id}/ and bulk-loads them into a Postgres schema using
psycopg2 COPY. Loads only columns that exist in the target table — extra
columns in the CSV (e.g. legacy record_source_value) are silently dropped.

A simple in-process dict tracks load progress so the UI can poll
/projects/{id}/load-status.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.services.db import PSYCOPG2_AVAILABLE as _PSYCOPG2_AVAILABLE
from app.services.db import PGConnection, connect as _connect, sql


# ─── State ─────────────────────────────────────────────────────────────────────

class TableLoadStatus(BaseModel):
    table: str
    status: str = "pending"  # pending | loading | success | error | skipped
    rows: int = 0
    elapsed: float = 0.0
    error: str = ""


class LoadStatus(BaseModel):
    project_id: str
    overall: str = "idle"  # idle | running | success | error
    schema: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    log: str = ""
    tables: list[TableLoadStatus] = []


_status_lock = threading.Lock()
_status_store: dict[str, LoadStatus] = {}


def reset_load_status(project_id: str) -> None:
    with _status_lock:
        _status_store[project_id] = LoadStatus(project_id=project_id, overall="running", started_at=time.time())


def _update_status(project_id: str, **fields: Any) -> None:
    with _status_lock:
        st = _status_store.get(project_id) or LoadStatus(project_id=project_id)
        for k, v in fields.items():
            setattr(st, k, v)
        _status_store[project_id] = st


def get_load_status(project_id: str) -> LoadStatus:
    with _status_lock:
        return _status_store.get(project_id) or LoadStatus(project_id=project_id)


def _append_log(project_id: str, line: str) -> None:
    with _status_lock:
        st = _status_store.get(project_id) or LoadStatus(project_id=project_id)
        st.log = (st.log + "\n" + line).strip()
        _status_store[project_id] = st


# ─── Health ───────────────────────────────────────────────────────────────────

def check_db_health() -> dict:
    vocab_schema = settings.omop_vocab_schema or "vocab"
    info: dict[str, Any] = {
        "configured": bool(settings.omop_db_host),
        "connected": False,
        "ddl_applied": False,
        "schemas": [],
        "vocab_schema": vocab_schema,
        "vocab_schema_ready": False,
        "vocab_rows": 0,
        "clinical_schemas": [],
        "error": "",
    }
    if not info["configured"]:
        info["error"] = "OMOP Postgres not configured (set OMOP_DB_* in backend/.env)."
        return info
    if not _PSYCOPG2_AVAILABLE:
        info["error"] = "psycopg2 not installed on the backend."
        return info
    try:
        with _connect() as conn:
            info["connected"] = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')"
                )
                info["schemas"] = sorted(r[0] for r in cur.fetchall())

                cur.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables "
                    "  WHERE table_schema = %s AND table_name = 'person'"
                    ")",
                    (settings.omop_default_schema,),
                )
                info["ddl_applied"] = bool(cur.fetchone()[0])

                cur.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables "
                    "  WHERE table_schema = %s AND table_name = 'concept'"
                    ")",
                    (vocab_schema,),
                )
                info["vocab_schema_ready"] = bool(cur.fetchone()[0])

                if info["vocab_schema_ready"]:
                    try:
                        cur.execute(
                            sql.SQL("SELECT count(*) FROM {schema}.concept").format(
                                schema=sql.Identifier(vocab_schema)
                            )
                        )
                        info["vocab_rows"] = int(cur.fetchone()[0])
                    except Exception:  # noqa: BLE001
                        info["vocab_rows"] = 0

                # Any schema starting with "cdm" is a candidate clinical schema.
                clinical_candidates = [
                    s for s in info["schemas"]
                    if s == settings.omop_default_schema or s.startswith("cdm_")
                ]
                clinical_rows: list[dict[str, Any]] = []
                for cs in clinical_candidates:
                    cur.execute(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM information_schema.tables "
                        "  WHERE table_schema = %s AND table_name = 'person'"
                        ")",
                        (cs,),
                    )
                    ddl_ok = bool(cur.fetchone()[0])
                    rows = 0
                    if ddl_ok:
                        try:
                            cur.execute(
                                sql.SQL("SELECT count(*) FROM {schema}.person").format(
                                    schema=sql.Identifier(cs)
                                )
                            )
                            rows = int(cur.fetchone()[0])
                        except Exception:  # noqa: BLE001
                            rows = 0
                    clinical_rows.append({"name": cs, "ddl_applied": ddl_ok, "person_rows": rows})
                info["clinical_schemas"] = clinical_rows
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


# ─── Loader ───────────────────────────────────────────────────────────────────

def _ensure_schema(conn: "PGConnection", schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(
                schema=sql.Identifier(schema)
            )
        )


def _table_columns(conn: "PGConnection", schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            (schema, table),
        )
        return [r[0] for r in cur.fetchall()]


def _copy_csv_into_table(
    conn: "PGConnection",
    schema: str,
    table: str,
    csv_path: Path,
    truncate: bool,
) -> tuple[int, str]:
    """Returns (rows_loaded, info_line)."""
    target_cols = _table_columns(conn, schema, table)
    if not target_cols:
        return 0, f"target table {schema}.{table} does not exist (skipped)"

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter=";", quotechar='"')
        try:
            header = next(reader)
        except StopIteration:
            return 0, f"{csv_path.name} is empty (skipped)"

        # Intersect CSV columns with target table columns (preserve target order)
        csv_idx = {name: i for i, name in enumerate(header)}
        usable = [c for c in target_cols if c in csv_idx]
        if not usable:
            return 0, f"no overlapping columns between {csv_path.name} and {schema}.{table} (skipped)"

        rows = list(reader)

    if truncate:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("TRUNCATE TABLE {schema}.{table} CASCADE").format(
                    schema=sql.Identifier(schema),
                    table=sql.Identifier(table),
                )
            )

    # Build an in-memory CSV with only the columns the target table wants
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        out_row = []
        for col in usable:
            idx = csv_idx[col]
            out_row.append(row[idx] if idx < len(row) else "")
        writer.writerow(out_row)
    buf.seek(0)

    copy_sql = sql.SQL(
        "COPY {schema}.{table} ({cols}) FROM STDIN "
        "WITH (FORMAT csv, DELIMITER ';', NULL '', QUOTE '\"', ENCODING 'UTF8')"
    ).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in usable),
    )
    with conn.cursor() as cur:
        cur.copy_expert(copy_sql.as_string(conn), buf)

    return len(rows), f"loaded {len(rows)} rows into {schema}.{table} (cols: {len(usable)}/{len(target_cols)})"


INDICES_TABLE = "__indices__"
CONSTRAINTS_TABLE = "__constraints__"
CUSTOM_CONCEPTS_TABLE = "__custom_concepts__"


def _insert_custom_concepts(conn: "PGConnection", project_id: str) -> tuple[int, str]:
    """Insert any custom concepts (id >= 2_000_000_000) into <vocab_schema>.concept
    so that downstream clinical FK references resolve. Idempotent via ON CONFLICT.

    Returns (rows_inserted, info_line). Returns (0, 'no custom_mappings.csv') if the
    file doesn't exist — this is the common case for projects with no custom concepts.
    """
    custom_path = settings.get_upload_path() / project_id / "mappings" / "custom_mappings.csv"
    if not custom_path.exists():
        return 0, "no custom_mappings.csv (project has no custom concepts)"

    vocab_schema = settings.omop_vocab_schema or "vocab"

    with custom_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    if not rows:
        return 0, "custom_mappings.csv is empty"

    insert_sql = sql.SQL(
        "INSERT INTO {schema}.concept "
        "(concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, "
        " standard_concept, concept_code, valid_start_date, valid_end_date, invalid_reason) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (concept_id) DO UPDATE SET "
        "concept_name = EXCLUDED.concept_name, "
        "domain_id = EXCLUDED.domain_id, "
        "vocabulary_id = EXCLUDED.vocabulary_id, "
        "concept_class_id = EXCLUDED.concept_class_id, "
        "concept_code = EXCLUDED.concept_code"
    ).format(schema=sql.Identifier(vocab_schema))

    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                insert_sql.as_string(conn),
                (
                    int(r["concept_id"]),
                    r.get("concept_name", ""),
                    r.get("domain_id", "Observation"),
                    r.get("vocabulary_id", "CUSTOM"),
                    r.get("concept_class_id", "Clinical Finding"),
                    r.get("standard_concept", "S"),
                    r.get("concept_code", r["concept_id"]),
                    r.get("valid_start_date", "1970-01-01"),
                    r.get("valid_end_date", "2099-12-31"),
                    r.get("invalid_reason", "") or None,
                ),
            )
    return len(rows), f"upserted {len(rows)} custom concepts into {vocab_schema}.concept"


def _set_search_path(conn: "PGConnection", schema: str) -> None:
    vocab = settings.omop_vocab_schema or "vocab"
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SET search_path TO {clin}, {voc}, public").format(
                clin=sql.Identifier(schema),
                voc=sql.Identifier(vocab),
            )
        )


def load_project_outputs(
    project_id: str,
    output_dir: str,
    schema: str,
    truncate: bool,
    table_order: list[str],
    apply_indices: bool = False,
) -> None:
    """Background task. Updates _status_store as it runs.

    If apply_indices is True, two synthetic entries (__indices__, __constraints__)
    are appended after the CSV loads so progress surfaces in the UI."""
    # Bootstrap the clinical DDL on demand. If it's already applied the
    # marker check makes this a no-op.
    try:
        from app.services.ddl_applier import apply_schema_ddl
        apply_schema_ddl(schema, "clinical")
    except Exception as exc:  # noqa: BLE001
        _append_log(project_id, f"[DDL] FATAL: {exc}")
        _update_status(project_id, overall="error", finished_at=time.time())
        return

    # Custom concepts run after the CSV loop and before indices/constraints so
    # that any FK reference from clinical rows to a custom concept resolves.
    extras: list[str] = [CUSTOM_CONCEPTS_TABLE]
    if apply_indices:
        extras.extend([INDICES_TABLE, CONSTRAINTS_TABLE])
    tables = [TableLoadStatus(table=t) for t in (*table_order, *extras)]
    _update_status(project_id, schema=schema, tables=tables, overall="running")

    try:
        with _connect() as conn:
            conn.autocommit = False
            _ensure_schema(conn, schema)
            _set_search_path(conn, schema)
            conn.commit()

            for entry in tables:
                if entry.table in {INDICES_TABLE, CONSTRAINTS_TABLE, CUSTOM_CONCEPTS_TABLE}:
                    continue  # handled after the CSV loop
                csv_path = Path(output_dir) / f"{entry.table}.csv"
                if not csv_path.exists():
                    entry.status = "skipped"
                    entry.error = "CSV not found"
                    _append_log(project_id, f"[{entry.table}] skipped (no CSV)")
                    _update_status(project_id, tables=tables)
                    continue

                entry.status = "loading"
                _update_status(project_id, tables=tables)
                t0 = time.monotonic()
                try:
                    rows, info = _copy_csv_into_table(
                        conn=conn,
                        schema=schema,
                        table=entry.table,
                        csv_path=csv_path,
                        truncate=truncate,
                    )
                    conn.commit()
                    entry.rows = rows
                    entry.status = "success" if rows > 0 or "skipped" not in info else "skipped"
                    _append_log(project_id, f"[{entry.table}] {info}")
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    entry.status = "error"
                    entry.error = str(exc)
                    _append_log(project_id, f"[{entry.table}] ERROR: {exc}")
                finally:
                    entry.elapsed = round(time.monotonic() - t0, 2)
                    _update_status(project_id, tables=tables)
    except Exception as exc:  # noqa: BLE001
        _append_log(project_id, f"[FATAL] {exc}")
        _update_status(project_id, overall="error", finished_at=time.time())
        return

    # Custom concept upsert (between CSV loads and indices).
    custom_entry = next((t for t in tables if t.table == CUSTOM_CONCEPTS_TABLE), None)
    if custom_entry is not None:
        custom_entry.status = "loading"
        _update_status(project_id, tables=tables)
        t0 = time.monotonic()
        try:
            with _connect() as conn:
                conn.autocommit = False
                rows, info = _insert_custom_concepts(conn, project_id)
                conn.commit()
            custom_entry.rows = rows
            custom_entry.status = "success" if rows > 0 else "skipped"
            _append_log(project_id, f"[{CUSTOM_CONCEPTS_TABLE}] {info}")
        except Exception as exc:  # noqa: BLE001
            custom_entry.status = "error"
            custom_entry.error = str(exc)
            _append_log(project_id, f"[{CUSTOM_CONCEPTS_TABLE}] ERROR: {exc}")
        finally:
            custom_entry.elapsed = round(time.monotonic() - t0, 2)
            _update_status(project_id, tables=tables)

    if apply_indices:
        # Run indices and constraints as the last two entries. Each is its own
        # short-lived transaction handled inside ddl_applier so a constraint
        # failure doesn't poison the earlier loads.
        from app.services.ddl_applier import apply_indices as _apply_idx
        from app.services.ddl_applier import apply_constraints as _apply_cons
        for entry, fn in (
            (next(t for t in tables if t.table == INDICES_TABLE), _apply_idx),
            (next(t for t in tables if t.table == CONSTRAINTS_TABLE), _apply_cons),
        ):
            entry.status = "loading"
            _update_status(project_id, tables=tables)
            t0 = time.monotonic()
            try:
                result = fn(schema)
                entry.status = "skipped" if result.get("skipped") else "success"
                msg = result.get("reason", "applied") if result.get("skipped") else "applied"
                _append_log(project_id, f"[{entry.table}] {msg}")
            except Exception as exc:  # noqa: BLE001
                entry.status = "error"
                entry.error = str(exc)
                _append_log(project_id, f"[{entry.table}] ERROR: {exc}")
            finally:
                entry.elapsed = round(time.monotonic() - t0, 2)
                _update_status(project_id, tables=tables)

    overall = "success" if all(t.status in {"success", "skipped"} for t in tables) else "error"
    _update_status(project_id, overall=overall, finished_at=time.time(), tables=tables)
