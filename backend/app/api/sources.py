import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.concurrency import run_in_threadpool
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

MAPPING_FILENAMES = {
    "variable_mapping": "variable_mapping.csv",
    "value_mapping": "value_mapping.csv",
    "variable_value_mapping": "variable_value_mapping.csv",
    "custom_mappings": "custom_mappings.csv",
}


_SOURCE_EXTENSIONS = {".csv", ".tsv", ".txt"}
# Buffer size when copying an uploaded file to disk; bounds upload memory use.
_UPLOAD_COPY_CHUNK = 1024 * 1024


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
    if (decision.get("modifier_mapping") or {}).get("modifier_concepts"):
        return True
    if (decision.get("condition_status_mapping") or {}).get("condition_status_concepts"):
        return True
    if (decision.get("qualifier_mapping") or {}).get("qualifier_concepts"):
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
        modifier_col = (decision.get("modifier_mapping") or {}).get("modifier_col")
        if modifier_col and modifier_col in removed_columns:
            reasons.append(f"its modifier column '{modifier_col}' no longer exists in the updated file")
        condition_status_col = (decision.get("condition_status_mapping") or {}).get("condition_status_col")
        if condition_status_col and condition_status_col in removed_columns:
            reasons.append(f"its condition status column '{condition_status_col}' no longer exists in the updated file")
        qualifier_col = (decision.get("qualifier_mapping") or {}).get("qualifier_col")
        if qualifier_col and qualifier_col in removed_columns:
            reasons.append(f"its qualifier column '{qualifier_col}' no longer exists in the updated file")
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


def _discard_file_mappings(project: Project, filename: str, old_columns: set[str]) -> None:
    """Forget every mapping choice tied to one source file — used when the user
    re-uploads an updated version of a file and chooses to discard prior
    mapping choices instead of keeping them for review (see `_find_decision_conflicts`
    / `_find_new_value_conflicts`, which is the "keep" path)."""
    if project.concept_decisions:
        decisions = dict(project.concept_decisions)
        for col in old_columns:
            decisions.pop(col, None)
        project.concept_decisions = decisions

    if project.etl_config:
        etl_config = dict(project.etl_config)
        for table_key, table_cfg in list(etl_config.items()):
            if not isinstance(table_cfg, dict):
                continue
            file_configs = table_cfg.get("file_configs")
            if isinstance(file_configs, dict) and filename in file_configs:
                file_configs = dict(file_configs)
                file_configs.pop(filename, None)
                etl_config[table_key] = {**table_cfg, "file_configs": file_configs}
            elif isinstance(file_configs, list):
                filtered = [
                    e for e in file_configs
                    if not (isinstance(e, dict) and e.get("source_filename") == filename)
                ]
                if len(filtered) != len(file_configs):
                    etl_config[table_key] = {**table_cfg, "file_configs": filtered}
        project.etl_config = etl_config


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
    keep_mappings: bool = Form(True),
    db: Session = Depends(get_db),
):
    """Accept one or more CSV/TSV files, OR a single ZIP containing CSV/TSV files.

    `keep_mappings` applies when a file being uploaded replaces one already on
    the project (same filename): True (default) preserves existing mapping
    choices and flags conflicts for review; False discards every mapping
    choice tied to the replaced file(s) instead. The frontend asks the user
    which they want before calling this endpoint when it detects a filename
    collision."""
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
        if not keep_mappings or filename in old_value_snapshots:
            return
        old_entry = existing_before.get(filename)
        if not old_entry:
            return
        cols = concept_decision_columns | _value_mapped_columns_for_file(project.etl_config or {}, filename)
        cols &= set(old_entry.get("columns") or [])
        if cols:
            old_value_snapshots[filename] = _snapshot_column_values(old_entry, cols)

    # Collect raw paths to process (extracts from ZIP when needed). Uploads are
    # already spooled to a temp file by Starlette, so copy from that file in
    # chunks rather than reading it into memory — a multi-GB CSV would
    # otherwise need that much RAM. Runs in a worker thread so a long copy
    # doesn't block the event loop.
    def _store_upload(upload: UploadFile) -> list[Path]:
        safe_name = Path(upload.filename).name if upload.filename else "source.csv"
        suffix = Path(safe_name).suffix.lower()
        stored: list[Path] = []
        upload.file.seek(0)

        if suffix == ".zip":
            # zipfile reads the seekable spooled file directly, and each member
            # is decompressed as a stream.
            with zipfile.ZipFile(upload.file) as zf:
                for info in zf.infolist():
                    member_path = Path(info.filename)
                    if info.is_dir() or member_path.suffix.lower() not in _SOURCE_EXTENSIONS or member_path.name.startswith("__"):
                        continue
                    _snapshot_if_value_mapped(member_path.name)
                    dest = project_upload_dir / member_path.name
                    with zf.open(info) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out, _UPLOAD_COPY_CHUNK)
                    stored.append(dest)
        elif suffix in _SOURCE_EXTENSIONS:
            _snapshot_if_value_mapped(safe_name)
            dest = project_upload_dir / safe_name
            with dest.open("wb") as out:
                shutil.copyfileobj(upload.file, out, _UPLOAD_COPY_CHUNK)
            stored.append(dest)
        return stored

    raw_paths: list[Path] = []
    for upload in files:
        raw_paths.extend(await run_in_threadpool(_store_upload, upload))

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

    if keep_mappings:
        removed_columns: set[str] = set()
        for filename, old_entry in replaced_old_entries.items():
            old_cols = set(old_entry.get("columns") or [])
            new_cols = set(existing[filename].get("columns") or [])
            removed_columns |= (old_cols - new_cols)
        conflicts = _find_decision_conflicts(project.concept_decisions or {}, removed_columns)
        conflicts += _find_new_value_conflicts(old_value_snapshots, existing)
    else:
        for filename, old_entry in replaced_old_entries.items():
            _discard_file_mappings(project, filename, set(old_entry.get("columns") or []))
        conflicts = []

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


