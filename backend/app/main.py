import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import create_tables
from app.api import projects, sources, mappings, codegen, execution, concept_mapping, chat, dbload

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    if not settings.openai_api_key:
        logger.warning(
            "OPENAI_API_KEY is not set. Code generation and chat endpoints will return 503. "
            "Set it in backend/.env to enable AI features."
        )
    yield


app = FastAPI(
    title="OMOP ETL Auto-Designer API",
    description="Code-less OMOP ETL builder with OpenAI-powered code generation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:4173",
    ],
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
app.include_router(dbload.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "openai_configured": bool(settings.openai_api_key),
    }
