"""
API routes for the concept mapping step (Concepts step).

Endpoints:
  GET  /projects/concept-lookup              → look up domain for a concept_id in CONCEPT.csv
  GET  /projects/concept-matcher/health      → readiness of the bulk matching pipeline
  POST /projects/{id}/match-concepts         → map a list of source columns to concepts in bulk
  GET  /projects/{id}/column-descriptions    → the project's data dictionary
  PUT  /projects/{id}/column-descriptions    → replace it (also used for single-column edits)
  POST /projects/{id}/column-descriptions/upload   → merge in an uploaded CSV/Excel dictionary
  GET  /projects/{id}/column-descriptions/template → a pre-filled CSV to fill in and upload back
  GET  /projects/{id}/column-values          → unique values per source column
  GET  /projects/{id}/concept-decisions       → load saved decisions
  POST /projects/{id}/concept-decisions       → save decisions (full replace)
  POST /projects/{id}/generate-mapping-csvs  → generate the 3 CSVs from decisions
  GET  /projects/{id}/download-mapping-files → download the generated mapping CSVs as a zip
  GET  /projects/{id}/download-mapping-summary → download a human-readable Excel summary of all decisions
"""
import csv
import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import pandas as pd

from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse
from app.services.column_descriptions import parse_descriptions
from app.services.mapping_generator import generate_mapping_csvs, generate_mapping_summary_excel
from app.config import settings

router = APIRouter(prefix="/projects", tags=["concept-mapping"])

# ── Concept domain lookup (CONCEPT.csv cache) ───────────────────────────────

# Per-process LRU cache so the same concept_id only hits Postgres once.
# Bound the size — the lookup is called as the user types concept IDs, so
# duplicates within a session are common.
from functools import lru_cache


@lru_cache(maxsize=20000)
def _get_concept_info(concept_id: int) -> "tuple[str, str, str | None] | None":
    """Look up (domain_id, concept_name, standard_concept) for an OMOP concept by
    querying the loaded vocabulary in Postgres. Returns None when the concept
    genuinely isn't there. Raises on connection/query errors instead of
    swallowing them — lru_cache only memoizes normal returns, not exceptions,
    so a transient failure (e.g. Postgres not reachable yet at startup) never
    gets cached as a permanent "not found" for that concept_id."""
    if concept_id is None or concept_id <= 0:
        return None
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    schema = settings.omop_vocab_schema or "vocab"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT domain_id, concept_name, standard_concept FROM {schema}.concept WHERE concept_id = %s"
                ).format(schema=pgsql.Identifier(schema)),
                (int(concept_id),),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return (str(row[0]), str(row[1]) if row[1] is not None else "", str(row[2]) if row[2] is not None else None)
            return None


@router.get("/concept-lookup/domain")
def concept_lookup(concept_id: int):
    """Return the domain_id, concept_name and standard_concept flag for a given
    concept_id by querying the loaded OMOP vocabulary in Postgres (vocab.concept).
    standard_concept is 'S' for standard concepts, 'C' for classification
    concepts, and null/None for non-standard concepts — per OMOP convention.
    """
    try:
        info = _get_concept_info(concept_id)
    except Exception as exc:
        # Vocab schema/table missing (Load vocabulary hasn't run yet) or
        # Postgres unreachable. Not cached — the next lookup will retry.
        print(f"[concept-lookup] vocab.concept query failed: {exc}")
        return {"concept_id": concept_id, "domain_id": None, "concept_name": None, "standard_concept": None, "found": False}
    if info:
        domain, concept_name, standard_concept = info
        return {"concept_id": concept_id, "domain_id": domain, "concept_name": concept_name, "standard_concept": standard_concept, "found": True}
    return {"concept_id": concept_id, "domain_id": None, "concept_name": None, "standard_concept": None, "found": False}


# ── Column descriptions (the project's data dictionary) ─────────────────────


