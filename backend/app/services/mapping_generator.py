"""
Generates variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv
and custom_mappings.csv from the user's concept decisions.

Decision structure per variable:
{
  "strategy": "map_variable" | "map_values" | "map_both" | "skip",
  "variable_concept": {"concept_id": int, "concept_name": str,
                       # optional fields, present when concept was created via
                       # the "Create custom concept" form:
                       "concept_code": str, "domain_id_str": str,
                       "vocabulary_id": str, "concept_class_id": str,
                       "is_custom": True} | null,
  "value_concepts": {
      "<source_value>": {... same shape ...}
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
DEFAULT_CUSTOM_VOCABULARY = "CUSTOM"


def _base_row(variable: str, concept: dict, domain_id: int | None = None) -> dict:
    return {
        "variable_source_code": variable,
        "source_code_description": concept.get("description", ""),
        "target_concept_id": concept["concept_id"],
        "target_concept_name": concept.get("concept_name", ""),
        "domain_id": domain_id if domain_id is not None else "",
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }


def _value_row(variable: str, value: str, concept: dict, domain_id: int | None = None) -> dict:
    return {
        "variable_source_code": variable,
        "value_source_code": value,
        "source_code_description": concept.get("description", ""),
        "target_concept_id": concept["concept_id"],
        "target_concept_name": concept.get("concept_name", ""),
        "domain_id": domain_id if domain_id is not None else "",
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }


def generate_mapping_csvs(
    concept_decisions: dict,
    output_dir: str,
    custom_vocabulary_id: str = DEFAULT_CUSTOM_VOCABULARY,
) -> dict[str, str]:
    """
    Generates the 4 mapping CSVs from concept_decisions dict.
    `custom_vocabulary_id` is the project-level vocab id that custom concepts
    (id >= 2_000_000_000) are tagged with when the concept dict doesn't carry
    one of its own.
    Returns a dict mapping key → absolute file path for each CSV produced.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    variable_rows: list[dict] = []
    value_rows: list[dict] = []
    var_value_rows: list[dict] = []
    custom_rows: list[dict] = []
    unit_rows: list[dict] = []
    route_rows: list[dict] = []
    modifier_rows: list[dict] = []
    condition_status_rows: list[dict] = []
    qualifier_rows: list[dict] = []

    for variable, decision in concept_decisions.items():
        strategy = decision.get("strategy", "skip")
        if strategy == "skip":
            continue

        var_concept = decision.get("variable_concept") or {}
        val_concepts: dict = decision.get("value_concepts") or {}
        domain_id: int | None = decision.get("domain_id") or None

        # --- variable_mapping.csv ---
        if strategy in ("map_variable", "map_both") and var_concept.get("concept_id"):
            variable_rows.append(_base_row(variable, var_concept, domain_id=domain_id))
            if var_concept["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                custom_rows.append(_custom_row(var_concept, custom_vocabulary_id))

        # --- variable_value_mapping.csv  (categorical: variable+value = concept) ---
        if strategy == "map_values":
            for val, vc in val_concepts.items():
                if vc.get("concept_id"):
                    var_value_rows.append(_value_row(variable, val, vc, domain_id=vc.get("domain_id")))
                    if vc["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                        custom_rows.append(_custom_row(vc, custom_vocabulary_id))

        # --- value_mapping.csv  (value_as_concept_id alongside variable concept) ---
        if strategy == "map_both":
            for val, vc in val_concepts.items():
                if vc.get("concept_id"):
                    value_rows.append(_value_row(variable, val, vc, domain_id=domain_id))
                    if vc["concept_id"] >= CUSTOM_CONCEPT_THRESHOLD:
                        custom_rows.append(_custom_row(vc, custom_vocabulary_id))

        # --- unit_mapping.csv  (measurement/observation/drug dose unit_source_value → unit_concept_id) ---
        unit_mapping = decision.get("unit_mapping") or {}
        unit_col: str | None = unit_mapping.get("unit_col")
        unit_concepts: dict = unit_mapping.get("unit_concepts") or {}
        if unit_col and unit_concepts:
            for unit_val, unit_cid in unit_concepts.items():
                if unit_cid:
                    unit_rows.append({
                        "variable_source_code": variable,
                        "unit_col": unit_col,
                        "unit_source_value": unit_val,
                        "unit_concept_id": int(unit_cid),
                    })

        # --- route_mapping.csv  (drug_exposure route_source_value → route_concept_id) ---
        route_mapping = decision.get("route_mapping") or {}
        route_col: str | None = route_mapping.get("route_col")
        route_concepts: dict = route_mapping.get("route_concepts") or {}
        if route_col and route_concepts:
            for route_val, route_cid in route_concepts.items():
                if route_cid:
                    route_rows.append({
                        "variable_source_code": variable,
                        "route_col": route_col,
                        "route_source_value": route_val,
                        "route_concept_id": int(route_cid),
                    })

        # --- modifier_mapping.csv  (procedure_occurrence modifier_source_value → modifier_concept_id) ---
        modifier_mapping = decision.get("modifier_mapping") or {}
        modifier_col: str | None = modifier_mapping.get("modifier_col")
        modifier_concepts: dict = modifier_mapping.get("modifier_concepts") or {}
        if modifier_col and modifier_concepts:
            for modifier_val, modifier_cid in modifier_concepts.items():
                if modifier_cid:
                    modifier_rows.append({
                        "variable_source_code": variable,
                        "modifier_col": modifier_col,
                        "modifier_source_value": modifier_val,
                        "modifier_concept_id": int(modifier_cid),
                    })

        # --- condition_status_mapping.csv  (condition_occurrence condition_status_source_value → condition_status_concept_id) ---
        condition_status_mapping = decision.get("condition_status_mapping") or {}
        condition_status_col: str | None = condition_status_mapping.get("condition_status_col")
        condition_status_concepts: dict = condition_status_mapping.get("condition_status_concepts") or {}
        if condition_status_col and condition_status_concepts:
            for status_val, status_cid in condition_status_concepts.items():
                if status_cid:
                    condition_status_rows.append({
                        "variable_source_code": variable,
                        "condition_status_col": condition_status_col,
                        "condition_status_source_value": status_val,
                        "condition_status_concept_id": int(status_cid),
                    })

        # --- qualifier_mapping.csv  (observation qualifier_source_value → qualifier_concept_id) ---
        qualifier_mapping = decision.get("qualifier_mapping") or {}
        qualifier_col: str | None = qualifier_mapping.get("qualifier_col")
        qualifier_concepts: dict = qualifier_mapping.get("qualifier_concepts") or {}
        if qualifier_col and qualifier_concepts:
            for qualifier_val, qualifier_cid in qualifier_concepts.items():
                if qualifier_cid:
                    qualifier_rows.append({
                        "variable_source_code": variable,
                        "qualifier_col": qualifier_col,
                        "qualifier_source_value": qualifier_val,
                        "qualifier_concept_id": int(qualifier_cid),
                    })

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

    if unit_rows:
        df = pd.DataFrame(unit_rows).sort_values(["variable_source_code", "unit_source_value"])
        p = str(output_path / "unit_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["unit_mapping"] = p

    if route_rows:
        df = pd.DataFrame(route_rows).sort_values(["variable_source_code", "route_source_value"])
        p = str(output_path / "route_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["route_mapping"] = p

    if modifier_rows:
        df = pd.DataFrame(modifier_rows).sort_values(["variable_source_code", "modifier_source_value"])
        p = str(output_path / "modifier_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["modifier_mapping"] = p

    if condition_status_rows:
        df = pd.DataFrame(condition_status_rows).sort_values(["variable_source_code", "condition_status_source_value"])
        p = str(output_path / "condition_status_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["condition_status_mapping"] = p

    if qualifier_rows:
        df = pd.DataFrame(qualifier_rows).sort_values(["variable_source_code", "qualifier_source_value"])
        p = str(output_path / "qualifier_mapping.csv")
        df.to_csv(p, index=False, encoding="utf-8")
        files["qualifier_mapping"] = p

    return files


def _custom_row(concept: dict, custom_vocabulary_id: str = DEFAULT_CUSTOM_VOCABULARY) -> dict:
    # Custom concepts created via the form carry `domain_id_str` (e.g. "Observation"),
    # plus optional concept_code / concept_class_id. Anything missing falls back to
    # sensible defaults so a bare manual-ID entry still produces a valid row.
    return {
        "concept_id": concept["concept_id"],
        "concept_name": concept.get("concept_name", ""),
        "domain_id": concept.get("domain_id_str") or "Observation",
        "vocabulary_id": concept.get("vocabulary_id") or custom_vocabulary_id,
        "concept_class_id": concept.get("concept_class_id") or "Clinical Finding",
        "standard_concept": "S",
        "concept_code": concept.get("concept_code") or str(concept["concept_id"]),
        "valid_start_date": VALID_START,
        "valid_end_date": VALID_END,
        "invalid_reason": "",
    }
