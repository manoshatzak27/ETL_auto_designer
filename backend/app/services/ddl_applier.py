"""
OMOP DDL applier.

Splits the OHDSI v5.4 DDL file (which intermingles vocab + clinical tables in
one @cdmDatabaseSchema-templated SQL file) into two buckets and applies each
into a target Postgres schema. Idempotent via per-schema marker rows.

Buckets:
  - vocab    → vocabulary tables (concept, vocabulary, …) – goes into the
               shared `vocab` schema by default
  - clinical → everything else (person, visit_occurrence, …) – goes into the
               user-chosen clinical schema (e.g. `cdm` or `cdm_<project_id>`)

Marker table per schema:
  CREATE TABLE <schema>.__ddl_marker(kind text PRIMARY KEY, applied_at timestamptz)
where kind ∈ {"vocab", "clinical", "indices", "constraints"}.

Public API:
  apply_schema_ddl(schema, kind, *, force=False)
  apply_indices(schema, *, force=False)
  apply_constraints(schema, *, force=False)
  is_ddl_applied(schema, kind) -> bool

CLI:
  python -m app.services.ddl_applier bootstrap
    Creates the vocab + clinical schemas configured in settings and applies
    DDL into each. Idempotent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

from app.config import settings
from app.services.db import connect, sql


# ─── DDL layout ────────────────────────────────────────────────────────────────

DDL_DIR = Path(__file__).resolve().parents[2] / "omop_ddl"
DDL_FILE = DDL_DIR / "OMOPCDM_postgresql_5.4_ddl.sql"
PK_FILE = DDL_DIR / "OMOPCDM_postgresql_5.4_primary_keys.sql"
INDICES_FILE = DDL_DIR / "OMOPCDM_postgresql_5.4_indices.sql"
CONSTRAINTS_FILE = DDL_DIR / "OMOPCDM_postgresql_5.4_constraints.sql"

VOCAB_TABLES: set[str] = {
    "vocabulary",
    "domain",
    "concept_class",
    "concept",
    "concept_relationship",
    "relationship",
    "concept_synonym",
    "concept_ancestor",
    "drug_strength",
    "source_to_concept_map",
}

_TABLE_BLOCK = re.compile(
    r"CREATE\s+TABLE\s+@cdmDatabaseSchema\.(\w+)\b[^;]*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_BLOCK = re.compile(
    r"ALTER\s+TABLE\s+@cdmDatabaseSchema\.(\w+)\b[^;]*;",
    re.IGNORECASE | re.DOTALL,
)
_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Kind = Literal["vocab", "clinical"]


def _validate_schema(schema: str) -> None:
    if not _SCHEMA_NAME.match(schema):
        raise ValueError(f"Invalid Postgres identifier for schema: {schema!r}")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _bucket(text: str, regex: re.Pattern[str]) -> tuple[str, str]:
    vocab: list[str] = []
    clinical: list[str] = []
    for match in regex.finditer(text):
        name = match.group(1).lower()
        block = match.group(0)
        (vocab if name in VOCAB_TABLES else clinical).append(block)
    return "\n\n".join(vocab), "\n\n".join(clinical)


# Parse the on-disk DDL once at import time. Cheap and avoids re-reading per
# request. If files are rotated at runtime, restart the backend.
_DDL_VOCAB, _DDL_CLINICAL = _bucket(_read(DDL_FILE), _TABLE_BLOCK)
_PK_VOCAB, _PK_CLINICAL = _bucket(_read(PK_FILE), _ALTER_BLOCK)


# ─── Markers ──────────────────────────────────────────────────────────────────

_MARKER_TABLE = "__ddl_marker"


def _ensure_marker_table(conn, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {schema}.{marker} ("
                "  kind text PRIMARY KEY,"
                "  applied_at timestamptz NOT NULL DEFAULT now()"
                ")"
            ).format(
                schema=sql.Identifier(schema),
                marker=sql.Identifier(_MARKER_TABLE),
            )
        )


def _marker_present(conn, schema: str, kind: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = %s AND table_name = %s"
            ")",
            (schema, _MARKER_TABLE),
        )
        if not cur.fetchone()[0]:
            return False
        cur.execute(
            sql.SQL("SELECT 1 FROM {schema}.{marker} WHERE kind = %s").format(
                schema=sql.Identifier(schema),
                marker=sql.Identifier(_MARKER_TABLE),
            ),
            (kind,),
        )
        return cur.fetchone() is not None


def _set_marker(conn, schema: str, kind: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {schema}.{marker}(kind) VALUES (%s) "
                "ON CONFLICT (kind) DO UPDATE SET applied_at = now()"
            ).format(
                schema=sql.Identifier(schema),
                marker=sql.Identifier(_MARKER_TABLE),
            ),
            (kind,),
        )


def is_ddl_applied(schema: str, kind: str) -> bool:
    _validate_schema(schema)
    try:
        with connect() as conn:
            return _marker_present(conn, schema, kind)
    except Exception:
        return False


# ─── Apply helpers ────────────────────────────────────────────────────────────

def _ensure_schema(conn, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(
                schema=sql.Identifier(schema)
            )
        )


def _apply_substituted(conn, schema: str, raw_sql: str) -> None:
    """Substitute @cdmDatabaseSchema with the quoted schema name and execute as one batch."""
    if not raw_sql.strip():
        return
    substituted = raw_sql.replace("@cdmDatabaseSchema", f'"{schema}"')
    with conn.cursor() as cur:
        cur.execute(substituted)


def _has_real_sql(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


# ─── Public API ───────────────────────────────────────────────────────────────

def apply_schema_ddl(
    schema: str,
    kind: Kind,
    *,
    force: bool = False,
) -> dict:
    """Create the schema (if missing) and apply the bucketed DDL + PKs.
    Idempotent via the marker table. `force=True` skips the marker check
    (caller is responsible for having dropped the schema first)."""
    _validate_schema(schema)
    ddl = _DDL_VOCAB if kind == "vocab" else _DDL_CLINICAL
    pks = _PK_VOCAB if kind == "vocab" else _PK_CLINICAL

    with connect() as conn:
        conn.autocommit = False
        _ensure_schema(conn, schema)
        conn.commit()

        if not force and _marker_present(conn, schema, kind):
            return {
                "schema": schema,
                "kind": kind,
                "skipped": True,
                "reason": "marker present",
            }

        try:
            _apply_substituted(conn, schema, ddl)
            _apply_substituted(conn, schema, pks)
            _ensure_marker_table(conn, schema)
            _set_marker(conn, schema, kind)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"schema": schema, "kind": kind, "skipped": False}


def apply_indices(schema: str, *, force: bool = False) -> dict:
    _validate_schema(schema)
    text = _read(INDICES_FILE)
    if not _has_real_sql(text):
        return {"schema": schema, "kind": "indices", "skipped": True, "reason": "no SQL"}

    with connect() as conn:
        conn.autocommit = False
        _ensure_schema(conn, schema)
        _ensure_marker_table(conn, schema)
        conn.commit()
        if not force and _marker_present(conn, schema, "indices"):
            return {"schema": schema, "kind": "indices", "skipped": True, "reason": "marker present"}
        try:
            _apply_substituted(conn, schema, text)
            _set_marker(conn, schema, "indices")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"schema": schema, "kind": "indices", "skipped": False}


def apply_constraints(schema: str, *, force: bool = False) -> dict:
    _validate_schema(schema)
    text = _read(CONSTRAINTS_FILE)
    if not _has_real_sql(text):
        return {
            "schema": schema,
            "kind": "constraints",
            "skipped": True,
            "reason": "no SQL in constraints file (vendored file is intentionally empty)",
        }

    with connect() as conn:
        conn.autocommit = False
        _ensure_schema(conn, schema)
        _ensure_marker_table(conn, schema)
        conn.commit()
        if not force and _marker_present(conn, schema, "constraints"):
            return {"schema": schema, "kind": "constraints", "skipped": True, "reason": "marker present"}
        try:
            _apply_substituted(conn, schema, text)
            _set_marker(conn, schema, "constraints")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"schema": schema, "kind": "constraints", "skipped": False}


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _bootstrap() -> int:
    vocab_schema = settings.omop_vocab_schema or "vocab"
    clinical_schema = settings.omop_default_schema or "cdm"
    for kind, schema in (("vocab", vocab_schema), ("clinical", clinical_schema)):
        result = apply_schema_ddl(schema, kind)
        verb = "skipped" if result.get("skipped") else "applied"
        print(f"[ddl_applier] {kind} DDL → {schema}: {verb}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "-h" or argv[1] == "--help":
        print("usage: python -m app.services.ddl_applier bootstrap", file=sys.stderr)
        return 0 if len(argv) >= 2 and argv[1] in {"-h", "--help"} else 2
    cmd = argv[1]
    if cmd == "bootstrap":
        return _bootstrap()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