def _project_columns(project: Project) -> list[tuple[str, str]]:
    """Every source column as (column, file), in file order, first spelling wins.

    Column names are treated as globally unique throughout the wizard —
    concept_decisions is keyed by them — so a name appearing in two files is one
    column here too, attributed to the first file that declares it.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in (project.source_files or []):
        filename = entry.get("filename") or ""
        for col in (entry.get("columns") or []):
            if col not in seen:
                seen.add(col)
                out.append((col, filename))
    for col in (project.source_columns or []):
        if col not in seen:
            seen.add(col)
            out.append((col, project.source_filename or ""))
    return out


class ColumnDescriptionsPayload(BaseModel):
    descriptions: dict[str, str]


@router.get("/{project_id}/column-descriptions")
def get_column_descriptions(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"descriptions": project.column_descriptions or {}}


@router.put("/{project_id}/column-descriptions")
def put_column_descriptions(
    project_id: str,
    payload: ColumnDescriptionsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Replace the whole dictionary. Also how a single description is edited or
    cleared — the map is small enough that a full replace is simpler than a
    patch, and it makes "clear all" the same code path as an empty upload."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    cleaned = {
        str(col): str(text).strip()
        for col, text in (payload.descriptions or {}).items()
        if str(text or "").strip()
    }
    project.column_descriptions = cleaned
    db.commit()
    return {"descriptions": cleaned}


@router.post("/{project_id}/column-descriptions/upload")
async def upload_column_descriptions(
    project_id: str,
    file: UploadFile = File(...),
    replace: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Merge an uploaded data dictionary (CSV/TSV or Excel) into the project.

    The file needs a header row naming a column and a description — several
    spellings of each are accepted, see `services/column_descriptions.py`. Rows
    whose column isn't in the project come back in `unmatched` rather than being
    dropped quietly, since a near-miss in a column name is the usual cause and
    the user is the only one who can resolve it.

    `replace=true` discards the existing dictionary first; the default merges,
    with the uploaded file winning on conflicts.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    known = [col for col, _ in _project_columns(project)]
    if not known:
        raise HTTPException(
            status_code=400,
            detail="This project has no source columns yet — upload a source file first.",
        )

    suffix = Path(file.filename or "upload.csv").suffix.lower() or ".csv"
    tmp_path: Path | None = None
    try:
        # Spooled to disk because delimiter/encoding sniffing and pandas both
        # want a real path, and a data dictionary is small.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)
        parsed = parse_descriptions(tmp_path, known)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't read that file: {exc}")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    existing: dict[str, str] = {} if replace else dict(project.column_descriptions or {})
    existing.update(parsed["descriptions"])
    project.column_descriptions = existing
    db.commit()

    described = set(existing)
    return {
        "descriptions": existing,
        "matched": parsed["matched"],
        "unmatched": parsed["unmatched"],
        "headers": parsed["headers"],
        # Columns still without a description, so the UI can say what's left.
        "missing": [col for col in known if col not in described],
    }


@router.get("/{project_id}/column-descriptions/template")
def column_descriptions_template(project_id: str, db: Session = Depends(get_db)):
    """A CSV of every source column, ready to fill in and upload back.

    Pre-filled with any description the project already has, so the template
    doubles as an export of the current dictionary.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    descriptions = project.column_descriptions or {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "table", "description"])
    for col, filename in _project_columns(project):
        writer.writerow([col, filename, descriptions.get(col, "")])

    # utf-8-sig: Excel opens a plain utf-8 CSV as mojibake, and this file exists
    # to be opened in Excel.
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project_id}_column_descriptions.csv"'},
    )


# ── Bulk concept matching (the "new concept finding" pipeline) ──────────────

# The matcher is the staged pipeline from the sibling project, behind HTTP
# (pipeline/service.py there, the `conceptmatcher` compose service here). It is
# a different thing from EntityLinker: EntityLinker answers "what concepts look
# like this phrase?" one query at a time for the AI Search box, while this maps
# a whole column list in one pass and returns a decision per column
# (auto_accept / review / unmapped) rather than a ranked list.


class MatchColumn(BaseModel):
    name: str
    description: str | None = None
    table: str | None = None


class MatchConceptsPayload(BaseModel):
    columns: list[MatchColumn]
    # Scopes any manual corrections recorded on the pipeline side; defaults to
    # the project id so one project's overrides never leak into another's.
    source_system: str | None = None
    candidates: int = 5


