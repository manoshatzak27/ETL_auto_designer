import re
import shutil
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectSummary
from app.config import settings

router = APIRouter(prefix="/projects", tags=["projects"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"


@router.get("/", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.updated_at.desc()).all()


@router.post("/", response_model=ProjectResponse, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    slug = _slugify(payload.name)
    if db.query(Project).filter(Project.id == slug).first():
        raise HTTPException(status_code=409, detail="A project with this name already exists")
    project = Project(id=slug, name=payload.name, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.custom_vocabulary_id is not None:
        # Normalize: strip + uppercase. Forbid empty after strip.
        v = payload.custom_vocabulary_id.strip()
        if not v:
            raise HTTPException(status_code=400, detail="custom_vocabulary_id cannot be empty")
        project.custom_vocabulary_id = v
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/copy", response_model=ProjectResponse, status_code=201)
def copy_project(project_id: str, db: Session = Depends(get_db)):
    source = db.query(Project).filter(Project.id == project_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Project not found")

    base_name = f"{source.name} (copy)"
    name = base_name
    slug = _slugify(name)
    suffix = 2
    while db.query(Project).filter(Project.id == slug).first():
        name = f"{base_name} {suffix}"
        slug = _slugify(name)
        suffix += 1

    def _rewrite_path(path: str) -> str:
        return path.replace(f"/{project_id}/", f"/{slug}/") if path else path

    for base_path in (settings.get_upload_path(), settings.get_output_path()):
        src_dir = base_path / project_id
        if src_dir.exists():
            shutil.copytree(src_dir, base_path / slug)

    new_source_files = []
    for entry in source.source_files or []:
        entry = dict(entry)
        if entry.get("path"):
            entry["path"] = _rewrite_path(entry["path"])
        new_source_files.append(entry)

    project = Project(
        id=slug,
        name=name,
        description=source.description,
        source_filename=source.source_filename,
        source_path=_rewrite_path(source.source_path),
        source_delimiter=source.source_delimiter,
        source_encoding=source.source_encoding,
        source_columns=list(source.source_columns or []),
        source_row_count=source.source_row_count,
        etl_config=dict(source.etl_config or {}),
        generated_code=source.generated_code,
        last_execution_log=source.last_execution_log,
        last_execution_status=source.last_execution_status,
        output_files=[_rewrite_path(f) for f in (source.output_files or [])],
        concept_decisions=dict(source.concept_decisions or {}),
        custom_vocabulary_id=source.custom_vocabulary_id,
        mapping_files={k: _rewrite_path(v) for k, v in (source.mapping_files or {}).items()},
        source_files=new_source_files,
        generated_scripts=dict(source.generated_scripts or {}),
        chat_history=list(source.chat_history or []),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    for base_path in (settings.get_upload_path(), settings.get_output_path()):
        project_dir = base_path / project_id
        if project_dir.exists():
            shutil.rmtree(project_dir)
