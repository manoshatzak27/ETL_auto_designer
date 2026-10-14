"""Read a data dictionary — one row per source column, with its description.

The Concepts step's bulk matcher is only as good as what it is given: a column
*name* is an identifier (`DOC_PAT_HAIR`, `creat_mgdl`), while a description is
prose a vocabulary can actually be searched with. This module turns whatever
spreadsheet the user has lying around into `{column_name: description}`.

Deliberately liberal about the input, because a data dictionary is rarely
written for us: several spellings of each header are accepted, the delimiter and
encoding are sniffed the same way an uploaded source CSV's are, Excel is read
as readily as CSV, and a column name is matched to the project's columns by
case and punctuation as a fallback. What is *not* guessed is which column a row
refers to when nothing matches — those rows come back in `unmatched` so the
caller can show them rather than silently dropping them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.schema_inferrer import _detect_delimiter, _detect_encoding

# Header spellings, lowercased, in priority order. The first one present wins,
# so a sheet carrying both `column` and `name` uses `column`.
NAME_HEADERS = (
    "column_name", "column name", "columnname", "column",
    "variable_name", "variable name", "variable",
    "field_name", "field name", "field",
    "name",
)
DESCRIPTION_HEADERS = (
    "description", "column_description", "column description",
    "definition", "meaning", "label", "desc", "comment", "comments",
)
TABLE_HEADERS = ("table", "table_name", "table name", "file", "filename", "source_file")

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


def _normalize(name: str) -> str:
    """Fold a column name for fallback matching: case, spaces and punctuation.

    `Patient ID`, `patient_id` and `patient-id` are the same column written by
    three different people; nothing else here would connect them.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _pick_header(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {str(c).strip().lower(): c for c in columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    return None


def _read_table(path: Path) -> pd.DataFrame:
    """Load the dictionary file as strings, CSV or Excel."""
    if path.suffix.lower() in EXCEL_SUFFIXES:
        return pd.read_excel(path, dtype=str)

    encoding = _detect_encoding(str(path))
    delimiter = _detect_delimiter(str(path), encoding)
    for enc in (encoding, "utf-8-sig", "utf-8", "windows-1252", "latin-1"):
        try:
            return pd.read_csv(path, sep=delimiter, encoding=enc, dtype=str,
                               on_bad_lines="skip")
        except Exception:
            continue
    # Last resort: never let an encoding quirk lose the whole upload.
    return pd.read_csv(path, sep=delimiter, encoding="utf-8", dtype=str,
                       encoding_errors="replace", on_bad_lines="skip")


def parse_descriptions(path: Path, known_columns: list[str]) -> dict[str, Any]:
    """Map a data-dictionary file onto the project's columns.

    Returns ``{descriptions, matched, unmatched, headers}`` where `descriptions`
    is keyed by the project's *own* spelling of each column (not the file's), so
    the result can be merged straight into `project.column_descriptions`.
    Rows whose column could not be identified are listed in `unmatched`.
    """
    df = _read_table(path)
    if df.empty:
        raise ValueError("The file has no rows.")

    headers = [str(c) for c in df.columns]
    name_col = _pick_header(headers, NAME_HEADERS)
    desc_col = _pick_header(headers, DESCRIPTION_HEADERS)
    table_col = _pick_header(headers, TABLE_HEADERS)

    if name_col is None or desc_col is None:
        missing = "a column name" if name_col is None else "a description"
        raise ValueError(
            f"Couldn't find {missing} column. Expected a header row with one of "
            f"{', '.join(NAME_HEADERS[:4])}… and one of "
            f"{', '.join(DESCRIPTION_HEADERS[:4])}…; found: {', '.join(headers)}"
        )

    exact = {c: c for c in known_columns}
    folded: dict[str, str] = {}
    for c in known_columns:
        # First spelling wins, so an ambiguous fold never silently reassigns a
        # description from one real column to another.
        folded.setdefault(_normalize(c), c)

    descriptions: dict[str, str] = {}
    matched: list[str] = []
    unmatched: list[dict[str, str]] = []

    for _, row in df.iterrows():
        raw_name = str(row.get(name_col) or "").strip()
        description = str(row.get(desc_col) or "").strip()
        if not raw_name or raw_name.lower() == "nan":
            continue
        if not description or description.lower() == "nan":
            continue

        column = exact.get(raw_name) or folded.get(_normalize(raw_name))
        if column is None:
            unmatched.append({
                "name": raw_name,
                "description": description,
                "table": str(row.get(table_col) or "").strip() if table_col else "",
            })
            continue
        if column not in descriptions:
            descriptions[column] = description
            matched.append(column)

    return {
        "descriptions": descriptions,
        "matched": matched,
        "unmatched": unmatched,
        "headers": {"name": name_col, "description": desc_col, "table": table_col},
    }