@router.get("/concept-matcher/health")
async def concept_matcher_health():
    """Whether the bulk matcher is reachable and its vocabulary is loaded.

    Never raises: the Concepts step calls this on mount purely to decide whether
    to enable its "Load concepts" button, so an unreachable matcher is a normal
    answer ("available": false), not an error.
    """
    if not settings.concept_matcher_url:
        return {"available": False, "detail": "CONCEPT_MATCHER_URL is not configured"}

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.concept_matcher_url.rstrip('/')}/health")
            resp.raise_for_status()
            info = resp.json()
    except Exception as exc:
        return {"available": False, "detail": f"Concept matcher unavailable: {exc}"}

    return {
        "available": bool(info.get("ready")),
        "detail": "; ".join(info.get("problems") or []) or None,
        **{k: info.get(k) for k in (
            "vocabulary_version", "concepts", "embeddings", "embedding_model",
            "auto_accept_threshold", "review_threshold",
        )},
    }


@router.post("/{project_id}/match-concepts")
async def match_concepts(
    project_id: str,
    payload: MatchConceptsPayload,
    db: Session = Depends(get_db),
):
    """Map a list of source columns onto standard OMOP concepts in one pass.

    Any column that arrives without a description is given the project's, from
    the uploaded data dictionary. Filling it in here rather than at the caller
    makes the dictionary authoritative: every path into the matcher gets the
    same input, and the frontend cannot forget to send it.

    Results come back in request order and each one echoes `column_name` and
    `source_table`, so the caller can tie a result to its row even when the same
    column name appears in more than one source file.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not settings.concept_matcher_url:
        raise HTTPException(status_code=503, detail="CONCEPT_MATCHER_URL is not configured")
    if not payload.columns:
        return {"run_id": 0, "results": []}

    import httpx

    described = project.column_descriptions or {}
    columns = []
    for column in payload.columns:
        entry = column.model_dump()
        if not (entry.get("description") or "").strip():
            entry["description"] = described.get(entry["name"]) or None
        columns.append(entry)

    body = {
        "columns": columns,
        "source_system": payload.source_system or project_id,
        "note": f"ETL Auto-Designer project {project_id}",
        "candidates": payload.candidates,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.concept_matcher_timeout) as client:
            resp = await client.post(
                f"{settings.concept_matcher_url.rstrip('/')}/match", json=body,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        # The matcher answers 400 for a malformed column list and 503 when the
        # vocabulary database is down — pass its own reason through rather than
        # flattening both into a generic failure.
        detail = exc.response.text
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Concept matcher unavailable: {exc}")


# ── Column unique values ────────────────────────────────────────────────────

@router.get("/{project_id}/column-values")
def get_column_values(
    project_id: str,
    max_values: int = 1000,
    filename: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Return per-column stats and distinct values for the source dataset.
    Response shape:
      { col: { distinct_values, distinct_count, null_count, total_rows, completion_rate } }
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Multi-file: read from the specified file when provided
    if filename and project.source_files:
        file_entry = next((f for f in project.source_files if f.get("filename") == filename), None)
        if file_entry and Path(file_entry["path"]).exists():
            df = pd.read_csv(
                file_entry["path"],
                sep=file_entry.get("delimiter", ","),
                encoding=file_entry.get("encoding", "utf-8"),
                dtype=str,
                on_bad_lines="skip",
            )
        else:
            return {}
    else:
        if not project.source_path or not Path(project.source_path).exists():
            return {}
        df = pd.read_csv(
            project.source_path,
            sep=project.source_delimiter or ",",
            encoding=project.source_encoding or "utf-8",
            dtype=str,
            on_bad_lines="skip",
        )

    total_rows = len(df)
    result: dict[str, dict] = {}

    for col in df.columns:
        null_count = int(df[col].isna().sum())
        all_vals = df[col].dropna().unique().tolist()
        distinct_count = len(all_vals)
        completion_rate = round(((total_rows - null_count) / total_rows * 100), 1) if total_rows else 0.0

        result[col] = {
            "distinct_values": [str(v) for v in all_vals[:max_values]],
            "distinct_count": distinct_count,
            "null_count": null_count,
            "total_rows": total_rows,
            "completion_rate": completion_rate,
        }

    return result


# ── Concept decisions ───────────────────────────────────────────────────────

class ConceptDecisionsPayload(BaseModel):
    decisions: dict[str, Any]


@router.get("/{project_id}/concept-decisions")
def get_concept_decisions(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.concept_decisions or {}


@router.post("/{project_id}/concept-decisions", response_model=ProjectResponse)
def save_concept_decisions(
    project_id: str,
    payload: ConceptDecisionsPayload,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.concept_decisions = payload.decisions
    db.commit()
    db.refresh(project)
    return project


# ── Generate mapping CSVs ───────────────────────────────────────────────────

@router.post("/{project_id}/generate-mapping-csvs", response_model=ProjectResponse)
def generate_csvs(project_id: str, db: Session = Depends(get_db)):
    """
    Generate variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv
    (and custom_mappings.csv) from the saved concept decisions.
    Stores file paths in project.mapping_files.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.concept_decisions:
        raise HTTPException(status_code=400, detail="No concept decisions saved yet")

    output_dir = str(settings.get_upload_path() / project_id / "mappings")
    files = generate_mapping_csvs(
        project.concept_decisions,
        output_dir,
        custom_vocabulary_id=project.custom_vocabulary_id or "CUSTOM",
    )

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No mapping rows generated. Make sure at least one variable is mapped.",
        )

    project.mapping_files = files
    db.commit()
    db.refresh(project)
    return project


