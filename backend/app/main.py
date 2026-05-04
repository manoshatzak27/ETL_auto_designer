from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import create_tables
from app.api import projects, sources, mappings, codegen, execution, concept_mapping, chat

app = FastAPI(
    title="OMOP ETL Auto-Designer API",
    description="Code-less OMOP ETL builder with OpenAI-powered code generation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(mappings.router, prefix="/api")
app.include_router(concept_mapping.router, prefix="/api")
app.include_router(codegen.router, prefix="/api")
app.include_router(execution.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.on_event("startup")
async def on_startup():
    create_tables()


@app.get("/api/health")
async def health():
    return {"status": "ok"}
