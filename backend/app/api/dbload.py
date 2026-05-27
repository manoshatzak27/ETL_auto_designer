"""
OMOP Postgres database load endpoints.

Each endpoint guards against an unconfigured Postgres by returning 503 with a
helpful message. Run the docker-compose stack to bring up Postgres + DDL.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.services.code_generator import SUPPORTED_TABLES
from app.services.omop_loader import (
    LoadStatus,
    check_db_health,
    get_load_status,
    load_project_outputs,
    reset_load_status,
)
from app.services.vocab_loader import load_vocabulary

router = APIRouter(tags=["dbload"])


class LoadDatabaseRequest(BaseModel):
    schema_mode: Literal["shared", "project"] = "shared"
    schema_name: str | None = None
    truncate: bool = True


class LoadVocabularyRequest(BaseModel):
    bundle_path: str
    schema_name: str | None = None


# ─── DB health ─────────────────────────────────────────────────────────────────

@router.get("/db-health")
def db_health() -> dict:
    return check_db_health()


# ─── Per-project ETL load ──────────────────────────────────────────────────────

@router.post("/projects/{project_id}/load-database")
def trigger_load_database(
    project_id: str,
    payload: LoadDatabaseRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.last_execution_status != "success":
        raise HTTPException(
            status_code=400,
            detail="ETL has not run successfully yet. Execute the pipeline on Step 11 first.",
        )

    output_dir = settings.get_output_path() / project_id
    if not output_dir.exists():
        raise HTTPException(status_code=400, detail="No output directory for this project.")

    schema_name = payload.schema_name or (
        f"cdm_{project_id.replace('-', '_')}" if payload.schema_mode == "project" else "cdm"
    )

    reset_load_status(project_id)
    background_tasks.add_task(
        load_project_outputs,
        project_id=project_id,
        output_dir=str(output_dir),
        schema=schema_name,
        truncate=payload.truncate,
        table_order=SUPPORTED_TABLES,
    )

    return {
        "status": "started",
        "schema": schema_name,
        "tables": SUPPORTED_TABLES,
    }


@router.get("/projects/{project_id}/load-status")
def get_project_load_status(project_id: str) -> LoadStatus:
    return get_load_status(project_id)


# ─── Vocabulary load ──────────────────────────────────────────────────────────

@router.post("/load-vocabulary")
def trigger_load_vocabulary(
    payload: LoadVocabularyRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    allowed_root = Path(settings.athena_bundle_root).resolve() if settings.athena_bundle_root else None
    bundle = Path(payload.bundle_path).resolve()
    if not bundle.exists() or not bundle.is_dir():
        raise HTTPException(status_code=400, detail=f"Bundle directory not found: {payload.bundle_path}")
    if allowed_root is not None and allowed_root not in bundle.parents and bundle != allowed_root:
        raise HTTPException(
            status_code=403,
            detail=f"Bundle path must be inside ATHENA_BUNDLE_ROOT ({allowed_root}).",
        )

    schema_name = payload.schema_name or "cdm"
    background_tasks.add_task(
        load_vocabulary,
        bundle_path=str(bundle),
        schema=schema_name,
    )
    return {"status": "started", "schema": schema_name, "bundle_path": str(bundle)}
