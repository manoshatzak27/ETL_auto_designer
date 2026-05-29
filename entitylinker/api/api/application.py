import os
import argparse
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.concept_link import router
from entitylinker.config import load_settings
from entitylinker.linker.concept_linker import ConceptLinker
from entitylinker.linker.reranker import Reranker

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=False)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: initialize heavy objects once, reuse across requests."""
    # Resolve config path (env var is used inside load_settings if None)
    cfg_path = os.getenv("ENTITYLINKER_CONFIG")

    # Load validated settings (raises helpful errors if missing/invalid)
    settings = load_settings(config_path=cfg_path)

    # Build singletons
    linker = ConceptLinker(config_path=cfg_path)
    reranker = Reranker()  # uses OPENAI_API_KEY from env if applicable

    # Stash in app.state for dependencies to access
    app.state.settings = settings
    app.state.linker = linker
    app.state.reranker = reranker

    try:
        yield
    finally:
        # Optional: explicit cleanup (usually not required)
        # del app.state.linker
        # del app.state.reranker
        pass

app = FastAPI(
    title="EntityLinker API",
    description="API for linking clinical text to OMOP vocabulary concepts.",
    version="0.1.0",
    lifespan=lifespan,
)

# Routes
app.include_router(router, prefix="/api")

@app.get("/")
def root():
    return {"message": "EntityLinker API is running"}

def main():
    """Console entrypoint: `entitylinker-api`."""
    # Parse CLI flags (so users can explicitly pass the YAML path)
    parser = argparse.ArgumentParser(description="Run EntityLinker API server")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config.yaml (alternatively set ENTITYLINKER_CONFIG env var)",
    )
    args, _ = parser.parse_known_args()

    if args.config:
        os.environ["ENTITYLINKER_CONFIG"] = args.config  # consumed in lifespan

    # Import uvicorn only when needed
    import uvicorn

    host = os.getenv("EL_API_HOST", "0.0.0.0")
    port = int(os.getenv("EL_API_PORT", "8000"))
    reload = os.getenv("EL_API_RELOAD", "0") == "1"  # set EL_API_RELOAD=1 for dev

    uvicorn.run("api.application:app", host=host, port=port, reload=reload)
