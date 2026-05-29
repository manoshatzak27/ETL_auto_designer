"""
Dependency providers for FastAPI.

We initialize heavy singletons (ConceptLinker, Reranker) in the app lifespan
and store them on `app.state`. Dependencies below simply retrieve them per request.
"""

from fastapi import Request
from entitylinker.linker.concept_linker import ConceptLinker
from entitylinker.linker.reranker import Reranker
from entitylinker.config import Settings


def get_linker(request: Request) -> ConceptLinker:
    """Return the singleton ConceptLinker initialized at startup."""
    return request.app.state.linker


def get_reranker(request: Request) -> Reranker:
    """Return the singleton Reranker initialized at startup."""
    return request.app.state.reranker


def get_settings(request: Request) -> Settings:
    """Expose effective settings (handy for endpoints or debugging)."""
    return request.app.state.settings
