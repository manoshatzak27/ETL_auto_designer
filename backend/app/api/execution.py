from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
import json
import re
import threading
from array import array
from collections import OrderedDict
from pathlib import Path
from typing import BinaryIO, Iterator, Optional
import pandas as pd
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.services.etl_executor import (
    execute_etl_scripts, RUN_LOG_FILENAME, BASIC_RUN_LOG_FILENAME, RUN_STATS_FILENAME, INFO_PREFIX,
    dropped_rows,
)
from app.config import settings

router = APIRouter(prefix="/projects", tags=["execution"])


@router.post("/{project_id}/execute")
async def execute_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.generated_scripts:
        raise HTTPException(status_code=400, detail="No generated scripts. Generate scripts first.")

    output_dir = settings.get_output_path() / project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Respect per-table enabled flag from the Source-step picker. Optional
    # tables that the user un-checked are excluded from execution even if a
    # stale generated_scripts entry exists from before they disabled them.
    # Domain-routed tables (measurement/observation/drug_exposure/procedure/
    # condition_occurrence) have no etl_config entry; they're always allowed.
    config: dict = project.etl_config or {}
    filtered_scripts = {
        table: code
        for table, code in (project.generated_scripts or {}).items()
        if table not in config
        or config[table].get("enabled", True) is not False
    }

    log, status, output_files, stats = await execute_etl_scripts(
        filtered_scripts,
        source_path=project.source_path,
        output_dir=str(output_dir),
        project_id=project_id,
        mapping_files=project.mapping_files or {},
        project_name=project.name,
    )

    project.last_execution_log = log
    project.last_execution_status = status
    project.output_files = output_files
    db.commit()

    return {"status": status, "log": log, "output_files": output_files, "stats": stats}


@router.get("/{project_id}/download/{filename}")
def download_output(project_id: str, filename: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    output_dir = settings.get_output_path() / project_id
    file_path = output_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    safe_name = Path(filename).name
    if safe_name not in [Path(f).name for f in (project.output_files or [])]:
        raise HTTPException(status_code=403, detail="File not in project outputs")

    media_type = "text/x-python" if filename.endswith(".py") else "text/csv"
    return FileResponse(str(file_path), filename=filename, media_type=media_type)


def _run_log_path(
    project_id: str, db: Session, filename: str = RUN_LOG_FILENAME, during_run: bool = False,
) -> Path:
    """Path of a file the last run left in the output dir. A project whose
    execution state was reset has no last run, so its leftover files are
    ignored — unless `during_run`, for files read while a run (maybe the
    project's first) is still in progress."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    path = settings.get_output_path() / project_id / filename
    if (not project.last_execution_status and not during_run) or not path.is_file():
        raise HTTPException(status_code=404, detail="No execution log yet. Execute the pipeline first.")
    return path


@router.get("/{project_id}/execution-log")
def get_execution_log_preview(
    project_id: str,
    limit: int = 5000,
    table: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return the first `limit` lines of the last run's detailed log — of the
    whole run, or only of `table`'s section of it.

    The full log can run to hundreds of MB (one INFO line per empty field on
    every written row), far more than the page can render, so only a preview is
    sent; the rest is in the log viewer (/execution-log/lines) or a download.
    """
    path = _run_log_path(project_id, db)
    limit = max(1, min(limit, 20000))

    head: list[str] = []
    total_lines = 0
    info_lines = 0
    size_bytes = 0
    with open(path, "rb") as fh:
        for section, _, raw in _sections(fh):
            if table and section != table:
                continue
            total_lines += 1
            size_bytes += len(raw)
            if raw.startswith(_INFO_PREFIX_B):
                info_lines += 1
            if total_lines <= limit:
                head.append(raw.decode("utf-8", errors="replace"))

    return {
        "log": "".join(head),
        "total_lines": total_lines,
        "info_lines": info_lines,
        "truncated": total_lines > limit,
        "size_bytes": size_bytes,
    }


@router.get("/{project_id}/execution-log/lines")
def get_execution_log_lines(
    project_id: str,
    view: str = "detailed",
    table: Optional[str] = None,
    kind: str = "all",
    q: Optional[str] = None,
    offset: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db),
):
    """One page of the last run's full log for the log viewer, after filtering
    by table section, `kind` (all | dropped | notes) and a case-insensitive
    search `q`. Each line comes with its line number in the log file; `total`
    is the number of lines that pass the filters.

    The first request for a set of filters scans the file once (about a second
    per million lines); later pages of it read only their own lines."""
    filename = BASIC_RUN_LOG_FILENAME if view == "basic" else RUN_LOG_FILENAME
    path = _run_log_path(project_id, db, filename)
    offset = max(0, offset)
    limit = max(1, min(limit, 5000))

    numbers, positions = _line_index(path, table or None, kind, q.lower() if q else None)
    page: list[dict] = []
    with open(path, "rb") as fh:
        for i in range(offset, min(len(numbers), offset + limit)):
            fh.seek(positions[i])
            text = fh.readline().decode("utf-8", errors="replace").rstrip("\r\n")
            page.append({"n": numbers[i], "text": text})

    return {"lines": page, "total": len(numbers), "offset": offset}


