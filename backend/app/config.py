from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    entitylinker_url: str = "http://localhost:8000/api/conceptlink"
    # Base URL of the staged OMOP matching pipeline (pipeline/service.py in the
    # "new concept finding" project), used by the Concepts step's bulk
    # "Load concepts" button. Empty disables the button rather than erroring.
    concept_matcher_url: str = "http://localhost:8002"
    # Ceiling on how long a whole-project match may take. A cold call pays the
    # embedding model load before the first column is scored, and the pipeline
    # costs roughly a quarter second per column after that.
    concept_matcher_timeout: float = 600.0
    database_url: str = "sqlite:///./etl_designer.db"
    upload_dir: str = "./uploads"
    output_dir: str = "./outputs"

    # OMOP Postgres target (used by the DB load step, not for wizard state)
    omop_db_host: str = ""
    omop_db_port: int = 5432
    omop_db_name: str = "omop"
    omop_db_user: str = ""
    omop_db_password: str = ""
    omop_default_schema: str = "cdm"
    omop_vocab_schema: str = "vocab"

    # Allow-listed root for vocabulary bundle paths (security guard for /load-vocabulary)
    athena_bundle_root: str = ""

    # Allow-listed root for /load-mappings-from-dir (defaults to uploads if empty)
    mappings_bundle_root: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_output_path(self) -> Path:
        p = Path(self.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
