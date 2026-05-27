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

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import connection as PGConnection
    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]
    sql = None  # type: ignore[assignment]
    PGConnection = Any  # type: ignore[assignment,misc]
    _PSYCOPG2_AVAILABLE = False


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


# ─── Connection ───────────────────────────────────────────────────────────────

def _connect() -> "PGConnection":
    if not _PSYCOPG2_AVAILABLE:
        raise RuntimeError(
            "psycopg2 is not installed. Install it (pip install psycopg2-binary) "
            "or use the docker-compose stack which bundles it."
        )
    if not settings.omop_db_host:
        raise RuntimeError(
            "OMOP_DB_HOST is not configured. Set OMOP_DB_* in backend/.env."
        )
    return psycopg2.connect(
        host=settings.omop_db_host,
        port=settings.omop_db_port,
        dbname=settings.omop_db_name,
        user=settings.omop_db_user,
        password=settings.omop_db_password,
    )


# ─── Health ───────────────────────────────────────────────────────────────────

def check_db_health() -> dict:
    info: dict[str, Any] = {
        "configured": bool(settings.omop_db_host),
        "connected": False,
        "ddl_applied": False,
        "schemas": [],
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


def load_project_outputs(
    project_id: str,
    output_dir: str,
    schema: str,
    truncate: bool,
    table_order: list[str],
) -> None:
    """Background task. Updates _status_store as it runs."""
    tables = [TableLoadStatus(table=t) for t in table_order]
    _update_status(project_id, schema=schema, tables=tables, overall="running")

    try:
        with _connect() as conn:
            conn.autocommit = False
            _ensure_schema(conn, schema)
            conn.commit()

            for entry in tables:
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

    overall = "success" if all(t.status in {"success", "skipped"} for t in tables) else "error"
    _update_status(project_id, overall=overall, finished_at=time.time(), tables=tables)
