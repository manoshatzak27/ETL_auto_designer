from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.schemas.project import ETLConfigUpdate, ProjectResponse

router = APIRouter(prefix="/projects", tags=["mappings"])

VALID_TABLES = {"person", "visit_occurrence", "observation_period", "stem_table", "death"}


@router.patch("/{project_id}/config", response_model=ProjectResponse)
def update_table_config(project_id: str, payload: ETLConfigUpdate, db: Session = Depends(get_db)):
    if payload.table not in VALID_TABLES:
        raise HTTPException(status_code=400, detail=f"table must be one of {VALID_TABLES}")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    config = dict(project.etl_config or {})
    config[payload.table] = payload.config
    project.etl_config = config
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}/config")
def get_config(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.etl_config or {}


@router.get("/{project_id}/config/{table}")
def get_table_config(project_id: str, table: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if table not in VALID_TABLES:
        raise HTTPException(status_code=400, detail=f"table must be one of {VALID_TABLES}")
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    config = project.etl_config or {}
    return config.get(table, {})