_INFO_PREFIX_B = INFO_PREFIX.encode()
_DROP_PREFIXES_B = (b"WARNING:", b"ERROR:")
_SECTION_START_RE = re.compile(rb"^\[TABLE: (\w+)\] Starting\.\.\.$")

# Line numbers and byte positions of the lines that pass a set of viewer
# filters, keyed by the log file's identity (path, mtime, size — a new run
# rewrites it) and the filters. Kept for the few most recent filter sets, up to
# _INDEX_CACHE_LINES lines in all (12 bytes a line).
_INDEX_CACHE: "OrderedDict[tuple, tuple[array, array]]" = OrderedDict()
_INDEX_CACHE_LINES = 20_000_000
_INDEX_LOCK = threading.Lock()


def _line_index(path: Path, table: Optional[str], kind: str, needle: Optional[str]) -> tuple[array, array]:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, table, kind, needle)
    with _INDEX_LOCK:
        if key in _INDEX_CACHE:
            _INDEX_CACHE.move_to_end(key)
            return _INDEX_CACHE[key]

    # An ASCII search can compare raw bytes, skipping the decode of every line.
    needle_b = needle.encode() if needle and needle.isascii() else None
    numbers = array("I")
    positions = array("q")
    with open(path, "rb") as fh:
        for n, (section, pos, raw) in enumerate(_sections(fh), start=1):
            if table and section != table:
                continue
            if kind == "dropped" and not (
                raw.startswith(_DROP_PREFIXES_B) and dropped_rows(raw.decode("utf-8", errors="replace"))
            ):
                continue
            if kind == "notes" and not raw.startswith(_INFO_PREFIX_B):
                continue
            if needle_b is not None:
                if needle_b not in raw.lower():
                    continue
            elif needle and needle not in raw.decode("utf-8", errors="replace").lower():
                continue
            numbers.append(n)
            positions.append(pos)

    with _INDEX_LOCK:
        _INDEX_CACHE[key] = (numbers, positions)
        while len(_INDEX_CACHE) > 1 and sum(len(v[0]) for v in _INDEX_CACHE.values()) > _INDEX_CACHE_LINES:
            _INDEX_CACHE.popitem(last=False)
    return numbers, positions


def _sections(fh: BinaryIO) -> Iterator[tuple[Optional[str], int, bytes]]:
    """Tag each line of a log opened in binary mode with the table whose
    section it's in and its byte position. A section opens with the executor's
    banner (blank line, rule, "[TABLE: x] Starting...", rule); the blank line
    and rule before the "Starting" line are held back until it's clear whether
    they open the next section."""
    current: Optional[str] = None
    pending: list[tuple[int, bytes]] = []
    pos = 0
    for raw in fh:
        stripped = raw.rstrip(b"\r\n")
        if stripped.startswith(b"[TABLE: ") and (m := _SECTION_START_RE.match(stripped)):
            current = m.group(1).decode()
        elif not stripped.strip(b"="):
            pending.append((pos, raw))
            pos += len(raw)
            continue
        for held_pos, held in pending:
            yield current, held_pos, held
        pending.clear()
        yield current, pos, raw
        pos += len(raw)
    for held_pos, held in pending:
        yield current, held_pos, held


@router.get("/{project_id}/execution-stats")
def get_execution_stats(project_id: str, during_run: bool = False, db: Session = Depends(get_db)):
    """Per-table counts of the last run: status, rows written, dropped rows,
    warnings, INFO lines. Rewritten as the run goes, so the page polls this
    (with during_run) for progress."""
    path = _run_log_path(project_id, db, RUN_STATS_FILENAME, during_run)
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{project_id}/execution-log/download")
def download_execution_log(project_id: str, view: str = "detailed", db: Session = Depends(get_db)):
    filename = BASIC_RUN_LOG_FILENAME if view == "basic" else RUN_LOG_FILENAME
    path = _run_log_path(project_id, db, filename)
    return FileResponse(str(path), filename=filename, media_type="text/plain")


@router.get("/{project_id}/output-preview")
def get_output_preview(
    project_id: str,
    filename: str,
    rows: int = 20,
    db: Session = Depends(get_db),
):
    """Return the first N rows of a generated OMOP CSV as JSON.
    Mirrors source-preview's shape: { columns, rows, total_rows }.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    safe_name = Path(filename).name
    if safe_name not in [Path(f).name for f in (project.output_files or [])]:
        raise HTTPException(status_code=403, detail="File not in project outputs")

    file_path = settings.get_output_path() / project_id / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    n_rows = max(1, min(rows, 500))
    try:
        df = pd.read_csv(
            file_path,
            sep=";",
            encoding="utf-8",
            nrows=n_rows,
            dtype=str,
            keep_default_na=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read CSV: {exc}")

    # Cheap total-row count (sum of \n in the file minus the header line).
    # Output CSVs are bounded by source size so this is fine.
    try:
        with open(file_path, "rb") as fh:
            total_lines = sum(1 for _ in fh)
        total_rows = max(0, total_lines - 1)
    except Exception:
        total_rows = len(df)

    return {
        "columns": [str(c) for c in df.columns.tolist()],
        "rows": df.to_dict(orient="records"),
        "total_rows": total_rows,
    }
