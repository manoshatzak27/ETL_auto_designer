"""
Shared runtime helpers for generated OMOP ETL table scripts.

etl_executor.py copies this file alongside each generated script into the
project's output directory before execution, so `from etl_runtime import
_info, _read_str` resolves via the script's own directory on sys.path
(Python adds the directory of the script being run to sys.path[0]).
"""
import os
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
