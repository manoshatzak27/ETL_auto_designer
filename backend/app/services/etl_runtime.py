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
