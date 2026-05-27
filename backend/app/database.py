from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    from app.models import project  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_projects_schema()


# Hand-rolled migrations for SQLite — Alembic is overkill for this project.
# Each entry: (column_name, SQL fragment used in ALTER TABLE ADD COLUMN).
_PROJECTS_NEW_COLUMNS: list[tuple[str, str]] = [
    ("custom_vocabulary_id", "VARCHAR(64) DEFAULT 'CUSTOM' NOT NULL"),
]


def _migrate_projects_schema() -> None:
    """Add columns that newer model versions expect but old DBs don't have.
    Idempotent: skips columns that already exist."""
    insp = inspect(engine)
    if "projects" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("projects")}
    with engine.begin() as conn:
        for name, ddl in _PROJECTS_NEW_COLUMNS:
            if name in existing:
                continue
            conn.execute(text(f'ALTER TABLE projects ADD COLUMN {name} {ddl}'))
