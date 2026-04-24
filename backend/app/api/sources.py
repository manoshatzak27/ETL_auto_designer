import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse
from app.services.schema_inferrer import infer_schema
from app.config import settings

router = APIRouter(prefix="/projects", tags=["sources"])

MAPPING_FILENAMES = {
    "variable_mapping": "variable_mapping.csv",
    "value_mapping": "value_mapping.csv",
    "variable_value_mapping": "variable_value_mapping.csv",
    "custom_mappings": "custom_mappings.csv",
}


@router.post("/{project_id}/upload-source", response_model=ProjectResponse)
async def upload_source(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # file.filename can be None in some browsers — use a safe fallback
    safe_name = Path(file.filename).name if file.filename else "source.csv"

    project_upload_dir = settings.get_upload_path() / project_id
    project_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = project_upload_dir / safe_name

    contents = await file.read()
    dest.write_bytes(contents)

    schema = infer_schema(str(dest))

    project.source_filename = safe_name
    project.source_path = str(dest)
    project.source_delimiter = schema["delimiter"]
    project.source_encoding = schema["encoding"]
    project.source_columns = schema["columns"]
    project.source_row_count = schema["row_count"]
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

    project_mapping_dir = settings.get_upload_path() / project_id / "mappings"
    project_mapping_dir.mkdir(parents=True, exist_ok=True)
    dest = project_mapping_dir / f"{mapping_type}.csv"

    contents = await file.read()
    dest.write_bytes(contents)

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

    source_dir = Path(payload.directory)
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {payload.directory}")

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


@router.get("/{project_id}/source-preview")
def source_preview(project_id: str, rows: int = 5, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.source_path or not Path(project.source_path).exists():
        raise HTTPException(status_code=404, detail="Source file not uploaded")

    import pandas as pd
    df = pd.read_csv(
        project.source_path,
        sep=project.source_delimiter if project.source_delimiter else ",",
        encoding=project.source_encoding if project.source_encoding else "utf-8",
        nrows=rows,
        dtype=str,
    )
    return {"columns": list(df.columns), "rows": df.fillna("").to_dict(orient="records")}
