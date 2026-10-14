from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.schemas.project import GenerateCodeRequest, ProjectResponse
from app.services.code_generator import (
    generate_table_script,
    generate_all_table_scripts,
    get_generation_progress,
    SUPPORTED_TABLES,
    _DOMAIN_TABLES,
)

router = APIRouter(prefix="/projects", tags=["codegen"])


def _require_openai_key() -> None:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY is not configured on the backend. "
                "Set it in backend/.env and restart the server."
            ),
        )


@router.get("/{project_id}/generate/{table}/progress")
def generate_progress(project_id: str, table: str):
    """Poll the live token count + partial code of an in-flight AI patch call for this table.

    Returns {"active": false} when no AI patch is currently running for it
    (either nothing is generating, or the current generation is the
    deterministic-only path with no extra instructions to apply).
    """
    progress = get_generation_progress(project_id, table)
    if progress is None:
        return {"active": False, "used": 0, "limit": 0, "content": ""}
    return {"active": True, **progress}


@router.post("/{project_id}/generate/{table}", response_model=ProjectResponse)
async def generate_single_table(
    project_id: str,
    table: str,
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) the Python ETL script for a single OMOP table."""
    _require_openai_key()
    if table not in SUPPORTED_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown table '{table}'. Supported: {SUPPORTED_TABLES}")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        code, usage = await generate_table_script(project, table)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    scripts: dict = dict(project.generated_scripts or {})
    scripts[table] = code
    usage_by_table: dict = dict(project.generated_scripts_usage or {})
    if usage:
        usage_by_table[table] = usage
    else:
        usage_by_table.pop(table, None)

    # When stem_table is generated, also (re)generate its 5 domain-routing
    # scripts. They're deterministic templates that read stem_table.csv and
    # split by domain_id; the UI doesn't expose them as separate buttons, so
    # without this they'd never enter generated_scripts and Execute would
    # skip them (producing no measurement.csv, observation.csv, etc.).
    if table == "stem_table":
        for dt in _DOMAIN_TABLES:
            dt_code, dt_usage = await generate_table_script(project, dt)
            scripts[dt] = dt_code
            if dt_usage:
                usage_by_table[dt] = dt_usage
            else:
                usage_by_table.pop(dt, None)

    project.generated_scripts = scripts
    project.generated_scripts_usage = usage_by_table

    project.last_execution_status = ""
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/generate", response_model=ProjectResponse)
async def generate_all_tables(
    project_id: str,
    payload: GenerateCodeRequest,
    db: Session = Depends(get_db),
):
    """Generate Python ETL scripts for all configured OMOP tables at once."""
    _require_openai_key()
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tables_to_gen = payload.tables or None

    try:
        if tables_to_gen:
            # Selective regeneration
            scripts: dict = dict(project.generated_scripts or {})
            usage_by_table: dict = dict(project.generated_scripts_usage or {})
            for table in tables_to_gen:
                if table in SUPPORTED_TABLES:
                    code, usage = await generate_table_script(project, table)
                    scripts[table] = code
                    if usage:
                        usage_by_table[table] = usage
                    else:
                        usage_by_table.pop(table, None)
        else:
            scripts, usage_by_table = await generate_all_table_scripts(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    project.generated_scripts = scripts
    project.generated_scripts_usage = usage_by_table
    project.last_execution_status = ""
    db.commit()
    db.refresh(project)
    return project
