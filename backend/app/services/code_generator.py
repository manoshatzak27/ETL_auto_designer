"""
Per-table ETL script generator.

Each OMOP table is generated independently via a dedicated OpenAI call.
The call includes:
  1. A shared system prompt (OMOP expert instructions)
  2. The full VOLABIOS reference script for that table (so the AI sees the exact style/structure)
  3. The user's structured ETL configuration for that table
  4. Optional free-text extra instructions from the user
"""
import json
from pathlib import Path
from openai import AsyncOpenAI
from app.config import settings

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
REFS_DIR = PROMPTS_DIR / "references"

# Tables we support, in dependency order
SUPPORTED_TABLES = [
    "location",
    "care_site",
    "provider",
    "person",
    "visit_occurrence",
    "observation_period",
    "stem_table",
    "death",
    "measurement",
    "observation",
    "drug_exposure",
    "procedure_occurrence",
    "condition_occurrence",
]

# Domain routing tables: read from stem_table.csv and filter by domain_id
_DOMAIN_TABLES: dict[str, int] = {
    "measurement": 1,
    "observation": 2,
    "drug_exposure": 3,
    "procedure_occurrence": 4,
    "condition_occurrence": 5,
}

# Tables that have a VOLABIOS reference script available
_REFERENCE_FILES: dict[str, str] = {
    "person": "person.py",
    "observation_period": "observation_period.py",
    "stem_table": "stem_table.py",
    "death": "death.py",
    # visit_occurrence has no reference script — uses prompt-only approach
}


def _load_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _reference_script(table: str) -> str:
    filename = _REFERENCE_FILES.get(table)
    if not filename:
        return ""
    return _load_text(REFS_DIR / filename)


def _table_prompt_hint(table: str) -> str:
    return _load_text(PROMPTS_DIR / f"{table}.txt")


def _system_prompt() -> str:
    base = _load_text(PROMPTS_DIR / "system_prompt.txt")
    if base:
        return base
    return (
        "You are an expert OMOP CDM v5.4 ETL engineer. "
        "Generate clean, production-ready standalone Python ETL scripts using only pandas, numpy, and the standard library."
    )


