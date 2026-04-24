import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Source dataset info
    source_filename: Mapped[str] = mapped_column(String(512), default="")
    source_path: Mapped[str] = mapped_column(String(512), default="")
    source_delimiter: Mapped[str] = mapped_column(String(10), default="")
    source_encoding: Mapped[str] = mapped_column(String(50), default="")
    source_columns: Mapped[list] = mapped_column(JSON, default=list)
    source_row_count: Mapped[int] = mapped_column(default=0)

    # Full ETL config (JSON blob per wizard step)
    etl_config: Mapped[dict] = mapped_column(JSON, default=dict)

    # Generated code and execution status
    generated_code: Mapped[str] = mapped_column(Text, default="")
    last_execution_log: Mapped[str] = mapped_column(Text, default="")
    last_execution_status: Mapped[str] = mapped_column(String(20), default="")  # success | error | running
    output_files: Mapped[list] = mapped_column(JSON, default=list)

    # Concept mapping decisions made in Step 2 (per-variable strategy + concept selections)
    concept_decisions: Mapped[dict] = mapped_column(JSON, default=dict)

    # Generated concept mapping CSVs (produced from concept_decisions)
    mapping_files: Mapped[dict] = mapped_column(JSON, default=dict)

    # Per-table generated Python scripts  {table_name: python_source_code}
    generated_scripts: Mapped[dict] = mapped_column(JSON, default=dict)