SOURCE_CONTENT_MAX_PAGE = 5000
# Rows held in memory at once while a PATCH streams through the original file.
SOURCE_PATCH_CHUNK_ROWS = 50_000


class SourceFileContentResponse(BaseModel):
    filename: str
    delimiter: str
    encoding: str
    columns: List[str]
    rows: List[dict]
    offset: int
    row_count: int


@router.get("/{project_id}/source-file-content", response_model=SourceFileContentResponse)
def get_source_file_content(
    project_id: str,
    filename: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=SOURCE_CONTENT_MAX_PAGE),
    db: Session = Depends(get_db),
):
    """One page of data rows (`offset`..`offset + limit - 1`, 0-based, header
    excluded). Only that slice is parsed, so the grid editor can page through
    files of any size."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = _find_source_file_entry(project, filename)

    try:
        df = pd.read_csv(
            entry["path"],
            sep=entry.get("delimiter", ","),
            encoding=entry.get("encoding", "utf-8"),
            dtype=str,
            keep_default_na=False,
            on_bad_lines="skip",
            # A callable rather than range(1, offset + 1): pandas turns a
            # list-like skiprows into a set, which for deep pages of a large
            # file would itself cost hundreds of MB.
            skiprows=(lambda i: 0 < i <= offset) if offset else None,
            nrows=limit,
        )
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=entry.get("columns") or [])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {exc}")

    return {
        "filename": filename,
        "delimiter": entry.get("delimiter", ","),
        "encoding": entry.get("encoding", "utf-8"),
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
        "offset": offset,
        "row_count": entry.get("row_count", len(df)),
    }


class SourceColumnSpec(BaseModel):
    # Column in the current file this one is read from; None for a new column.
    source: str | None = None
    name: str


class SourceFileEditsPayload(BaseModel):
    # The output columns in order. Existing columns left out are deleted;
    # `source != name` is a rename.
    columns: List[SourceColumnSpec]
    # Data-row index (0-based, as served by GET) -> {output column name: value}.
    cell_edits: dict[int, dict[str, str]] = {}
    deleted_rows: List[int] = []
    # Appended after the last existing row, keyed by output column name.
    added_rows: List[dict[str, str]] = []


@router.patch("/{project_id}/source-file-content", response_model=ProjectResponse)
def patch_source_file_content(
    project_id: str,
    filename: str,
    payload: SourceFileEditsPayload,
    db: Session = Depends(get_db),
):
    """Apply a set of edits made in the paged grid editor. The original file is
    streamed in chunks into a new file with the edits applied, so memory stays
    bounded however large the file is; the client only ever sends what changed."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = _find_source_file_entry(project, filename)

    names = [c.name.strip() for c in payload.columns]
    if not names or any(not n for n in names):
        raise HTTPException(status_code=400, detail="Column names must be non-empty")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise HTTPException(status_code=400, detail=f"Duplicate column name: {', '.join(dupes)}")
    name_set = set(names)
    for row_values in [*payload.cell_edits.values(), *payload.added_rows]:
        unknown = set(row_values) - name_set
        if unknown:
            raise HTTPException(status_code=400, detail=f"Edits reference unknown column(s): {', '.join(sorted(unknown))}")
    if any(r < 0 for r in [*payload.cell_edits, *payload.deleted_rows]):
        raise HTTPException(status_code=400, detail="Row indexes must be non-negative")

    path = Path(entry["path"])
    sep = entry.get("delimiter", ",")
    encoding = entry.get("encoding", "utf-8")
    read_kwargs = dict(sep=sep, encoding=encoding, dtype=str, keep_default_na=False)

    try:
        header = list(pd.read_csv(path, nrows=0, **read_kwargs).columns)
    except pd.errors.EmptyDataError:
        header = []
    missing_sources = [c.source for c in payload.columns if c.source is not None and c.source not in header]
    if missing_sources:
        raise HTTPException(
            status_code=409,
            detail=f"Column(s) no longer in the file: {', '.join(missing_sources)}. Reload the editor and try again.",
        )

    sources = [c.source for c in payload.columns]
    deleted = set(payload.deleted_rows)
    edit_rows = sorted(payload.cell_edits)
    edit_pos = 0
    rows_seen = 0
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    def project_columns(chunk: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {name: (chunk[src] if src is not None else "") for name, src in zip(names, sources)},
            index=chunk.index,
        )

    try:
        with open(tmp_path, "w", encoding=encoding, newline="") as out:
            pd.DataFrame(columns=names).to_csv(out, sep=sep, index=False)
            if header:
                # on_bad_lines="error": silently dropping a malformed line would
                # shift every later row by one, so edits made against the row
                # indexes the client saw would land on the wrong rows.
                reader = pd.read_csv(path, chunksize=SOURCE_PATCH_CHUNK_ROWS, on_bad_lines="error", **read_kwargs)
                for chunk in reader:
                    chunk.index = pd.RangeIndex(rows_seen, rows_seen + len(chunk))
                    rows_seen += len(chunk)
                    result = project_columns(chunk)
                    while edit_pos < len(edit_rows) and edit_rows[edit_pos] < rows_seen:
                        row = edit_rows[edit_pos]
                        for name, value in payload.cell_edits[row].items():
                            result.at[row, name] = value
                        edit_pos += 1
                    if deleted:
                        result = result[~result.index.isin(deleted)]
                    result.to_csv(out, sep=sep, index=False, header=False)
            if payload.added_rows:
                added = pd.DataFrame(payload.added_rows, columns=names).fillna("")
                added.to_csv(out, sep=sep, index=False, header=False)

        out_of_range = [r for r in [*edit_rows, *deleted] if r >= rows_seen]
        if out_of_range:
            raise HTTPException(
                status_code=409,
                detail=f"Row(s) {', '.join(map(str, sorted(out_of_range)[:5]))} no longer exist in the file. Reload the editor and try again.",
            )
        schema = infer_schema(str(tmp_path))
        os.replace(tmp_path, path)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except pd.errors.ParserError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=f"The file has a malformed row, so edits can't be matched to rows safely: {exc}",
        )
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

    if Path(file.filename or "").suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Mapping file must be a .csv file.")

    project_mapping_dir = settings.get_upload_path() / project_id / "mappings"
    project_mapping_dir.mkdir(parents=True, exist_ok=True)
    dest = project_mapping_dir / f"{mapping_type}.csv"
    tmp = dest.with_name(dest.name + ".tmp")

    # Copy to a temp file and check it parses as CSV before replacing the
    # existing mapping, so a bad upload doesn't clobber a good one.
    def _store() -> str | None:
        file.file.seek(0)
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out, _UPLOAD_COPY_CHUNK)
        try:
            pd.read_csv(tmp, nrows=100, dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            return "Mapping file must be UTF-8 encoded text."
        except pd.errors.EmptyDataError:
            return "Mapping file is empty."
        except pd.errors.ParserError as exc:
            return f"Mapping file is not a valid CSV: {exc}"
        os.replace(tmp, dest)
        return None

    try:
        error = await run_in_threadpool(_store)
    finally:
        tmp.unlink(missing_ok=True)
    if error:
        raise HTTPException(status_code=400, detail=error)

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
