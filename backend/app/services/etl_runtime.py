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
