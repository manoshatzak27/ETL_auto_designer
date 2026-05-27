"""EntityLinker configuration loader.

This module loads user-supplied **YAML** configuration for EntityLinker.
It defines a `Settings` model (validated with Pydantic) and a `load_settings`
function that reads a YAML file from an explicit path or from the
`ENTITYLINKER_CONFIG` environment variable.

YAML schema example:
    ```yaml
    data_dir: "C:/Users/you/Documents/EntityLinkerData"
    vocabularies:
      - SNOMED
      - LOINC
    ```
"""

import os
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field, field_validator
import yaml


class Settings(BaseModel):
    """Validated configuration settings for EntityLinker.

    Attributes:
        data_dir: Absolute path to the directory containing OMOP source files
            (e.g., `CONCEPT.csv`) and optional caches (embeddings, FAISS index).
        vocabularies: List of OMOP vocabulary IDs to load (e.g., ["SNOMED", "LOINC"]).
    """

    data_dir: Path = Field(...)
    vocabularies: List[str] = Field(...)

    @field_validator("data_dir", mode="before")
    def expand_path(cls, v):
        """Expand and absolutize the configured data directory.

        This expands `~` and environment variables within the path and converts
        it to an absolute `Path`.

        Args:
            v: Raw value from YAML (string or path-like).

        Returns:
            Path: An absolute, expanded `Path` instance.
        """
        return Path(os.path.expandvars(os.path.expanduser(str(v)))).resolve()

    @field_validator("vocabularies", mode="before")
    def ensure_list(cls, v):
        """Normalize vocabularies into a list of strings.

        Accepts either a YAML list or a single comma-separated string.

        Args:
            v: Raw value from YAML (list or string).

        Returns:
            list[str]: A list of vocabulary identifiers (possibly empty).
        """
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return list(v or [])


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load and validate YAML configuration.

    The loader reads a YAML file and returns a validated `Settings` object.
    Path resolution priority is:

    1. The explicit `config_path` argument (recommended).
    2. The `ENTITYLINKER_CONFIG` environment variable.

    If neither is provided, or the resolved path does not exist, a
    `FileNotFoundError` is raised.

    Args:
        config_path: Optional path to `config.yaml`. If omitted, the function
            will try to read from the `ENTITYLINKER_CONFIG` environment variable.

    Returns:
        Settings: A validated configuration object.
    """
    raw = str(config_path) if config_path is not None else os.getenv("ENTITYLINKER_CONFIG")
    if not raw:
        raise FileNotFoundError(
            "No config file provided. Pass --config /path/to/config.yaml "
            "or set ENTITYLINKER_CONFIG=/path/to/config.yaml"
        )

    path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    # Parse YAML safely; empty files yield {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Pydantic validates required fields and types
    settings = Settings(**data)

    return settings