def _build_table_prompt(project, table: str) -> str:
    config: dict = (project.etl_config or {}).get(table, {})
    extra: str = config.get("extra_instructions", "").strip()
    concept_decisions: dict = project.concept_decisions or {}
    reference = _reference_script(table)
    hint = _table_prompt_hint(table)

    lines: list[str] = []

    # ── Reference implementation ──────────────────────────────────────────
    if reference:
        lines += [
            "## REFERENCE IMPLEMENTATION (VOLABIOS/PRIAS ETL)",
            "Study this script carefully. Your output MUST follow the same:",
            "- Module structure and import style",
            "- Variable naming conventions",
            "- Per-row loop pattern with explicit try/except per row",
            "- Logging setup (logging.basicConfig + module-level logger)",
            "- Inline comments style (non-obvious logic only)",
            "",
            "```python",
            reference,
            "```",
            "",
        ]
    else:
        lines += [
            "## TABLE DESCRIPTION",
            hint or f"Generate an OMOP {table} transformation.",
            "",
        ]

    _CONCEPT_MAPPING_TABLES = {"stem_table", "death"}

    # ── Standalone adapter instructions ──────────────────────────────────
    _NEEDS_PERSON_LOOKUP = {"visit_occurrence", "observation_period", "stem_table", "death"}
    _NEEDS_VISIT_LOOKUP = {"stem_table", "death"}

    if table in _DOMAIN_TABLES:
        # Domain routing tables read from stem_table.csv — no source file, no wrapper
        domain_id_val = _DOMAIN_TABLES[table]
        lines += [
            "## DOMAIN ROUTING RULES",
            f"This script populates the OMOP {table.upper()} table from the staging stem_table.",
            f"Input:   ETL_OUTPUT_DIR/stem_table.csv (semicolon-delimited, UTF-8)",
            f"Filter:  only rows where domain_id == {domain_id_val}",
            f"Output:  ETL_OUTPUT_DIR/{table}.csv (semicolon-delimited, UTF-8)",
            "Env var: ETL_OUTPUT_DIR → directory containing stem_table.csv and where output is written",
            "Print progress: e.g. 'Writing {table}.csv ... done (N records)'".format(table=table),
            "Guard: include `if __name__ == '__main__': main()`",
            "",
        ]
        # Inject person_id type so the generated script reads it correctly from stem_table.csv
        person_id_cfg: dict = (project.etl_config or {}).get("person", {}).get("mappings", {}).get("person_id", {})
        if person_id_cfg.get("auto_increment"):
            pid_note = "person_id in stem_table.csv is a sequential integer (auto-incremented). Read it as int."
            pid_cast = "int(row['person_id'])"
        else:
            transform = person_id_cfg.get("transform", "int_float")
            if transform == "str":
                pid_note = "person_id in stem_table.csv is a string (user chose str transform). Read it as str — do NOT cast to int."
                pid_cast = "str(row['person_id'])"
            elif transform == "int":
                pid_note = "person_id in stem_table.csv is an integer (user chose int transform). Read it as int."
                pid_cast = "int(row['person_id'])"
            else:  # int_float (default)
                pid_note = "person_id in stem_table.csv was stored via int(float(...)) (user chose int_float transform). Read it as int."
                pid_cast = "int(float(row['person_id']))"
        lines += [
            "## PERSON ID TYPE",
            pid_note,
            f"Use this exact cast when reading person_id: `{pid_cast}`",
            "Wrap the cast in try/except and skip the row if it fails.",
            "",
        ]
        # Inject stem_table config as context
        stem_cfg: dict = (project.etl_config or {}).get("stem_table", {})
        if stem_cfg:
            lines += [
                "## STEM TABLE CONFIG (context only — do not re-implement stem table logic)",
                "```json",
                json.dumps(stem_cfg, indent=2),
                "```",
                "",
            ]
    else:
        env_vars = [
            "  - ETL_SOURCE_PATH   → FULL path to the source CSV file (use directly as the file path)"
            "  - ETL_OUTPUT_DIR    → output directory for OMOP CSVs",
        ]
        if table in _CONCEPT_MAPPING_TABLES:
            env_vars += [
                "  - ETL_MAPPING_variable_mapping        → direct file path to variable_mapping.csv (may be empty/absent)",
                "  - ETL_MAPPING_value_mapping            → direct file path to value_mapping.csv (may be empty/absent)",
                "  - ETL_MAPPING_variable_value_mapping  → direct file path to variable_value_mapping.csv (may be empty/absent)",
                "    All mapping CSVs use comma delimiter and UTF-8 encoding.",
                "    If a path env var is empty or the file does not exist, treat that mapping as an empty dict.",
                "    CSV column names (exact):",
                "      variable_mapping.csv:       variable_source_code, target_concept_id, domain_id",
                "      value_mapping.csv:          variable_source_code, value_source_code, target_concept_id",
                "      variable_value_mapping.csv: variable_source_code, value_source_code, target_concept_id, domain_id",
                "    Example load pattern:",
                "      def _load_csv(path):",
                "          if not path: return pd.DataFrame()",
                "          try: return pd.read_csv(path, sep=',', encoding='utf-8')",
                "          except FileNotFoundError: return pd.DataFrame()",
                "      vm = _load_csv(os.environ.get('ETL_MAPPING_variable_mapping',''))",
                "      var_map = {r['variable_source_code'].lower(): r['target_concept_id'] for _,r in vm.iterrows()} if not vm.empty else {}",
            ]

        adaptation_lines = [
            "## ADAPTATION RULES",
            "The reference uses a `wrapper` object. Your script must NOT use it.",
            "Instead, read data from files using these environment variables:",
            *env_vars,
            "",
        ]
        if table in _NEEDS_PERSON_LOOKUP:
            adaptation_lines.append(
                "Person ID lookup: load ETL_OUTPUT_DIR/person.csv and build a dict {person_source_value: person_id}."
            )
        if table in _NEEDS_VISIT_LOOKUP:
            source_stem = Path(project.source_filename).stem if project.source_filename else "basedata"
            adaptation_lines += [
                "Visit occurrence ID lookup: load ETL_OUTPUT_DIR/visit_occurrence.csv (semicolon-delimited)",
                "  and build a dict keyed by visit_source_value: {row['visit_source_value']: row['visit_occurrence_id']}.",
                f"The visit_source_value key format is: '{{person_source_value}}-{source_stem}-{{visit_label_normalized}}'",
                "  where visit_label_normalized = visit label lowercased with spaces replaced by underscores.",
            ]
        adaptation_lines += [
            "",
            "Output: write semicolon-delimited (;) UTF-8 CSV to ETL_OUTPUT_DIR/{table}.csv".format(table=table),
            "Print progress: e.g. 'Writing {table}.csv ... done (N records)'".format(table=table),
            "Guard: include `if __name__ == '__main__': main()`",
            "",
        ]
        lines += adaptation_lines

    # ── visit_occurrence: visit_source_value auto-compute rule ───────────
    if table == "visit_occurrence":
        source_stem = Path(project.source_filename).stem if project.source_filename else "basedata"
        lines += [
            "## VISIT_SOURCE_VALUE — AUTO-COMPUTED",
            f"VISIT_SOURCE_VALUE_FILENAME_STEM = '{source_stem}'",
            "visit_source_value must be built at runtime as:",
            f"  f\"{{person_source_value}}-{source_stem}-{{visit_label_normalized}}\"",
            "where visit_label_normalized = visit label from config, lowercased, spaces → underscores.",
            "Do NOT read visit_source_value from the config's source_value field — ignore it.",
            "record_source_value must equal visit_source_value for every row.",
            "",
        ]

    # ── Source dataset ────────────────────────────────────────────────────
    lines += [
        "## SOURCE DATASET",
        f"  Filename  : {project.source_filename}",
        f"  Delimiter : {repr(project.source_delimiter or ',')}",
        f"  Encoding  : {project.source_encoding or 'utf-8'}",
        f"  Columns   : {project.source_columns}",
        f"  Row count : {project.source_row_count}",
        "",
    ]

    # ── Table-specific config ─────────────────────────────────────────────
    # Strip legacy scalar fields that were replaced by per-value maps
    config_for_prompt = dict(config)
    if table == "provider":
        config_for_prompt.pop("gender_concept_id", None)
        config_for_prompt.pop("specialty_concept_id", None)

    lines += [
        f"## USER CONFIGURATION FOR {table.upper()}",
        "```json",
        json.dumps(config_for_prompt, indent=2),
        "```",
        "",
    ]

    # ── Person ID mode / transform note ──────────────────────────────────
    if table == "person":
        person_id_cfg = config.get("mappings", {}).get("person_id", {})
        if person_id_cfg.get("auto_increment"):
            lines += [
                "## PERSON ID — AUTO-INCREMENT MODE",
                "The user has enabled auto-increment for person_id.",
                "IMPORTANT: Do NOT read person_id from any source column.",
                "Assign sequential integers starting from 1 for each output row (e.g. use enumerate).",
                "Set person_source_value to the string representation of that sequential integer.",
                "",
            ]
        else:
            _transform_map = {
                "int_float": "int(float(value))",
                "int": "int(value)",
                "str": "str(value)",
            }
            transform = person_id_cfg.get("transform", "int_float")
            transform_expr = _transform_map.get(transform, "int(float(value))")
            lines += [
                "## PERSON ID — TRANSFORM",
                f"The user has selected person_id transform: `{transform}`.",
                f"Cast the source person_id column using exactly: `{transform_expr}`",
                "Do NOT use a different cast expression — respect the user's choice.",
                "",
            ]

        dob_cfg = config.get("mappings", {}).get("year_of_birth", {})
        date_format = dob_cfg.get("date_format", "%Y-%m-%d")
        lines += [
            "## DATE OF BIRTH — FORMAT",
            f"The user has configured date_format: `{date_format}`",
            f"Parse the date of birth column using exactly: `datetime.strptime(value, '{date_format}')`",
            "Do NOT use a different format string — respect the user's choice.",
            "",
        ]

    # ── Concept decisions summary (relevant to this table) ───────────────
    if concept_decisions:
        lines += [
            "## CONCEPT MAPPING DECISIONS (from UI)",
            "These are the user's per-variable mapping decisions. Use them to understand",
            "which variables are clinical (map_variable / map_values / map_both) vs",
            "administrative (skip), and what concept IDs have been pre-assigned.",
            "```json",
            json.dumps(concept_decisions, indent=2),
            "```",
            "",
        ]

    # ── Hint (if reference was shown, this is additional guidance) ───────
    if reference and hint:
        lines += [
            "## ADDITIONAL OMOP FIELD GUIDANCE",
            hint,
            "",
        ]

    # ── Provider prefix specialty ─────────────────────────────────────────
    if table == "provider":
        prov_cfg_ps: dict = (project.etl_config or {}).get("provider", {})
        prefix_specialty = prov_cfg_ps.get("prefix_specialty", "")
        prefix_specialty_concept_id = prov_cfg_ps.get("prefix_specialty_concept_id")
        if prefix_specialty or prefix_specialty_concept_id:
            lines += ["## PREFIX SPECIALTY — STATIC DEFAULT"]
            if prefix_specialty:
                lines.append(
                    f"When specialty_source_value cannot be derived from a source column "
                    f"(column not configured or value is blank/null), use the static string "
                    f"'{prefix_specialty}' as specialty_source_value."
                )
            if prefix_specialty_concept_id:
                lines.append(
                    f"When specialty_concept_id cannot be resolved from the per-value map, "
                    f"fall back to the static concept ID {prefix_specialty_concept_id}."
                )
            lines += ["", ""]

    # ── Provider composite source value ──────────────────────────────────
    if table == "provider":
        prov_cfg: dict = (project.etl_config or {}).get("provider", {})
        prov_name_col = prov_cfg.get("provider_name_col", "")
        if prov_name_col:
            lines += [
                "## PROVIDER_SOURCE_VALUE — AUTO-COMPUTED",
                "Build provider_source_value as: str(care_site_id) + ' | ' + str(row['" + prov_name_col + "'])",
                "where care_site_id is the resolved OMOP care_site_id (use the string 'None' if not found).",
                "IMPORTANT: cast every value to str() before joining.",
                "Truncate to 50 chars. Use this composite value as the deduplication key.",
                "",
            ]
        care_site_config: dict = (project.etl_config or {}).get("care_site", {})
        if care_site_config:
            lines += [
                "## CARE SITE CONFIG (for care_site_id lookup)",
                "Use care_site_name_col from this config to match against care_site_name in ETL_OUTPUT_DIR/care_site.csv.",
                "Build dict: {str(row['care_site_name']): int(row['care_site_id'])} and look up each provider row.",
                "If no match, file absent, or care_site_name_col not configured, set care_site_id to None.",
                "```json",
                json.dumps(care_site_config, indent=2),
                "```",
                "",
            ]

    # ── Care site composite source value ─────────────────────────────────
    if table == "care_site":
        cs_cfg: dict = (project.etl_config or {}).get("care_site", {})
        loc_cfg: dict = (project.etl_config or {}).get("location", {})
        name_col = cs_cfg.get("care_site_name_col", "")
        cs_addr_cols = [
            col for key in ("cs_address_1_col", "cs_address_2_col", "cs_city_col",
                            "cs_state_col", "cs_zip_col", "cs_county_col")
            if (col := loc_cfg.get(key, ""))
        ]
        cs_country = loc_cfg.get("cs_country_source_value", "")
        if name_col:
            lines += [
                "## CARE_SITE_SOURCE_VALUE — COMPOSITE KEY",
                "Build care_site_source_value as: str(location_id) + ' | ' + str(row['" + name_col + "'])",
                "where location_id is the OMOP location_id looked up from ETL_OUTPUT_DIR/location.csv",
                "using the cs_location_source_value for that row (computed from the cs_* address columns).",
                "  IMPORTANT: cast every column value to str() before joining — columns like zip may be integers.",
                f"  Address columns used to compute cs_location_source_value: {cs_addr_cols}" + (f" + static country '{cs_country}'" if cs_country else ""),
                "Use this composite value as the deduplication key (max 50 chars).",
                "",
            ]

    # ── Location config (injected into care_site and person for location_id lookup) ──
    if table in ("care_site", "person"):
        location_config: dict = (project.etl_config or {}).get("location", {})
        if location_config:
            lines += [
                "## LOCATION CONFIG (for location_id lookup)",
                "Use the address columns below to compute location_source_value per row",
                "and look up location_id from ETL_OUTPUT_DIR/location.csv.",
                "IMPORTANT: cast every column value to str() before joining — columns like zip may be integers.",
                "```json",
                json.dumps(location_config, indent=2),
                "```",
                "",
            ]

    if table == "person":
        care_site_config: dict = (project.etl_config or {}).get("care_site", {})
        if care_site_config:
            lines += [
                "## CARE SITE CONFIG (for care_site_id lookup)",
                "Load ETL_OUTPUT_DIR/care_site.csv and build a dict {care_site_source_value: care_site_id}.",
                "care_site_source_value in that file has the format: '<location_id> | <care_site_name>'",
                "To look up care_site_id for a person row: compute cs_location_source_value from the cs_* address",
                "columns, resolve location_id from location.csv, then reconstruct the key as",
                "  str(location_id) + ' | ' + str(row[care_site_name_col])",
                "```json",
                json.dumps(care_site_config, indent=2),
                "```",
                "",
            ]

        provider_config: dict = (project.etl_config or {}).get("provider", {})
        if provider_config:
            lines += [
                "## PROVIDER CONFIG (for provider_id lookup)",
                "Use the column below to look up provider_id from ETL_OUTPUT_DIR/provider.csv.",
                "```json",
                json.dumps(provider_config, indent=2),
                "```",
                "",
            ]

    # ── Extra user instructions ───────────────────────────────────────────
    if extra:
        lines += [
            "## EXTRA INSTRUCTIONS FROM USER",
            extra,
            "",
        ]

    lines += [
        "## OUTPUT",
        "Output ONLY the Python script. No markdown fences. No explanations outside the code.",
        "The script must be completely runnable as: python script.py",
    ]

    return "\n".join(lines)


