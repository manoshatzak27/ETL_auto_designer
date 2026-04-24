"""
Infers delimiter, encoding, column names and row count from an uploaded CSV file.
"""
import chardet
import pandas as pd
from pathlib import Path


DELIMITERS = [";", ",", "\t", "|"]
ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "windows-1252", "latin-1", "cp1253"]


def _detect_encoding(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read(65536)
    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "utf-8"
    # normalise common aliases
    enc = enc.lower().replace("-", "_")
    if enc in ("utf_8_sig", "utf_8"):
        return "utf-8"
    if enc in ("windows_1252", "cp1252"):
        return "windows-1252"
    return detected.get("encoding") or "utf-8"


def _detect_delimiter(path: str, encoding: str) -> str:
    with open(path, "r", encoding=encoding, errors="replace") as f:
        first_line = f.readline()
    counts = {d: first_line.count(d) for d in DELIMITERS}
    return max(counts, key=counts.get)


def infer_schema(path: str) -> dict:
    encoding = _detect_encoding(path)
    delimiter = _detect_delimiter(path, encoding)

    # try all encodings if first attempt fails
    df = None
    for enc in [encoding] + ENCODINGS_TO_TRY:
        try:
            df = pd.read_csv(path, sep=delimiter, encoding=enc, nrows=5, dtype=str)
            encoding = enc
            break
        except Exception:
            continue

    if df is None:
        df = pd.read_csv(path, sep=delimiter, encoding="utf-8", on_bad_lines="skip", nrows=5, dtype=str)

    # get row count without loading whole file
    row_count = 0
    try:
        with open(path, "r", encoding=encoding, errors="replace") as f:
            row_count = sum(1 for _ in f) - 1  # subtract header
    except Exception:
        pass

    return {
        "delimiter": delimiter,
        "encoding": encoding,
        "columns": list(df.columns),
        "row_count": max(row_count, 0),
    }
