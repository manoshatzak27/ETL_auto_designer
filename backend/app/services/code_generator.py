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
]

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
            "- Per-row loop pattern with explicit try/except per row",
            "- Logging setup (logging.basicConfig + module-level logger)",
            "- Variable naming conventions",
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

    _CONCEPT_MAPPING_TABLES = {"stem_table", "observation_period", "visit_occurrence", "death"}

    # ── Standalone adapter instructions ──────────────────────────────────
    env_vars = [
        "  - ETL_SOURCE_PATH   → path to the source CSV file",
        "  - ETL_OUTPUT_DIR    → output directory for OMOP CSVs",
    ]
    if table in _CONCEPT_MAPPING_TABLES:
        env_vars += [
            "  - ETL_MAPPING_FILES → JSON string: {name: filepath} for the 3 concept mapping CSVs",
            "    • variable_mapping.csv  (variable_source_code → concept_id)",
            "    • value_mapping.csv     (variable_source_code, value_source_code → value_as_concept_id)",
            "    • variable_value_mapping.csv  (variable_source_code, value_source_code → concept_id)",
        ]

    lines += [
        "## ADAPTATION RULES",
        "The reference uses a `wrapper` object. Your script must NOT use it.",
        "Instead, read data from files using these environment variables:",
        *env_vars,
        "",
        "Person ID lookup: load ETL_OUTPUT_DIR/person.csv and build a dict {person_source_value: person_id}.",
        "Visit occurrence ID lookup (needed by stem_table and death): load ETL_OUTPUT_DIR/visit_occurrence.csv",
        "  and build a dict {record_source_value: visit_occurrence_id}.",
        "The record_source_value key format for visits is: '{person_source_value}-basedata-{visit_type}'",
        "",
        "Output: write semicolon-delimited (;) UTF-8 CSV to ETL_OUTPUT_DIR/{table}.csv".format(table=table),
        "Print progress: e.g. 'Writing {table}.csv ... done (N records)'".format(table=table),
        "Guard: include `if __name__ == '__main__': main()`",
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
    lines += [
        f"## USER CONFIGURATION FOR {table.upper()}",
        "```json",
        json.dumps(config, indent=2),
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

    # ── Location config (injected into care_site and person for location_id lookup) ──
    if table in ("care_site", "person"):
        location_config: dict = (project.etl_config or {}).get("location", {})
        if location_config:
            lines += [
                "## LOCATION CONFIG (for location_id lookup)",
                "Use the address columns below to compute location_source_value per row",
                "and look up location_id from ETL_OUTPUT_DIR/location.csv.",
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
                "Use the column below to look up care_site_id from ETL_OUTPUT_DIR/care_site.csv.",
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


async def generate_table_script(project, table: str) -> str:
    """Generate the Python ETL script for a single OMOP table."""
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

    if not tables:
        tables = SUPPORTED_TABLES

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
