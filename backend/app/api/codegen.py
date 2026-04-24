from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.schemas.project import GenerateCodeRequest, ProjectResponse
from app.services.code_generator import (
    generate_table_script,
    generate_all_table_scripts,
    SUPPORTED_TABLES,
)

router = APIRouter(prefix="/projects", tags=["codegen"])


@router.post("/{project_id}/generate/{table}", response_model=ProjectResponse)
async def generate_single_table(
    project_id: str,
    table: str,
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) the Python ETL script for a single OMOP table."""
    if table not in SUPPORTED_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown table '{table}'. Supported: {SUPPORTED_TABLES}")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    code = await generate_table_script(project, table)

    scripts: dict = dict(project.generated_scripts or {})
    scripts[table] = code
    project.generated_scripts = scripts

    # Keep legacy generated_code field as concatenation for backward compat
    project.generated_code = "\n\n# " + "=" * 60 + "\n\n".join(
        f"# === {t}.py ===\n{scripts[t]}" for t in SUPPORTED_TABLES if t in scripts
    )

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
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tables_to_gen = payload.tables or None

    if tables_to_gen:
        # Selective regeneration
        scripts: dict = dict(project.generated_scripts or {})
        for table in tables_to_gen:
            if table in SUPPORTED_TABLES:
                scripts[table] = await generate_table_script(project, table)
    else:
        scripts = await generate_all_table_scripts(project)

    project.generated_scripts = scripts
    project.generated_code = "\n\n".join(
        f"# === {t}.py ===\n{scripts[t]}" for t in SUPPORTED_TABLES if t in scripts
    )
    project.last_execution_status = ""
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/concept-search")
async def concept_search(project_id: str, query: str, top_k: int = 20):
    """Proxy concept search to the EntityLinker service."""
    import httpx
    from app.config import settings

    if not settings.entitylinker_url:
        raise HTTPException(status_code=503, detail="EntityLinker URL not configured")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.entitylinker_url,
                json={"query": query, "top_k": top_k, "use_reranker": False},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"EntityLinker unavailable: {e}")
