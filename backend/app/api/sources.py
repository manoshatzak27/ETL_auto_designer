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
import pandas as pd
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


def _snapshot_column_values(entry: dict, columns: set[str]) -> dict[str, set[str]]:
    """Read the distinct values present in `columns` for a source file entry.
    Used to detect values that appear for the first time when a file is
    replaced by a newer version, so previously-made per-value concept
    mappings can be flagged as incomplete instead of silently leaving the
    new values unmapped in the generated ETL."""
    path = Path(entry.get("path", ""))
    if not columns or not path.exists():
        return {}
    try:
        df = pd.read_csv(
            path,
            sep=entry.get("delimiter", ","),
            encoding=entry.get("encoding", "utf-8"),
            dtype=str,
            on_bad_lines="skip",
            usecols=lambda c: c in columns,
        )
    except Exception:
        return {}
    return {col: set(df[col].dropna().unique().tolist()) for col in df.columns}


# Table configs (etl_config, keyed by OMOP table) are untyped JSON on the
# backend — each wizard step (Person, Provider, Care Site, Location, Visit)
# defines its own per-value concept maps (e.g. gender_concept_id.value_map,
# gender_concept_value_map, place_of_service_value_map, country_concept_id_map,
# visit_concept_value_map) with an inconsistent field-naming convention. Rather
# than hardcoding every table's shape, these two helpers find them generically
# by pairing a dict-of-numbers field with a same-named (ignoring boilerplate
# tokens like "source"/"col"/"map") sibling string field holding the source
# column it depends on.
_CONFIG_FIELD_FILLER_TOKENS = {"source", "value", "col", "column", "map", "concept", "id", "of"}


def _significant_tokens(key: str) -> tuple[str, ...]:
    return tuple(t for t in key.lower().split("_") if t and t not in _CONFIG_FIELD_FILLER_TOKENS)


def _find_config_value_maps(node: Any) -> list[tuple[str, dict]]:
    """Recursively scan a table-config subtree for (source_column, value_map)
    pairs, e.g. `{"source_col": "gender", "value_map": {"M": 8507, ...}}` or
    `{"gender_source_value_col": "gender", "gender_concept_value_map": {...}}`."""
    found: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        map_fields = {
            k: v for k, v in node.items()
            if isinstance(v, dict) and v and all(isinstance(vv, (int, float)) and not isinstance(vv, bool) for vv in v.values())
        }
        col_fields = {
            k: v for k, v in node.items()
            if isinstance(v, str) and v and ("col" in k.lower() or "column" in k.lower())
        }
        for map_key, value_map in map_fields.items():
            map_tokens = _significant_tokens(map_key)
            for col_key, col_val in col_fields.items():
                if _significant_tokens(col_key) == map_tokens:
                    found.append((col_val, value_map))
                    break
        for v in node.values():
            found.extend(_find_config_value_maps(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_config_value_maps(item))
    return found


def _iter_file_scoped_configs(etl_config: dict, filename: str):
    """Yield table-config subtrees scoped to one source file. Person/Provider/
    Care Site/Location key `file_configs` by filename directly; Visit uses a
    list of per-file entries carrying a `source_filename` field."""
    for table_cfg in (etl_config or {}).values():
        if not isinstance(table_cfg, dict):
            continue
        file_configs = table_cfg.get("file_configs")
        if isinstance(file_configs, dict):
            entry = file_configs.get(filename)
            if isinstance(entry, dict):
                yield entry
        elif isinstance(file_configs, list):
            for entry in file_configs:
                if isinstance(entry, dict) and entry.get("source_filename") == filename:
                    yield entry


def _value_mapped_columns_for_file(etl_config: dict, filename: str) -> set[str]:
    """Source columns that already have a per-value concept map configured
    (non-empty) for a specific file, across all OMOP table configs."""
    cols: set[str] = set()
    for scoped_cfg in _iter_file_scoped_configs(etl_config, filename):
        for source_col, value_map in _find_config_value_maps(scoped_cfg):
            if source_col and value_map:
                cols.add(source_col)
    return cols


def _find_new_value_conflicts(
    old_value_snapshots: dict[str, dict[str, set[str]]],
    new_entries: dict[str, dict],
) -> list[dict]:
    """Diff pre-replacement value snapshots (captured before the new file
    overwrote the old one on disk, see `_snapshot_column_values`) against the
    new file's values for the same columns. Values present only in the new
    file haven't been mapped yet — flag them the same way removed columns
    are flagged, so the user notices instead of the new values silently
    falling through unmapped in the generated ETL."""
    conflicts: list[dict] = []
    for filename, old_values in old_value_snapshots.items():
        new_entry = new_entries.get(filename)
        if not new_entry or not old_values:
            continue
        new_values = _snapshot_column_values(new_entry, set(old_values.keys()))
        for col, old_vals in old_values.items():
            added = sorted(new_values.get(col, set()) - old_vals)
            if not added:
                continue
            preview = ", ".join(repr(v) for v in added[:5])
            suffix = f" and {len(added) - 5} more" if len(added) > 5 else ""
            conflicts.append({
                "column": col,
                "reason": f"the updated file has {len(added)} new value(s) not yet mapped: {preview}{suffix}",
            })
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

    # Columns with an existing per-value mapping — either a Concepts-step
    # decision (global, keyed by column name) or a per-value concept map in
    # one of the OMOP-table configs (Person gender/race/ethnicity, Provider
    # gender/specialty, Care Site place-of-service, Location country, Visit
    # visit-type — file-scoped). When a file supplying one of these columns
    # gets replaced, we snapshot its distinct values *before* the new bytes
    # are written to disk (the old content is gone once overwritten) so we
    # can later detect values that only exist in the new file — see
    # `_find_new_value_conflicts`.
    concept_decision_columns = {
        col
        for col, decision in (project.concept_decisions or {}).items()
        if isinstance(decision, dict)
        and decision.get("strategy") in ("map_values", "map_both")
        and decision.get("value_concepts")
    }
    existing_before: dict[str, dict] = {f["filename"]: f for f in (project.source_files or [])}
    old_value_snapshots: dict[str, dict[str, set[str]]] = {}

    def _snapshot_if_value_mapped(filename: str) -> None:
        if filename in old_value_snapshots:
            return
        old_entry = existing_before.get(filename)
        if not old_entry:
            return
        cols = concept_decision_columns | _value_mapped_columns_for_file(project.etl_config or {}, filename)
        cols &= set(old_entry.get("columns") or [])
        if cols:
            old_value_snapshots[filename] = _snapshot_column_values(old_entry, cols)

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
                        _snapshot_if_value_mapped(member_path.name)
                        dest = project_upload_dir / member_path.name
                        dest.write_bytes(zf.read(member))
                        raw_paths.append(dest)
        elif Path(safe_name).suffix.lower() in _SOURCE_EXTENSIONS:
            _snapshot_if_value_mapped(safe_name)
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
    conflicts += _find_new_value_conflicts(old_value_snapshots, existing)

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
