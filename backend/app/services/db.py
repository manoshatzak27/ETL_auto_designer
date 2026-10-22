"""
Shared Postgres connection helper for the OMOP loaders and DDL applier.

Centralizes the psycopg2-availability check and the settings → connection
plumbing so each consumer doesn't reimplement the same guard rails.
"""
from __future__ import annotations

from typing import Any

from app.config import settings

try:
    import psycopg2
    from psycopg2 import sql as _sql
    from psycopg2.extensions import connection as PGConnection
    PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]
    _sql = None  # type: ignore[assignment]
    PGConnection = Any  # type: ignore[assignment,misc]
    PSYCOPG2_AVAILABLE = False


# Re-export so callers don't need to redo the try/except dance.
sql = _sql


def connect() -> "PGConnection":
    if not PSYCOPG2_AVAILABLE:
        raise RuntimeError(
            "psycopg2 is not installed. Install it (pip install psycopg2-binary) "
            "or use the docker-compose stack which bundles it."
        )
    if not settings.omop_db_host:
        raise RuntimeError(
            "OMOP_DB_HOST is not configured. Set OMOP_DB_* in backend/.env."
        )
    return psycopg2.connect(
        host=settings.omop_db_host,
        port=settings.omop_db_port,
        dbname=settings.omop_db_name,
        user=settings.omop_db_user,
        password=settings.omop_db_password,
    )
