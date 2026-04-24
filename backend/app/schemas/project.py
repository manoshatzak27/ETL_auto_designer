from datetime import datetime
from typing import Any
from pydantic import BaseModel, field_validator


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    source_filename: str
    source_delimiter: str
    source_encoding: str
    source_columns: list[str] = []
    source_row_count: int
    etl_config: dict[str, Any] = {}
    generated_code: str
    generated_scripts: dict[str, Any] = {}
    last_execution_status: str
    output_files: list[str] = []
    mapping_files: dict[str, Any] = {}

    model_config = {"from_attributes": True}

    # Coerce NULL DB values to safe defaults so existing rows don't 500
    @field_validator("etl_config", "generated_scripts", "mapping_files", mode="before")
    @classmethod
    def _dict_or_empty(cls, v: Any) -> dict:
        return v if isinstance(v, dict) else {}

    @field_validator("source_columns", "output_files", mode="before")
    @classmethod
    def _list_or_empty(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    source_filename: str
    last_execution_status: str

    model_config = {"from_attributes": True}


class ETLConfigUpdate(BaseModel):
    table: str
    config: dict[str, Any]


class GenerateCodeRequest(BaseModel):
    tables: list[str] | None = None


class ConceptSearchRequest(BaseModel):
    query: str
    top_k: int = 20
    use_reranker: bool = False