# ── Download mapping CSVs as a zip ──────────────────────────────────────────

@router.get("/{project_id}/download-mapping-files")
def download_mapping_files(project_id: str, db: Session = Depends(get_db)):
    """Bundle the CSVs listed in project.mapping_files into a zip for download."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    files: dict = project.mapping_files or {}
    existing = [Path(p) for p in files.values() if p and Path(p).is_file()]
    if not existing:
        raise HTTPException(
            status_code=404,
            detail="No mapping files generated yet. Generate them first.",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in existing:
            zf.write(path, arcname=path.name)
    buf.seek(0)

    zip_name = f"{project_id}_mapping_files.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ── Download mapping summary as Excel ───────────────────────────────────────

def _all_distinct_values(project: Project, max_values: int = 1000) -> dict[str, list[str]]:
    """Distinct source values for every column across every uploaded source file
    (unlike get_column_values above, which only reads the one currently-selected
    file). Lets the mapping summary list every value a column actually has, not
    just the ones the user happened to assign a concept to."""
    file_entries = project.source_files or (
        [{"path": project.source_path, "delimiter": project.source_delimiter, "encoding": project.source_encoding}]
        if project.source_path else []
    )
    result: dict[str, list[str]] = {}
    for entry in file_entries:
        path = entry.get("path")
        if not path or not Path(path).exists():
            continue
        df = pd.read_csv(
            path,
            sep=entry.get("delimiter") or ",",
            encoding=entry.get("encoding") or "utf-8",
            dtype=str,
            on_bad_lines="skip",
        )
        for col in df.columns:
            if col in result:
                continue
            result[col] = [str(v) for v in df[col].dropna().unique().tolist()[:max_values]]
    return result


def _ordered_decisions(project: Project) -> dict:
    """Re-key project.concept_decisions to follow the column order of the
    uploaded source file(s), instead of dict/insertion order — which is
    whatever order the user happened to touch columns in, not a meaningful
    order to read a report in. Any decision whose column isn't in a known
    file (e.g. its file was later removed) is appended at the end, in its
    original order, so nothing gets silently dropped."""
    decisions = project.concept_decisions or {}
    order: list[str] = []
    seen: set[str] = set()
    for entry in (project.source_files or []):
        for col in (entry.get("columns") or []):
            if col not in seen:
                order.append(col)
                seen.add(col)
    for col in (project.source_columns or []):
        if col not in seen:
            order.append(col)
            seen.add(col)

    ordered = {col: decisions[col] for col in order if col in decisions}
    for col, d in decisions.items():
        if col not in ordered:
            ordered[col] = d
    return ordered


@router.get("/{project_id}/download-mapping-summary")
def download_mapping_summary(project_id: str, db: Session = Depends(get_db)):
    """Build and download an Excel summary of every variable's mapping decisions
    (included or not, mapped or skipped) — variable/value concept ids and names,
    unit/route/type concepts, and start/end datetime columns. For human review,
    unlike generate-mapping-csvs which only emits ETL-ready rows."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.concept_decisions:
        raise HTTPException(status_code=400, detail="No concept decisions saved yet")

    def lookup_name(concept_id: int) -> str:
        try:
            info = _get_concept_info(concept_id)
        except Exception:
            return ""
        return info[1] if info else ""

    column_values = _all_distinct_values(project)
    buf = generate_mapping_summary_excel(_ordered_decisions(project), lookup_name, column_values)

    file_name = f"{project_id}_mapping_summary.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
