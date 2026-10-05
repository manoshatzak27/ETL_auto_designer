"""
Column auto-matching for the table steps.

  GET  /projects/column-matcher/health          → is the LLM fallback usable
  POST /projects/{id}/suggest-column-mapping    → source column per step field
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project
from app.services.column_matcher import (
    FIELD_SPECS,
    FieldRequest,
    SourceColumns,
    llm_health,
    match_columns,
    profile_file,
)

router = APIRouter(prefix="/projects", tags=["column-matching"])


@router.get("/column-matcher/health")
async def column_matcher_health(refresh: bool = False):
    return {"llm": await llm_health(refresh)}


class FieldEntry(BaseModel):
    # Unique within the request, e.g. "city_col" or "1:date_col" for a step
    # with several visit definitions.
    key: str
    # The FIELD_SPECS entry to match against; defaults to `key`.
    spec: str | None = None
    # Text on screen that tells this field apart from its siblings (a visit's label).
    hint: str = ""
    # Files to look in for this field; defaults to the request's `filenames`.
    filenames: list[str] | None = None
    # For a date field: the step's current date format, a name shared by the
    # fields that use that same format, and whether a column already mapped
    # in the step relies on it.
    date_format: str | None = None
    format_group: str | None = None
    format_locked: bool = False


class SuggestColumnMappingRequest(BaseModel):
    table: str
    filenames: list[str] = Field(default_factory=list)
    fields: list[FieldEntry]
    exclude_columns: list[str] = Field(default_factory=list)
    use_llm: bool = True


@router.post("/{project_id}/suggest-column-mapping")
async def suggest_column_mapping(
    project_id: str,
    body: SuggestColumnMappingRequest,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    specs = FIELD_SPECS.get(body.table)
    if specs is None:
        raise HTTPException(status_code=400, detail=f"No column matching for table {body.table!r}")

    # Every file the request mentions, resolved to a path on disk. A project from
    # before multi-file uploads has only the legacy single source, under "".
    entries = {f.get("filename"): f for f in (project.source_files or [])}
    wanted = set(body.filenames) | {fn for f in body.fields for fn in (f.filenames or [])}
    if not wanted:
        wanted = {next(iter(entries), "")}
    sources: dict[str, SourceColumns] = {}
    for fn in wanted:
        entry = entries.get(fn)
        if entry:
            path, delimiter, encoding = entry["path"], entry.get("delimiter", ","), entry.get("encoding", "utf-8")
        elif fn == "" and project.source_path:
            path, delimiter, encoding = project.source_path, project.source_delimiter or ",", project.source_encoding or "utf-8"
        else:
            raise HTTPException(status_code=404, detail=f"Source file {fn!r} not found in this project")
        if not Path(path).exists():
            raise HTTPException(status_code=404, detail=f"Source file {fn or path!r} is missing on disk")
        try:
            sources[fn] = SourceColumns(fn, profile_file(path, delimiter, encoding))
        except Exception as exc:  # noqa: BLE001 — unreadable file
            raise HTTPException(status_code=422, detail=f"Could not read {fn or path!r}: {exc}") from exc

    default_files = list(body.filenames) or list(wanted)
    fields: list[FieldRequest] = []
    for f in body.fields:
        spec_key = f.spec or f.key
        spec = specs.get(spec_key)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"Unknown field {spec_key!r} for table {body.table!r}")
        fields.append(FieldRequest(
            f.key, spec, spec_key, f.filenames or default_files, f.hint,
            f.date_format, f.format_group, f.format_locked,
        ))

    return await match_columns(
        body.table,
        fields,
        sources,
        set(body.exclude_columns),
        dict(project.column_descriptions or {}),
        body.use_llm,
    )
