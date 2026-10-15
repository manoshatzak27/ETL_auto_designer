"""
AI chat service for discussing and modifying generated ETL scripts.

The AI receives the full current script as context and can answer questions
or return an updated version inside a ```python ... ``` fence.
"""
import json
import re
from openai import AsyncOpenAI
from app.config import settings

_CODE_FENCE_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)

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

# Tables whose config is needed as upstream context when discussing a given table
_TABLE_DEPS: dict[str, list[str]] = {
    "location": [],
    "care_site": ["location"],
    "provider": ["care_site"],
    "person": ["location", "care_site", "provider"],
    "visit_occurrence": ["person", "care_site", "provider"],
    "observation_period": ["person"],
    "stem_table": ["person", "visit_occurrence"],
    "death": ["person"],
    "measurement": ["person", "visit_occurrence", "stem_table"],
    "observation": ["person", "visit_occurrence", "stem_table"],
    "drug_exposure": ["person", "visit_occurrence", "stem_table"],
    "procedure_occurrence": ["person", "visit_occurrence", "stem_table"],
    "condition_occurrence": ["person", "visit_occurrence", "stem_table"],
}


def _extract_code(text: str) -> str | None:
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def _build_system_prompt(project, table: str) -> str:
    code = (project.generated_scripts or {}).get(table, "")
    etl_config: dict = project.etl_config or {}
    concept_decisions: dict = project.concept_decisions or {}

    col_list = "\n".join(f"  - {c}" for c in (project.source_columns or []))

    lines = [
        "You are an expert OMOP CDM v5.4 ETL engineer helping a user review and modify generated Python ETL scripts.",
        "",
        "## Source dataset",
        f"- File: {project.source_filename}",
        f"- Delimiter: {project.source_delimiter!r}  Encoding: {project.source_encoding!r}",
        f"- Row count: {project.source_row_count}",
        "- Exact column names present in the source file:",
        col_list,
        "",
        f"## Currently discussing: {table}.py",
        "",
        "You can:",
        "1. Explain what the code does and answer questions about specific sections.",
        "2. Make code modifications when asked.",
        "3. Point out potential bugs or improvements.",
        "",
        "IMPORTANT — code generation policy:",
        "- Do NOT return a Python code block unless the user has explicitly asked for a code change",
        "  or has answered yes/confirmed after you asked them.",
        "- When you find bugs or improvements, describe them in plain text first.",
        "- End your analysis with: 'Would you like me to proceed with the code correction?'",
        "- Only after the user confirms should you return the updated script.",
        "",
        "IMPORTANT — when making code changes:",
        "- Return the COMPLETE updated Python script inside a single ```python ... ``` block.",
        "- After the block, briefly explain what you changed and why.",
        "- The code must be standalone Python using only pandas, numpy, and the standard library.",
        "- Environment variables used by the script:",
        "    ETL_SOURCE_PATH   — path to the source CSV",
        "    ETL_OUTPUT_DIR    — directory where OMOP output CSVs are written",
        "    ETL_MAPPING_<name> — path to each concept mapping CSV (variable_mapping, value_mapping, variable_value_mapping, unit_mapping)",
        "      • variable_mapping.csv  (variable_source_code → target_concept_id, domain_id)",
        "      • value_mapping.csv     (variable_source_code, value_source_code → target_concept_id)",
        "      • variable_value_mapping.csv  (variable_source_code, value_source_code → target_concept_id, domain_id)",
        "      • unit_mapping.csv      (variable_source_code → unit_concept_id)",
        f"- Output: write semicolon-delimited (;) UTF-8 CSV to ETL_OUTPUT_DIR/{table}.csv",
        "- Always include: if __name__ == '__main__': main()",
        "",
    ]

    # ── Table-specific ETL config ─────────────────────────────────────────────
    table_cfg = etl_config.get(table)
    if table_cfg:
        # Strip UI-only persistence keys to keep the prompt concise
        _ui_keys = {"enabled", "visit_labels"}
        clean_cfg = {k: v for k, v in table_cfg.items() if k not in _ui_keys}
        if clean_cfg:
            lines += [
                f"## ETL configuration for {table}.py",
                "This is the field-mapping config that was used to generate the script.",
                "Use it to understand which source columns map to which OMOP fields.",
                "```json",
                json.dumps(clean_cfg, indent=2, default=str),
                "```",
                "",
            ]

    # ── Upstream table configs (cross-table lookups) ──────────────────────────
    dep_configs: list[tuple[str, dict]] = []
    for dep in _TABLE_DEPS.get(table, []):
        dep_cfg = etl_config.get(dep)
        if dep_cfg:
            _ui_keys = {"enabled", "visit_labels"}
            clean = {k: v for k, v in dep_cfg.items() if k not in _ui_keys}
            if clean:
                dep_configs.append((dep, clean))

    if dep_configs:
        lines += [
            "## Related table configs (upstream dependencies)",
            "These tables are read by the script above via CSV lookups.",
            "",
        ]
        for dep_name, dep_cfg in dep_configs:
            lines += [
                f"### {dep_name}.py config",
                "```json",
                json.dumps(dep_cfg, indent=2, default=str),
                "```",
                "",
            ]

    # ── Concept decisions (variable-level concept mapping from Concepts step) ──
    if concept_decisions:
        lines += [
            "## Concept mapping decisions (Concepts step)",
            "These are the per-variable concept mapping decisions made by the user.",
            "Each key is a source variable name; the value describes the mapping strategy",
            "and the selected OMOP concept IDs.",
            "```json",
            json.dumps(concept_decisions, indent=2, default=str),
            "```",
            "",
        ]

    # ── Extra instructions the user wrote during codegen ─────────────────────
    extra = (table_cfg or {}).get("extra_instructions", "").strip()
    if extra:
        lines += [
            "## Extra instructions (entered by user during code generation)",
            extra,
            "",
        ]

    # ── Current script ────────────────────────────────────────────────────────
    if code:
        lines += [
            f"## Current {table}.py",
            "```python",
            code,
            "```",
        ]
    else:
        lines.append(f"Note: {table}.py has not been generated yet.")

    return "\n".join(lines)


async def chat(
    project,
    table: str,
    history: list[dict],
    user_message: str,
) -> dict:
    """
    Send a user message and get an AI response with optional code update.

    Returns:
        response      — AI text
        code_updated  — True if the AI returned updated code
        updated_code  — the new script, or None
    """
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    system = _build_system_prompt(project, table)

    # Build OpenAI message list: system + previous turns + new user message
    messages: list[dict] = [{"role": "system", "content": system}]
    for msg in history:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.3,
        max_tokens=16000,
    )

    choice = response.choices[0]
    content = choice.message.content or ""
    updated_code = _extract_code(content)

    # A long script can get cut off before the closing ``` fence, in which case
    # no code is extracted — tell the user instead of silently dropping the update.
    if updated_code is None and choice.finish_reason == "length":
        content += (
            "\n\n⚠️ This response was cut off before the code block finished, "
            "so no update could be applied. Try asking again, or request the "
            "change in smaller steps."
        )

    return {
        "response": content,
        "code_updated": updated_code is not None,
        "updated_code": updated_code,
    }
