from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
import json
import re
from pathlib import Path
from typing import Iterable, Iterator, Optional
import pandas as pd
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.services.etl_executor import (
    execute_etl_scripts, RUN_LOG_FILENAME, BASIC_RUN_LOG_FILENAME, RUN_STATS_FILENAME, INFO_PREFIX,
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
    sent; the rest is available through /execution-log/download.
    """
    path = _run_log_path(project_id, db)
    limit = max(1, min(limit, 20000))

    head: list[str] = []
    total_lines = 0
    info_lines = 0
    size_bytes = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for section, line in _sections(fh):
            if table and section != table:
                continue
            total_lines += 1
            size_bytes += len(line.encode("utf-8"))
            if line.startswith(INFO_PREFIX):
                info_lines += 1
            if total_lines <= limit:
                head.append(line)

    return {
        "log": "".join(head),
        "total_lines": total_lines,
        "info_lines": info_lines,
        "truncated": total_lines > limit,
        "size_bytes": size_bytes,
    }


_SECTION_START_RE = re.compile(r"^\[TABLE: (\w+)\] Starting\.\.\.$")


def _sections(lines: Iterable[str]) -> Iterator[tuple[Optional[str], str]]:
    """Tag each log line with the table whose section it's in. A section opens
    with the executor's banner (blank line, rule, "[TABLE: x] Starting...",
    rule); the blank line and rule before the "Starting" line are held back
    until it's clear whether they open the next section."""
    current: Optional[str] = None
    pending: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        if m := _SECTION_START_RE.match(stripped):
            current = m.group(1)
        elif stripped == "" or set(stripped) == {"="}:
            pending.append(line)
            continue
        for held in pending:
            yield current, held
        pending.clear()
        yield current, line
    for held in pending:
        yield current, held


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
