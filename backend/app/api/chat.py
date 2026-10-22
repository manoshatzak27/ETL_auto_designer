from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse
from app.services import chat_service

router = APIRouter(prefix="/projects", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    table: str


class ProposalRequest(BaseModel):
    table: str


def _get_project(project_id: str, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _resolve_proposal(project: Project, table: str, status: str) -> str:
    """Mark the table's pending proposal `status` and return its code.

    A script the AI returns is only a proposal: it stays in the chat history
    with code_status "proposed" until the user applies or discards it.
    """
    # Copies, so SQLAlchemy sees the JSON column change.
    history = [dict(m) for m in (project.chat_history or [])]
    for msg in reversed(history):
        if msg.get("table") == table and msg.get("code_status") == "proposed":
            code = chat_service.extract_code(msg.get("content", ""))
            if code is None:
                break
            msg["code_status"] = status
            project.chat_history = history
            return code
    raise HTTPException(status_code=404, detail=f"No pending change for {table}.py")


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
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY is not configured on the backend. "
                "Set it in backend/.env and restart the server."
            ),
        )
    if req.table not in chat_service.SUPPORTED_TABLES:
        raise HTTPException(status_code=400, detail=f"Unknown table '{req.table}'")

    project = _get_project(project_id, db)

    history: list[dict] = [dict(m) for m in (project.chat_history or [])]

    try:
        result = await chat_service.chat(
            project=project,
            table=req.table,
            history=history,
            user_message=req.message,
        )
    except chat_service.ChatContextTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))

    # Persist new messages. Returned code is not applied here: it waits in the
    # history as a proposal, replacing any earlier one for the same table.
    proposed = result["code_updated"] and bool(result["updated_code"])
    if proposed:
        for msg in history:
            if msg.get("table") == req.table and msg.get("code_status") == "proposed":
                msg["code_status"] = "superseded"
    history.append({"role": "user", "content": req.message, "table": req.table})
    assistant_msg = {
        "role": "assistant",
        "content": result["response"],
        "table": req.table,
        "code_updated": proposed,
    }
    if proposed:
        assistant_msg["code_status"] = "proposed"
    history.append(assistant_msg)
    project.chat_history = history

    db.commit()

    return {"response": result["response"], "code_updated": proposed, "history": history}


@router.post("/{project_id}/chat/proposal/apply", response_model=ProjectResponse)
def apply_proposal(project_id: str, req: ProposalRequest, db: Session = Depends(get_db)):
    """Replace the table's script with the AI's pending proposal."""
    project = _get_project(project_id, db)
    code = _resolve_proposal(project, req.table, "applied")
    scripts: dict = dict(project.generated_scripts or {})
    scripts[req.table] = code
    project.generated_scripts = scripts
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/chat/proposal/discard")
def discard_proposal(project_id: str, req: ProposalRequest, db: Session = Depends(get_db)):
    """Drop the table's pending proposal; the script stays as it is."""
    project = _get_project(project_id, db)
    _resolve_proposal(project, req.table, "discarded")
    db.commit()
    return {"history": project.chat_history}


@router.delete("/{project_id}/chat")
def clear_chat_history(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.chat_history = []
    db.commit()
    return {"ok": True}
