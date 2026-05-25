"""
API routes for the concept mapping step (Step 2).

Endpoints:
  GET  /projects/concept-lookup              → look up domain for a concept_id in CONCEPT.csv
  GET  /projects/{id}/column-values          → unique values per source column
  GET  /projects/{id}/concept-decisions       → load saved decisions
  POST /projects/{id}/concept-decisions       → save decisions (full replace)
  POST /projects/{id}/generate-mapping-csvs  → generate the 3 CSVs from decisions
"""
from pathlib import Path
from typing import Any
import threading
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import pandas as pd

from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse
from app.services.mapping_generator import generate_mapping_csvs
from app.config import settings

router = APIRouter(prefix="/projects", tags=["concept-mapping"])

# ── Concept domain lookup (CONCEPT.csv cache) ───────────────────────────────

_concept_df: "pd.DataFrame | None" = None
_concept_lock = threading.Lock()


def _get_concept_domain(concept_id: int) -> "str | None":
    global _concept_df
    if _concept_df is None:
        with _concept_lock:
            if _concept_df is None:
                path = settings.get_upload_path() / "concepts" / "CONCEPT.csv"
                if path.exists():
                    try:
                        _concept_df = pd.read_csv(
                            path,
                            sep="\t",
                            usecols=["concept_id", "domain_id"],
                            dtype={"domain_id": "category"},
                            index_col="concept_id",
                        )
                        _concept_df.index = _concept_df.index.astype("int64")
                    except Exception as exc:
                        print(f"[concept-lookup] Failed to load CONCEPT.csv: {exc}")
                        _concept_df = pd.DataFrame(
                            columns=["domain_id"],
                            index=pd.Index([], name="concept_id", dtype="int64"),
                        )
                else:
                    print(f"[concept-lookup] CONCEPT.csv not found at {path}")
                    _concept_df = pd.DataFrame(
                        columns=["domain_id"],
                        index=pd.Index([], name="concept_id", dtype="int64"),
                    )
    try:
        val = _concept_df.at[concept_id, "domain_id"]
        return str(val) if pd.notna(val) else None
    except KeyError:
        return None


@router.get("/concept-lookup/domain")
def concept_lookup(concept_id: int):
    """Return the domain_id string for a given concept_id from CONCEPT.csv."""
    domain = _get_concept_domain(concept_id)
    return {"concept_id": concept_id, "domain_id": domain, "found": domain is not None}


# ── Column unique values ────────────────────────────────────────────────────

@router.get("/{project_id}/column-values")
def get_column_values(
    project_id: str,
    max_values: int = 200,
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
    if not project.source_path or not Path(project.source_path).exists():
        raise HTTPException(status_code=400, detail="Source file not uploaded yet")

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
    files = generate_mapping_csvs(project.concept_decisions, output_dir)

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No mapping rows generated. Make sure at least one variable is mapped.",
        )

    project.mapping_files = files
    db.commit()
    db.refresh(project)
    return project
