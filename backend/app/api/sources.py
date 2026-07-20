import io
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse, UploadSourcesResponse
from app.services.schema_inferrer import infer_schema, detect_pid_transform
from app.config import settings

router = APIRouter(prefix="/projects", tags=["sources"])

# Informational only — the frontend uses this (mirrored) to warn before
# loading a file into the non-virtualized editable grid. Not enforced here.
SOURCE_CONTENT_WARN_THRESHOLD = 20

MAPPING_FILENAMES = {
    "variable_mapping": "variable_mapping.csv",
    "value_mapping": "value_mapping.csv",
    "variable_value_mapping": "variable_value_mapping.csv",
    "custom_mappings": "custom_mappings.csv",
}


@router.post("/{project_id}/upload-source", response_model=ProjectResponse)
async def upload_source(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # file.filename can be None in some browsers — use a safe fallback
    safe_name = Path(file.filename).name if file.filename else "source.csv"

    project_upload_dir = settings.get_upload_path() / project_id
    project_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = project_upload_dir / safe_name

    contents = await file.read()
    dest.write_bytes(contents)

    schema = infer_schema(str(dest))

    project.source_files = []
    _sync_legacy_fields(project, [{
        "filename": safe_name,
        "path": str(dest),
        "delimiter": schema["delimiter"],
        "encoding": schema["encoding"],
        "columns": schema["columns"],
        "row_count": schema["row_count"],
        "size_bytes": dest.stat().st_size,
    }])
    _reset_project_state(project)
    db.commit()
    db.refresh(project)
    return project


_SOURCE_EXTENSIONS = {".csv", ".tsv", ".txt"}


def _reset_project_state(project: Project) -> None:
    project.concept_decisions = {}
    project.etl_config = {}
    project.generated_scripts = {}
    project.mapping_files = {}
    project.generated_code = ""
    project.last_execution_log = ""
    project.last_execution_status = ""
    project.output_files = []


def _decision_is_meaningful(decision: Any) -> bool:
    """A decision that was never touched by the user (still 'skip', no mappings)
    isn't worth flagging as a conflict even if its column disappears."""
    if not isinstance(decision, dict):
        return False
    if decision.get("strategy") not in (None, "", "skip"):
        return True
    if decision.get("variable_concept"):
        return True
    if decision.get("value_concepts"):
        return True
    if (decision.get("unit_mapping") or {}).get("unit_concepts"):
        return True
    if (decision.get("route_mapping") or {}).get("route_concepts"):
        return True
    return False


def _find_decision_conflicts(concept_decisions: dict, removed_columns: set[str]) -> list[dict]:
    """concept_decisions is keyed by bare column name. When a replaced file drops
    columns, find decisions that reference those columns — either as the mapped
    column itself, or as the sibling column supplying unit/route values — so the
    user can be told exactly what needs remapping."""
    if not removed_columns:
        return []
    conflicts: list[dict] = []
    for col, decision in (concept_decisions or {}).items():
        if not _decision_is_meaningful(decision):
            continue
        reasons = []
        if col in removed_columns:
            reasons.append(f"column '{col}' no longer exists in the updated file")
        unit_col = (decision.get("unit_mapping") or {}).get("unit_col")
        if unit_col and unit_col in removed_columns:
            reasons.append(f"its unit column '{unit_col}' no longer exists in the updated file")
        route_col = (decision.get("route_mapping") or {}).get("route_col")
        if route_col and route_col in removed_columns:
            reasons.append(f"its route column '{route_col}' no longer exists in the updated file")
        if reasons:
            conflicts.append({"column": col, "reason": "; ".join(reasons)})
    return conflicts


def _sync_legacy_fields(project: Project, source_files: list[dict]) -> None:
    """Keep the old single-file columns in sync with the first entry in source_files."""
    if source_files:
        first = source_files[0]
        project.source_filename = first["filename"]
        project.source_path = first["path"]
        project.source_delimiter = first["delimiter"]
        project.source_encoding = first["encoding"]
        project.source_columns = first["columns"]
        project.source_row_count = first["row_count"]
    else:
        project.source_filename = ""
        project.source_path = ""
        project.source_delimiter = ""
        project.source_encoding = ""
        project.source_columns = []
        project.source_row_count = 0


@router.post("/{project_id}/upload-sources", response_model=UploadSourcesResponse)
async def upload_sources(
    project_id: str,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Accept one or more CSV/TSV files, OR a single ZIP containing CSV/TSV files."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_upload_dir = settings.get_upload_path() / project_id
    project_upload_dir.mkdir(parents=True, exist_ok=True)

    # Collect raw paths to process (extracts from ZIP when needed)
    raw_paths: list[Path] = []
    for upload in files:
        safe_name = Path(upload.filename).name if upload.filename else "source.csv"
        contents = await upload.read()

        if Path(safe_name).suffix.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(contents)) as zf:
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.suffix.lower() in _SOURCE_EXTENSIONS and not member_path.name.startswith("__"):
                        dest = project_upload_dir / member_path.name
                        dest.write_bytes(zf.read(member))
                        raw_paths.append(dest)
        elif Path(safe_name).suffix.lower() in _SOURCE_EXTENSIONS:
            dest = project_upload_dir / safe_name
            dest.write_bytes(contents)
            raw_paths.append(dest)

    if not raw_paths:
        raise HTTPException(status_code=400, detail="No CSV/TSV files found in the uploaded content.")

    # Build a dict keyed by filename so re-uploading a file replaces its entry
    had_existing_files = bool(project.source_files)
    existing: dict[str, dict] = {f["filename"]: f for f in (project.source_files or [])}
    # Track files that are being replaced (same filename already present) so we
    # can diff their old vs. new columns and warn about mapping choices that no
    # longer apply, instead of silently dropping or hiding them.
    replaced_old_entries: dict[str, dict] = {}
    for path in raw_paths:
        schema = infer_schema(str(path))
        if path.name in existing:
            replaced_old_entries[path.name] = existing[path.name]
        existing[path.name] = {
            "filename": path.name,
            "path": str(path),
            "delimiter": schema["delimiter"],
            "encoding": schema["encoding"],
            "columns": schema["columns"],
            "row_count": schema["row_count"],
            "size_bytes": path.stat().st_size,
        }

    merged = list(existing.values())
    project.source_files = merged
    _sync_legacy_fields(project, merged)
    # Only wipe downstream mapping/config/execution state on the *first* upload for
    # this project. Adding an extra file, or replacing one with a newer version,
    # should preserve existing concept decisions, mappings, and generated code —
    # concept_decisions is keyed by column name, so unaffected columns keep their
    # mapping automatically.
    if not had_existing_files:
        _reset_project_state(project)

    removed_columns: set[str] = set()
    for filename, old_entry in replaced_old_entries.items():
        old_cols = set(old_entry.get("columns") or [])
        new_cols = set(existing[filename].get("columns") or [])
        removed_columns |= (old_cols - new_cols)
    conflicts = _find_decision_conflicts(project.concept_decisions or {}, removed_columns)

    db.commit()
    db.refresh(project)
    return {"project": project, "conflicts": conflicts}


@router.delete("/{project_id}/source-files/{index}", response_model=ProjectResponse)
def delete_source_file(
    project_id: str,
    index: int,
    db: Session = Depends(get_db),
):
    """Remove a source file from source_files by index."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    files = list(project.source_files or [])
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=400, detail="Invalid file index")

    removed = files.pop(index)
    # Best-effort: delete the file from disk
    try:
        Path(removed["path"]).unlink(missing_ok=True)
    except Exception:
        pass

    project.source_files = files
    _sync_legacy_fields(project, files)
    db.commit()
    db.refresh(project)
    return project


def _find_source_file_entry(project: Project, filename: str) -> dict:
    entry = next((f for f in (project.source_files or []) if f.get("filename") == filename), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Source file not found")
    if not Path(entry["path"]).exists():
        raise HTTPException(status_code=404, detail="Source file missing from disk")
    return entry


@router.get("/{project_id}/source-files/{filename}/download")
def download_source_file(project_id: str, filename: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = _find_source_file_entry(project, filename)
    return FileResponse(entry["path"], filename=entry["filename"], media_type="text/csv")


class SourceFileContentResponse(BaseModel):
    filename: str
    delimiter: str
    encoding: str
    columns: List[str]
    rows: List[dict]
    row_count: int


@router.get("/{project_id}/source-file-content", response_model=SourceFileContentResponse)
def get_source_file_content(project_id: str, filename: str, rows: int | None = None, db: Session = Depends(get_db)):
    """`rows`, when given, caps how many data rows are read — used for the
    20-row preview so opening a huge file doesn't load it all into memory."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = _find_source_file_entry(project, filename)

    import pandas as pd
    try:
        df = pd.read_csv(
            entry["path"],
            sep=entry.get("delimiter", ","),
            encoding=entry.get("encoding", "utf-8"),
            dtype=str,
            keep_default_na=False,
            on_bad_lines="skip",
            nrows=rows,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {exc}")

    return {
        "filename": filename,
        "delimiter": entry.get("delimiter", ","),
        "encoding": entry.get("encoding", "utf-8"),
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
        "row_count": entry.get("row_count", len(df)),
    }


class SourceFileContentPayload(BaseModel):
    columns: List[str]
    rows: List[dict[str, Any]]


@router.put("/{project_id}/source-file-content", response_model=ProjectResponse)
def update_source_file_content(
    project_id: str,
    filename: str,
    payload: SourceFileContentPayload,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = _find_source_file_entry(project, filename)

    columns = [c.strip() for c in payload.columns]
    if not columns or any(not c for c in columns):
        raise HTTPException(status_code=400, detail="Column names must be non-empty")
    seen = set()
    for c in columns:
        if c in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate column name: {c}")
        seen.add(c)

    import pandas as pd
    df = pd.DataFrame(payload.rows, columns=columns).fillna("")

    path = Path(entry["path"])
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        df.to_csv(tmp_path, sep=entry.get("delimiter", ","), encoding=entry.get("encoding", "utf-8"), index=False)
        schema = infer_schema(str(tmp_path))
        os.replace(tmp_path, path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}")

    updated_entry = {
        "filename": filename,
        "path": str(path),
        "delimiter": schema["delimiter"],
        "encoding": schema["encoding"],
        "columns": schema["columns"],
        "row_count": schema["row_count"],
        "size_bytes": path.stat().st_size,
    }
    source_files = [updated_entry if f.get("filename") == filename else f for f in (project.source_files or [])]
    project.source_files = source_files
    _sync_legacy_fields(project, source_files)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/upload-mapping", response_model=ProjectResponse)
async def upload_mapping_csv(
    project_id: str,
    mapping_type: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a single mapping CSV manually."""
    if mapping_type not in MAPPING_FILENAMES:
        raise HTTPException(status_code=400, detail=f"mapping_type must be one of {list(MAPPING_FILENAMES)}")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_mapping_dir = settings.get_upload_path() / project_id / "mappings"
    project_mapping_dir.mkdir(parents=True, exist_ok=True)
    dest = project_mapping_dir / f"{mapping_type}.csv"

    contents = await file.read()
    dest.write_bytes(contents)

    mapping_files = dict(project.mapping_files or {})
    mapping_files[mapping_type] = str(dest)
    project.mapping_files = mapping_files
    db.commit()
    db.refresh(project)
    return project


class LoadMappingsFromDirRequest(BaseModel):
    directory: str


@router.post("/{project_id}/load-mappings-from-dir", response_model=ProjectResponse)
def load_mappings_from_dir(
    project_id: str,
    payload: LoadMappingsFromDirRequest,
    db: Session = Depends(get_db),
):
    """
    Read variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv
    (and optionally custom_mappings.csv) directly from a local directory path.
    This is used when the files were produced by the omop-docker-package auto-etl tool.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    source_dir = Path(payload.directory).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {payload.directory}")

    # Restrict to an allow-listed base — defaults to the project's upload dir if
    # MAPPINGS_BUNDLE_ROOT is unset. This prevents arbitrary host-path reads.
    allowed_root_str = getattr(settings, "mappings_bundle_root", "") or str(settings.get_upload_path())
    allowed_root = Path(allowed_root_str).resolve()
    if allowed_root not in source_dir.parents and source_dir != allowed_root:
        raise HTTPException(
            status_code=403,
            detail=f"Directory must be inside MAPPINGS_BUNDLE_ROOT ({allowed_root}).",
        )

    # Copy each found CSV into the project's managed mappings folder
    project_mapping_dir = settings.get_upload_path() / project_id / "mappings"
    project_mapping_dir.mkdir(parents=True, exist_ok=True)

    mapping_files = dict(project.mapping_files or {})
    loaded: list[str] = []
    missing: list[str] = []

    for key, filename in MAPPING_FILENAMES.items():
        src = source_dir / filename
        if src.exists():
            dest = project_mapping_dir / f"{key}.csv"
            shutil.copy2(str(src), str(dest))
            mapping_files[key] = str(dest)
            loaded.append(filename)
        elif key != "custom_mappings":  # custom_mappings is optional
            missing.append(filename)

    if not loaded:
        raise HTTPException(
            status_code=400,
            detail=f"No mapping CSVs found in {payload.directory}. Expected: variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv",
        )

    project.mapping_files = mapping_files
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}/detect-column-type")
def detect_column_type(project_id: str, column: str, filename: str | None = None, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Multi-file project: route to the specific file when filename is provided
    if filename and project.source_files:
        file_entry = next((f for f in project.source_files if f.get("filename") == filename), None)
        if file_entry and Path(file_entry["path"]).exists():
            if column not in file_entry.get("columns", []):
                raise HTTPException(status_code=400, detail=f"Column '{column}' not found in '{filename}'")
            transform = detect_pid_transform(
                file_entry["path"],
                file_entry.get("delimiter", ","),
                file_entry.get("encoding", "utf-8"),
                column,
            )
            return {"column": column, "transform": transform}

    # Fallback: legacy single-file
    if not project.source_path or not Path(project.source_path).exists():
        raise HTTPException(status_code=404, detail="Source file not uploaded")
    if column not in (project.source_columns or []):
        raise HTTPException(status_code=400, detail=f"Column '{column}' not found in source file")

    transform = detect_pid_transform(
        project.source_path,
        project.source_delimiter or ",",
        project.source_encoding or "utf-8",
        column,
    )
    return {"column": column, "transform": transform}


@router.get("/{project_id}/source-preview")
def source_preview(project_id: str, rows: int = 5, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.source_path or not Path(project.source_path).exists():
        raise HTTPException(status_code=404, detail="Source file not uploaded")

    import pandas as pd
    df = pd.read_csv(
        project.source_path,
        sep=project.source_delimiter if project.source_delimiter else ",",
        encoding=project.source_encoding if project.source_encoding else "utf-8",
        nrows=rows,
        dtype=str,
    )
    return {"columns": list(df.columns), "rows": df.fillna("").to_dict(orient="records")}