def _xtr(var: str, col: str, indent: int = 8) -> str:
    """Return a line of generated Python that safely extracts a source column into a local var."""
    pad = " " * indent
    if not col:
        return f"{pad}{var} = None"
    c = repr(col)
    return f"{pad}{var} = (str(row.get({c}, '')).strip() or None) if pd.notnull(row.get({c})) else None"


def _generate_location_script(project) -> str:
    """Deterministic template-based generator for the OMOP location script."""
    loc = (project.etl_config or {}).get("location", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    a1_col = loc.get("address_1_col", "")
    a2_col = loc.get("address_2_col", "")
    city_col = loc.get("city_col", "")
    state_col = loc.get("state_col", "")
    zip_col = loc.get("zip_col", "")
    county_col = loc.get("county_col", "")
    country_sv = loc.get("country_source_value", "")
    lat_col = loc.get("latitude_col", "")
    lon_col = loc.get("longitude_col", "")


    cs_a1_col = loc.get("cs_address_1_col", "")
    cs_a2_col = loc.get("cs_address_2_col", "")
    cs_city_col = loc.get("cs_city_col", "")
    cs_state_col = loc.get("cs_state_col", "")
    cs_zip_col = loc.get("cs_zip_col", "")
    cs_county_col = loc.get("cs_county_col", "")
    cs_country_sv = loc.get("cs_country_source_value", "")
    cs_lat_col = loc.get("cs_latitude_col", "")
    cs_lon_col = loc.get("cs_longitude_col", "")

    cid_map = json.dumps(loc.get("country_concept_id_map", {}))
    cid_default = loc.get("country_concept_id_default", 0)
    cs_cid_map = json.dumps(loc.get("cs_country_concept_id_map", {}))
    cs_cid_default = loc.get("cs_country_concept_id_default", 0)

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "def _to_lat(val):\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -90 <= f <= 90:\n"
        "            return f\n"
        "        print(f'WARNING: latitude value {f} is out of range [-90, 90] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "def _to_lon(val):\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -180 <= f <= 180:\n"
        "            return f\n"
        "        print(f'WARNING: longitude value {f} is out of range [-180, 180] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "def main():\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    person_config = {\n"
        f'        "address_1_col": {repr(a1_col)},\n'
        f'        "address_2_col": {repr(a2_col)},\n'
        f'        "city_col": {repr(city_col)},\n'
        f'        "state_col": {repr(state_col)},\n'
        f'        "zip_col": {repr(zip_col)},\n'
        f'        "county_col": {repr(county_col)},\n'
        f'        "country_source_value": {repr(country_sv)},\n'
        f'        "latitude_col": {repr(lat_col)},\n'
        f'        "longitude_col": {repr(lon_col)},\n'
        "    }\n"
        "\n"
        "    care_site_config = {\n"
        f'        "address_1_col": {repr(cs_a1_col)},\n'
        f'        "address_2_col": {repr(cs_a2_col)},\n'
        f'        "city_col": {repr(cs_city_col)},\n'
        f'        "state_col": {repr(cs_state_col)},\n'
        f'        "zip_col": {repr(cs_zip_col)},\n'
        f'        "county_col": {repr(cs_county_col)},\n'
        f'        "country_source_value": {repr(cs_country_sv)},\n'
        f'        "latitude_col": {repr(cs_lat_col)},\n'
        f'        "longitude_col": {repr(cs_lon_col)},\n'
        "    }\n"
        "\n"
        f"    country_concept_id_map = {cid_map}\n"
        f"    country_concept_id_default = {cid_default}\n"
        f"    cs_country_concept_id_map = {cs_cid_map}\n"
        f"    cs_country_concept_id_default = {cs_cid_default}\n"
        "\n"
        "    rows = []\n"
        "\n"
        "    for _, row in df.iterrows():\n"
        + _xtr("address_1", a1_col) + "\n"
        + _xtr("address_2", a2_col) + "\n"
        + _xtr("city", city_col) + "\n"
        + _xtr("state", state_col) + "\n"
        + _xtr("zip_code", zip_col) + "\n"
        + _xtr("county", county_col) + "\n"
        + _xtr("latitude", lat_col) + "\n"
        + _xtr("longitude", lon_col) + "\n"
        + '        country_source_value = person_config["country_source_value"]\n'
        + "\n"
        + '        location_source_value = " | ".join(filter(None, [address_1, address_2, city, state, zip_code, county, country_source_value]))[:255]\n'
        + "        country_concept_id = country_concept_id_map.get(county, country_concept_id_default)\n"
        + "        if country_concept_id == 0:\n"
        + "            country_concept_id = None\n"
        + "\n"
        + "        if any([address_1, city, state, zip_code]):\n"
        + "            rows.append([\n"
        + "                None,\n"
        + "                address_1[:50] if address_1 else None,\n"
        + "                address_2[:50] if address_2 else None,\n"
        + "                city[:50] if city else None,\n"
        + "                state[:2] if state else None,\n"
        + "                zip_code if zip_code else None,\n"
        + "                county[:20] if county else None,\n"
        + "                location_source_value,\n"
        + "                country_concept_id,\n"
        + "                country_source_value,\n"
        + "                _to_lat(latitude),\n"
        + "                _to_lon(longitude),\n"
        + "            ])\n"
        + "\n"
        + _xtr("cs_address_1", cs_a1_col) + "\n"
        + _xtr("cs_address_2", cs_a2_col) + "\n"
        + _xtr("cs_city", cs_city_col) + "\n"
        + _xtr("cs_state", cs_state_col) + "\n"
        + _xtr("cs_zip_code", cs_zip_col) + "\n"
        + _xtr("cs_county", cs_county_col) + "\n"
        + '        cs_country_source_value = care_site_config["country_source_value"]\n'
        + _xtr("cs_latitude", cs_lat_col) + "\n"
        + _xtr("cs_longitude", cs_lon_col) + "\n"
        + "\n"
        + '        cs_location_source_value = " | ".join(filter(None, [cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value]))[:255]\n'
        + "        cs_country_concept_id = cs_country_concept_id_map.get(cs_county, cs_country_concept_id_default)\n"
        + "        if cs_country_concept_id == 0:\n"
        + "            cs_country_concept_id = None\n"
        + "\n"
        + "        if any([cs_address_1, cs_city, cs_state, cs_zip_code]):\n"
        + "            rows.append([\n"
        + "                None,\n"
        + "                cs_address_1[:50] if cs_address_1 else None,\n"
        + "                cs_address_2[:50] if cs_address_2 else None,\n"
        + "                cs_city[:50] if cs_city else None,\n"
        + "                cs_state[:2] if cs_state else None,\n"
        + "                cs_zip_code if cs_zip_code else None,\n"
        + "                cs_county[:20] if cs_county else None,\n"
        + "                cs_location_source_value,\n"
        + "                cs_country_concept_id,\n"
        + "                cs_country_source_value,\n"
        + "                _to_lat(cs_latitude),\n"
        + "                _to_lon(cs_longitude),\n"
        + "            ])\n"
        + "\n"
        + "    output_df = pd.DataFrame(rows, columns=[\n"
        + '        "location_id", "address_1", "address_2", "city", "state", "zip", "county",\n'
        + '        "location_source_value", "country_concept_id", "country_source_value", "latitude", "longitude"\n'
        + "    ])\n"
        + "\n"
        + '    output_df = output_df.drop_duplicates(subset=["location_source_value"], keep="first")\n'
        + '    output_df["location_id"] = range(1, len(output_df) + 1)\n'
        + "\n"
        + "    output_file = os.path.join(output_dir, 'location.csv')\n"
        + "    output_df.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing location.csv ... done ({len(output_df)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _generate_care_site_script(project) -> str:
    """Deterministic template-based generator for the OMOP care_site script."""
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    loc = (project.etl_config or {}).get("location", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    name_col = cs_cfg.get("care_site_name_col", "")
    pos_col = cs_cfg.get("place_of_service_col", "")
    pos_value_map = cs_cfg.get("place_of_service_value_map", {})

    cs_a1_col = loc.get("cs_address_1_col", "")
    cs_a2_col = loc.get("cs_address_2_col", "")
    cs_city_col = loc.get("cs_city_col", "")
    cs_state_col = loc.get("cs_state_col", "")
    cs_zip_col = loc.get("cs_zip_col", "")
    cs_county_col = loc.get("cs_county_col", "")
    cs_country_sv = loc.get("cs_country_source_value", "")

    has_location = any([cs_a1_col, cs_a2_col, cs_city_col, cs_state_col, cs_zip_col, cs_county_col, cs_country_sv])

    location_block = (
        "    location_lookup = {}\n"
        "    location_file = os.path.join(output_dir, 'location.csv')\n"
        "    if os.path.exists(location_file):\n"
        "        try:\n"
        "            loc_df = pd.read_csv(location_file, delimiter=';', encoding='utf-8')\n"
        "            location_lookup = dict(zip(loc_df['location_source_value'], loc_df['location_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load location.csv: {e}')\n"
        if has_location else
        "    location_lookup = {}\n"
    )

    cs_extractions = (
        _xtr("cs_address_1", cs_a1_col, 12) + "\n"
        + _xtr("cs_address_2", cs_a2_col, 12) + "\n"
        + _xtr("cs_city", cs_city_col, 12) + "\n"
        + _xtr("cs_state", cs_state_col, 12) + "\n"
        + _xtr("cs_zip_code", cs_zip_col, 12) + "\n"
        + _xtr("cs_county", cs_county_col, 12) + "\n"
        + f"            cs_country_source_value = {repr(cs_country_sv)}\n"
        + "            cs_location_source_value = ' | '.join(filter(None, [cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value]))[:255]\n"
        + "            location_id = location_lookup.get(cs_location_source_value)\n"
        if has_location else
        "            location_id = None\n"
    )

    name_extraction = (
        f"            care_site_name = (str(row.get({repr(name_col)}, '')).strip() or None) if pd.notnull(row.get({repr(name_col)})) else None\n"
        if name_col else
        "            care_site_name = None\n"
    )

    pos_sv_extraction = (
        f"            pos_source_value = (str(row.get({repr(pos_col)}, '')).strip()[:50] or None) if pd.notnull(row.get({repr(pos_col)})) else None\n"
        f"            place_of_service_concept_id = pos_value_map.get(pos_source_value)\n"
        if pos_col else
        "            pos_source_value = None\n"
        "            place_of_service_concept_id = None\n"
    )

    dedup_block = (
        "    if not df_out.empty:\n"
        "        df_out['_name_norm'] = df_out['care_site_name'].str.strip().str.lower()\n"
        "        df_out = df_out.drop_duplicates(subset=['_name_norm'], keep='first')\n"
        "        df_out = df_out.drop(columns=['_name_norm'])\n"
        if name_col else
        ""
    )

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "def main():\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        f"    care_site_name_col = {repr(name_col)}\n"
        f"    pos_value_map = {json.dumps(pos_value_map)}\n"
        "\n"
        + location_block
        + "\n"
        "    rows = []\n"
        "    for _, row in df.iterrows():\n"
        "        try:\n"
        + cs_extractions
        + name_extraction
        + "            care_site_source_value = (str(location_id) + ' | ' + care_site_name)[:50] if care_site_name else None\n"
        + pos_sv_extraction
        + "            rows.append({\n"
        + "                'care_site_name': care_site_name[:255] if care_site_name else None,\n"
        + "                'place_of_service_concept_id': place_of_service_concept_id,\n"
        + "                'location_id': location_id,\n"
        + "                'care_site_source_value': care_site_source_value,\n"
        + "                'place_of_service_source_value': pos_source_value[:50] if pos_source_value else None,\n"
        + "            })\n"
        + "        except Exception as e:\n"
        + "            print(f'WARNING: skipping row — {e}')\n"
        + "\n"
        + "    df_out = pd.DataFrame(rows)\n"
        + dedup_block
        + "    df_out = df_out.reset_index(drop=True)\n"
        + "    df_out['care_site_id'] = df_out.index + 1\n"
        + "\n"
        + "    df_out = df_out[['care_site_id', 'care_site_name', 'place_of_service_concept_id',\n"
        + "                     'location_id', 'care_site_source_value', 'place_of_service_source_value']]\n"
        + "\n"
        + "    output_file = os.path.join(output_dir, 'care_site.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing care_site.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _generate_provider_script(project) -> str:
    """Deterministic template-based generator for the OMOP provider script."""
    prov = (project.etl_config or {}).get("provider", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    name_col = prov.get("provider_name_col", "")
    npi_col = prov.get("npi_col", "")
    dea_col = prov.get("dea_col", "")
    yob_col = prov.get("year_of_birth_col", "")
    specialty_col = prov.get("specialty_source_value_col", "")
    specialty_map = prov.get("specialty_concept_value_map", {})
    prefix_specialty = prov.get("prefix_specialty", "") or ""
    prefix_specialty_cid = prov.get("prefix_specialty_concept_id")
    gender_col = prov.get("gender_source_value_col", "")
    gender_map = prov.get("gender_concept_value_map", {})
    gender_default = prov.get("gender_concept_id_default", 0) or 0
    cs_name_col = cs_cfg.get("care_site_name_col", "")

    if cs_name_col:
        care_site_block = (
            "    care_site_lookup = {}\n"
            "    care_site_file = os.path.join(output_dir, 'care_site.csv')\n"
            "    if os.path.exists(care_site_file):\n"
            "        try:\n"
            "            cs_df = pd.read_csv(care_site_file, delimiter=';', encoding='utf-8')\n"
            "            care_site_lookup = {str(r['care_site_name']): int(r['care_site_id']) for _, r in cs_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load care_site.csv: {e}')\n"
        )
        cs_lookup_line = (
            f"            raw_cs_name = (str(row.get({repr(cs_name_col)}, '')).strip() or None) if pd.notnull(row.get({repr(cs_name_col)})) else None\n"
            "            care_site_id = care_site_lookup.get(raw_cs_name) if raw_cs_name else None\n"
        )
    else:
        care_site_block = "    care_site_lookup = {}\n"
        cs_lookup_line = "            care_site_id = None\n"

    if specialty_col:
        specialty_lines = (
            f"            specialty_source_value = (str(row.get({repr(specialty_col)}, '')).strip()[:50] or None) if pd.notnull(row.get({repr(specialty_col)})) else None\n"
            "            specialty_concept_id = specialty_map.get(specialty_source_value, 0) if specialty_source_value else 0\n"
        )
    elif prefix_specialty:
        _sv = prefix_specialty[:50]
        _cid = prefix_specialty_cid if prefix_specialty_cid is not None else 0
        specialty_lines = (
            f"            specialty_source_value = {repr(_sv)}\n"
            f"            specialty_concept_id = {_cid}\n"
        )
    else:
        specialty_lines = (
            "            specialty_source_value = None\n"
            "            specialty_concept_id = 0\n"
        )

    if gender_col:
        gender_lines = (
            f"            gender_source_value = (str(row.get({repr(gender_col)}, '')).strip()[:50] or None) if pd.notnull(row.get({repr(gender_col)})) else None\n"
            "            gender_concept_id = gender_map.get(gender_source_value, gender_default) if gender_source_value else gender_default\n"
        )
    else:
        gender_lines = (
            "            gender_source_value = None\n"
            "            gender_concept_id = gender_default\n"
        )

    name_line = (
        f"            provider_name = (str(row.get({repr(name_col)}, '')).strip()[:255] or None) if pd.notnull(row.get({repr(name_col)})) else None\n"
        if name_col else
        "            provider_name = None\n"
    )
    npi_line = (
        f"            npi = (str(row.get({repr(npi_col)}, '')).strip()[:20] or None) if pd.notnull(row.get({repr(npi_col)})) else None\n"
        if npi_col else
        "            npi = None\n"
    )
    dea_line = (
        f"            dea = (str(row.get({repr(dea_col)}, '')).strip()[:20] or None) if pd.notnull(row.get({repr(dea_col)})) else None\n"
        if dea_col else
        "            dea = None\n"
    )
    yob_lines = (
        f"            _yob_raw = row.get({repr(yob_col)})\n"
        "            try:\n"
        "                year_of_birth = int(_yob_raw) if pd.notnull(_yob_raw) else None\n"
        "            except (TypeError, ValueError):\n"
        "                year_of_birth = None\n"
        if yob_col else
        "            year_of_birth = None\n"
    )

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "def main():\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        f"    specialty_map = {json.dumps(specialty_map)}\n"
        f"    gender_map = {json.dumps(gender_map)}\n"
        f"    gender_default = {gender_default}\n"
        "\n"
        + care_site_block
        + "\n"
        "    rows = []\n"
        "    seen = set()\n"
        "    for _, row in df.iterrows():\n"
        "        try:\n"
        + name_line
        + npi_line
        + dea_line
        + yob_lines
        + specialty_lines
        + cs_lookup_line
        + gender_lines
        + "            provider_source_value = (str(care_site_id) + ' | ' + (provider_name or ''))[:50]\n"
        + "            if provider_source_value in seen:\n"
        + "                continue\n"
        + "            seen.add(provider_source_value)\n"
        + "            rows.append({\n"
        + "                'provider_id': None,\n"
        + "                'provider_name': provider_name,\n"
        + "                'npi': npi,\n"
        + "                'dea': dea,\n"
        + "                'specialty_concept_id': specialty_concept_id,\n"
        + "                'care_site_id': care_site_id,\n"
        + "                'year_of_birth': year_of_birth,\n"
        + "                'gender_concept_id': gender_concept_id,\n"
        + "                'provider_source_value': provider_source_value,\n"
        + "                'specialty_source_value': specialty_source_value,\n"
        + "                'specialty_source_concept_id': 0,\n"
        + "                'gender_source_value': gender_source_value,\n"
        + "                'gender_source_concept_id': 0,\n"
        + "            })\n"
        + "        except Exception as e:\n"
        + "            print(f'WARNING: skipping row — {e}')\n"
        + "\n"
        + "    df_out = pd.DataFrame(rows)\n"
        + "    df_out['provider_id'] = range(1, len(df_out) + 1)\n"
        + "\n"
        + "    df_out = df_out[['provider_id', 'provider_name', 'npi', 'dea', 'specialty_concept_id',\n"
        + "                     'care_site_id', 'year_of_birth', 'gender_concept_id', 'provider_source_value',\n"
        + "                     'specialty_source_value', 'specialty_source_concept_id',\n"
        + "                     'gender_source_value', 'gender_source_concept_id']]\n"
        + "\n"
        + "    output_file = os.path.join(output_dir, 'provider.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing provider.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


async def generate_table_script(project, table: str) -> str:
    """Generate the Python ETL script for a single OMOP table."""
    if table == "location":
        return _generate_location_script(project)
    if table == "care_site":
        return _generate_care_site_script(project)
    if table == "provider":
        return _generate_provider_script(project)

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    system = _system_prompt()
    user = _build_table_prompt(project, table)

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.15,
        max_tokens=8192,
    )

    content = response.choices[0].message.content or ""
    return _strip_fences(content)


async def generate_all_table_scripts(project) -> dict[str, str]:
    """Generate scripts for all tables configured in etl_config. Returns {table: code}."""
    import asyncio

    config: dict = project.etl_config or {}
    tables = [t for t in SUPPORTED_TABLES if t in config]

    # Always include domain tables when stem_table is configured (they depend on it)
    if "stem_table" in config:
        for dt in _DOMAIN_TABLES:
            if dt not in tables:
                tables.append(dt)

    if not tables:
        tables = list(SUPPORTED_TABLES)

    tasks = {t: generate_table_script(project, t) for t in tables}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    out: dict[str, str] = {}
    for table, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            out[table] = f"# ERROR generating {table}: {result}"
        else:
            out[table] = result  # type: ignore[assignment]

    return out


def _strip_fences(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    inner: list[str] = []
    in_block = False
    for line in lines:
        if line.startswith("```") and not in_block:
            in_block = True
            continue
        if line.startswith("```") and in_block:
            in_block = False
            continue
        if in_block:
            inner.append(line)
    return "\n".join(inner)
