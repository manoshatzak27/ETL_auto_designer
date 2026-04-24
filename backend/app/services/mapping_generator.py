"""
Generates variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv
and custom_mappings.csv from the user's concept decisions.

Decision structure per variable:
{
  "strategy": "map_variable" | "map_values" | "map_both" | "skip",
  "variable_concept": {"concept_id": int, "concept_name": str} | null,
  "value_concepts": {
      "<source_value>": {"concept_id": int, "concept_name": str}
  }
}

Strategies:
  map_variable  → variable_mapping.csv only (numeric variables)
  map_values    → variable_value_mapping.csv (categorical: each variable+value IS its own concept)
  map_both      → variable_mapping.csv + value_mapping.csv
                  (categorical: variable has a concept + each value has a value_as_concept_id)
  skip          → omitted from all files
"""
import os
from datetime import date
from pathlib import Path
import pandas as pd

VALID_START = "1970-01-01"
VALID_END = "2099-12-31"
CUSTOM_CONCEPT_THRESHOLD = 2_000_000_000


def _base_row(variable: str, concept: dict) -> dict:
    return {
        "variable_source_code": variable,
        "source_code_description": concept.get("description", ""),
        "target_concept_id": concept["concept_id"],
        "target_concept_name": concept.get("concept_name", ""),
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }


def _value_row(variable: str, value: str, concept: dict) -> dict:
    return {
        "variable_source_code": variable,
        "value_source_code": value,
        "source_code_description": concept.get("description", ""),
        "target_concept_id": concept["concept_id"],
        "target_concept_name": concept.get("concept_name", ""),
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }


def generate_mapping_csvs(concept_decisions: dict, output_dir: str) -> dict[str, str]:
    """
    Generates the 4 mapping CSVs from concept_decisions dict.
    Returns a dict mapping key → absolute file path for each CSV produced.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    variable_rows: list[dict] = []
    value_rows: list[dict] = []
    var_value_rows: list[dict] = []
    custom_rows: list[dict] = []

    for variable, decision in concept_decisions.items():
        strategy = decision.get("strategy", "skip")
        if strategy == "skip":
            continue

        var_concept = decision.get("variable_concept") or {}
        val_concepts: dict = decision.get("value_concepts") or {}

        # --- variable_mapping.csv ---
        if strategy in ("map_variable", "map_both") and var_concept.get("concept_id"):
            variable_rows.append(_base_row(variable, var_concept))
            if var_concept["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                custom_rows.append(_custom_row(var_concept))

        # --- variable_value_mapping.csv  (categorical: variable+value = concept) ---
        if strategy == "map_values":
            for val, vc in val_concepts.items():
                if vc.get("concept_id"):
                    var_value_rows.append(_value_row(variable, val, vc))
                    if vc["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                        custom_rows.append(_custom_row(vc))

        # --- value_mapping.csv  (value_as_concept_id alongside variable concept) ---
        if strategy == "map_both":
            for val, vc in val_concepts.items():
                if vc.get("concept_id"):
                    value_rows.append(_value_row(variable, val, vc))
                    if vc["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                        custom_rows.append(_custom_row(vc))

    files: dict[str, str] = {}

    if variable_rows:
        df = pd.DataFrame(variable_rows).sort_values("variable_source_code")
        p = str(output_path / "variable_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["variable_mapping"] = p

    if value_rows:
        df = pd.DataFrame(value_rows).sort_values(["variable_source_code", "value_source_code"])
        p = str(output_path / "value_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["value_mapping"] = p

    if var_value_rows:
        df = pd.DataFrame(var_value_rows).sort_values(["variable_source_code", "value_source_code"])
        p = str(output_path / "variable_value_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["variable_value_mapping"] = p

    if custom_rows:
        seen: set[int] = set()
        unique = [r for r in custom_rows if r["concept_id"] not in seen and not seen.add(r["concept_id"])]
        df = pd.DataFrame(unique).sort_values("concept_id")
        p = str(output_path / "custom_mappings.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["custom_mappings"] = p

    return files


def _custom_row(concept: dict) -> dict:
    return {
        "concept_id": concept["concept_id"],
        "concept_name": concept.get("concept_name", ""),
        "domain_id": concept.get("domain_id", "Observation"),
        "vocabulary_id": concept.get("vocabulary_id", "VOLABIOS"),
        "concept_class_id": concept.get("concept_class_id", "Clinical Finding"),
        "standard_concept": "S",
        "concept_code": concept.get("concept_code", str(concept["concept_id"])),
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }
