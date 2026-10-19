"""
Column auto-matching for the table steps.

  GET  /projects/column-matcher/health          → is the LLM fallback usable
  POST /projects/{id}/suggest-column-mapping    → source column per step field
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.concept_mapping import ConceptPreference, suggest_concepts_for_values
from app.database import get_db
from app.models.project import Project
from app.services.column_matcher import (
    FIELD_SPECS,
    ConceptTarget,
    FieldRequest,
    SourceColumns,
    ValueConcepts,
    llm_health,
    match_columns,
    profile_file,
)

router = APIRouter(prefix="/projects", tags=["column-matching"])

# (project, target, value) → the concept matcher's status for that value. The
# value scan sends every short-list column of a file — a minute or so per
# domain the first time — so a value is asked about once per process.
_VALUE_STATUS_LIMIT = 200_000
_value_status: dict[tuple[str, ConceptTarget, str], str] = {}


def _value_concepts(project_id: str) -> ValueConcepts:
    async def statuses(target: ConceptTarget, values: list[str]) -> dict[str, str]:
        todo = [v for v in values if (project_id, target, v) not in _value_status]
        if todo:
            prefer = ConceptPreference(
                vocabularies=list(target.vocabularies) or None,
                concept_classes=list(target.concept_classes) or None,
            ) if target.vocabularies or target.concept_classes else None
            results = await suggest_concepts_for_values(project_id, target.domain, todo, prefer)
            if len(_value_status) + len(results) > _VALUE_STATUS_LIMIT:
                _value_status.clear()
            for v, entry in results.items():
                # No concept, or concept 0 ("Unknown" → not mapped), is no
                # evidence that the column holds the field's concepts.
                status = entry["status"] if entry.get("concept_id") else "unmapped"
                _value_status[(project_id, target, v)] = status
        return {v: _value_status.get((project_id, target, v), "unmapped") for v in values}

    return statuses


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
        _value_concepts(project_id),
    )
