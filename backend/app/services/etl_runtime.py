"""
Shared runtime helpers for generated OMOP ETL table scripts.

etl_executor.py copies this file alongside each generated script into the
project's output directory before execution, so `from etl_runtime import
_info, _read_str` resolves via the script's own directory on sys.path
(Python adds the directory of the script being run to sys.path[0]).
"""
import os
from datetime import datetime

import pandas as pd

VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'


def _info(msg):
    """Print diagnostic message only in detailed output mode."""
    if VERBOSE:
        print(msg)


def _read_str(row, col):
    val = row.get(col)
    result = (str(val).strip() or None) if pd.notnull(val) else None
    if result is None:
        _info(f"INFO: column {col!r} is empty for this row")
    return result


def _write_omop_csv(df, output_file):
    """Write an OMOP table DataFrame to semicolon-delimited CSV.

    A column that's populated for some rows and null for others gets upcast
    by pandas to float64 (plain int64 has no way to represent a missing
    value), so a valid id like 9448 round-trips through to_csv as "9448.0"
    — which Postgres COPY rejects for an integer column ('invalid input
    syntax for type integer'). Any float64 column whose non-null values are
    all whole numbers is cast to the nullable Int64 dtype first, so it
    serializes as a clean "9448" (and "" for null) instead. A column with
    genuine fractional values (value_as_number, range_low, ...) is left
    untouched.
    """
    for col in df.columns:
        if df[col].dtype == 'float64':
            non_null = df[col].dropna()
            if not non_null.empty and (non_null % 1 == 0).all():
                df[col] = df[col].astype('Int64')
    df.to_csv(output_file, sep=';', index=False, encoding='utf-8')


def number_visits_by_date(df, pid_col, date_specs, counters):
    """Auto-number each person's visits (visit1, visit2, ...) in visit-date order.

    Used in multi-row mode when no visit identifier column exists. Both the
    visit_occurrence and stem_table scripts call this so they derive the same
    number for the same row — the stem table joins on
    f'{person_source_value}-visit{n}'.

    date_specs: one (date_col, date_format) pair per visit definition.
    counters:   person_source_value -> highest number already used; updated in
                place so numbering continues across source files.

    Returns {(row position, definition index) -> n}, where row position is
    1-based like the scripts' _src_idx. Rows on the same date keep their
    source order; a row with a missing or unparseable date gets no number
    (the visit script skips it anyway).
    """
    entries = []
    for src_idx, (_, row) in enumerate(df.iterrows(), start=1):
        if pid_col:
            pid_raw = row.get(pid_col)
            if pd.isnull(pid_raw):
                continue
            person_source_value = str(pid_raw)
        else:
            person_source_value = str(src_idx)
        for def_idx, (date_col, date_fmt) in enumerate(date_specs):
            raw = row.get(date_col) if date_col else None
            date_str = str(raw).strip() if pd.notnull(raw) else ''
            if not date_str or date_str == 'nan':
                continue
            try:
                visit_date = datetime.strptime(date_str, date_fmt or '%Y-%m-%d')
            except ValueError:
                continue
            entries.append((visit_date, src_idx, def_idx, person_source_value))

    numbers = {}
    for _, src_idx, def_idx, person_source_value in sorted(entries, key=lambda e: e[:3]):
        counters[person_source_value] = counters.get(person_source_value, 0) + 1
        numbers[(src_idx, def_idx)] = counters[person_source_value]
    return numbers


def build_id_lookup(output_dir, filename, key_col, id_col, delimiter=';'):
    """Build {key_col value (as str) -> id_col value} from an already-generated
    OMOP table CSV in output_dir (e.g. location.csv, care_site.csv,
    provider.csv, person.csv, visit_occurrence.csv). Every generated script
    that needs to resolve a foreign key written by an earlier ETL step
    (location_id, care_site_id, provider_id, person_id,
    visit_occurrence_id, ...) goes through this one function instead of
    each hand-rolling its own CSV-to-dict loader.

    Returns {} if the file doesn't exist yet (the upstream step hasn't run)
    or can't be parsed — callers already treat a missing key as a lookup
    miss, so this degrades the same way a script whose dependency ran
    successfully but simply has no matching row would.
    """
    path = os.path.join(output_dir, filename)
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_csv(path, delimiter=delimiter, encoding='utf-8')
        return dict(zip(df[key_col].astype(str), df[id_col]))
    except Exception as e:
        print(f'WARNING: could not load {filename}: {e}')
        return {}
