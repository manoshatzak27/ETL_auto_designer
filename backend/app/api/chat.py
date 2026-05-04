from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models.project import Project
from app.services import chat_service

router = APIRouter(prefix="/projects", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    table: str


@router.get("/{project_id}/chat")
def get_chat_history(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"history": project.chat_history or []}


@router.post("/{project_id}/chat")
async def send_chat_message(
    project_id: str,
    req: ChatRequest,
    db: Session = Depends(get_db),
):
    if req.table not in chat_service.SUPPORTED_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown table '{req.table}'")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    history: list[dict] = list(project.chat_history or [])

    result = await chat_service.chat(
        project=project,
        table=req.table,
        history=history,
        user_message=req.message,
    )

    # Persist new messages
    history.append({"role": "user", "content": req.message, "table": req.table})
    history.append({
        "role": "assistant",
        "content": result["response"],
        "table": req.table,
        "code_updated": result["code_updated"],
    })
    project.chat_history = history

    # Apply code update if AI returned one
    if result["code_updated"] and result["updated_code"]:
        scripts: dict = dict(project.generated_scripts or {})
        scripts[req.table] = result["updated_code"]
        project.generated_scripts = scripts

    db.commit()
    db.refresh(project)

    return {
        "response": result["response"],
        "code_updated": result["code_updated"],
        "generated_scripts": project.generated_scripts,
    }


@router.delete("/{project_id}/chat")
def clear_chat_history(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.chat_history = []
    db.commit()
    return {"ok": True}
