from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.services.etl_executor import execute_etl_scripts
from app.config import settings

router = APIRouter(prefix="/projects", tags=["execution"])


@router.post("/{project_id}/execute")
async def execute_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.generated_scripts:
        raise HTTPException(status_code=400, detail="No generated scripts. Generate scripts first.")

    output_dir = settings.get_output_path() / project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Respect per-table enabled flag from the Source-step picker. Optional
    # tables that the user un-checked are excluded from execution even if a
    # stale generated_scripts entry exists from before they disabled them.
    # Domain-routed tables (measurement/observation/drug_exposure/procedure/
    # condition_occurrence) have no etl_config entry; they're always allowed.
    config: dict = project.etl_config or {}
    filtered_scripts = {
        table: code
        for table, code in (project.generated_scripts or {}).items()
        if table not in config
        or config[table].get("enabled", True) is not False
    }

    log, status, output_files = await execute_etl_scripts(
        filtered_scripts,
        source_path=project.source_path,
        output_dir=str(output_dir),
        project_id=project_id,
        mapping_files=project.mapping_files or {},
        project_name=project.name,
    )

    project.last_execution_log = log
    project.last_execution_status = status
    project.output_files = output_files
    db.commit()

    return {"status": status, "log": log, "output_files": output_files}


@router.get("/{project_id}/execution-log")
def get_execution_log(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "status": project.last_execution_status,
        "log": project.last_execution_log,
        "output_files": project.output_files,
    }


@router.get("/{project_id}/download/{filename}")
def download_output(project_id: str, filename: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    output_dir = settings.get_output_path() / project_id
    file_path = output_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    safe_name = Path(filename).name
    if safe_name not in [Path(f).name for f in (project.output_files or [])]:
        raise HTTPException(status_code=403, detail="File not in project outputs")

    media_type = "text/x-python" if filename.endswith(".py") else "text/csv"
    return FileResponse(str(file_path), filename=filename, media_type=media_type)
