"""
AI chat service for discussing and modifying generated ETL scripts.

The AI receives the full current script as context and can answer questions
or return an updated version inside a ```python ... ``` fence.
"""
import json
import re
from openai import AsyncOpenAI, BadRequestError
from app.config import settings

_CODE_FENCE_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)

_MAX_OUTPUT_TOKENS = 16000
# Older turns of the same table's conversation are dropped past this.
_MAX_HISTORY_MESSAGES = 20

# Only these scripts read the Concepts step's decisions; the others don't get
# them in their prompt.
_CONCEPT_TABLES = {
    "stem_table",
    "measurement",
    "observation",
    "drug_exposure",
    "procedure_occurrence",
    "condition_occurrence",
}
# Decision keys the UI keeps for itself ("Load concepts" alternatives, the
# include tick) — the generated scripts never read them.
_UI_ONLY_DECISION_KEYS = {"suggestions", "value_suggestions", "enabled"}


class ChatContextTooLarge(Exception):
    """The model rejected the request because the prompt exceeds its context window."""

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


def extract_code(text: str) -> str | None:
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def _estimate_tokens(text: str) -> int:
    # JSON and code run at roughly 3–4 characters per token; err on the high side.
    return len(text) // 3 + 1


def _slim(value):
    """Reduce every concept reference to its id and name, recursively.

    A matched concept also carries vocabulary, domain, score and justification,
    which the scripts don't use and which multiply the prompt size.
    """
    if isinstance(value, dict):
        if "concept_id" in value:
            return {k: value[k] for k in ("concept_id", "concept_name") if k in value}
        return {k: _slim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_slim(v) for v in value]
    return value


def _compact_decisions(decisions: dict, detail: str) -> dict:
    """The concept decisions as the script needs them.

    detail="full" keeps each value's concept; detail="variable" replaces the
    per-value concepts with their count, for projects too large to send them all.
    Excluded variables, UI-only keys and empty fields are left out.
    """
    out: dict = {}
    for var, decision in decisions.items():
        if not isinstance(decision, dict) or decision.get("enabled") is False:
            continue
        compact: dict = {}
        for key, value in decision.items():
            if key in _UI_ONLY_DECISION_KEYS or value is None or value == "" or value == {} or value == []:
                continue
            if key == "value_concepts" and detail == "variable":
                compact["value_concept_count"] = len(value)
                continue
            compact[key] = _slim(value)
        out[var] = compact
    return out


def _build_system_prompt(project, table: str, decision_detail: str = "full") -> str:
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
    if concept_decisions and table in _CONCEPT_TABLES:
        if decision_detail == "none":
            lines += [
                "## Concept mapping decisions (Concepts step)",
                "Omitted: this project has too many to fit in the conversation. At runtime the",
                "script reads them from the mapping CSVs listed above.",
                "",
            ]
        else:
            lines += [
                "## Concept mapping decisions (Concepts step)",
                "These are the per-variable concept mapping decisions made by the user.",
                "Each key is a source variable name; the value describes the mapping strategy",
                "and the selected OMOP concept IDs.",
            ]
            if decision_detail == "variable":
                lines += [
                    "Per-value concepts are omitted for size (value_concept_count says how many",
                    "a variable has); at runtime the script reads them from value_mapping.csv",
                    "and variable_value_mapping.csv.",
                ]
            lines += [
                "```json",
                json.dumps(
                    _compact_decisions(concept_decisions, decision_detail),
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ),
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

    budget = settings.openai_context_tokens - _MAX_OUTPUT_TOKENS

    # Shrink the concept decisions until the system prompt and the new message
    # fit, keeping the most detail that does.
    for detail in ("full", "variable", "none"):
        system = _build_system_prompt(project, table, detail)
        used = _estimate_tokens(system) + _estimate_tokens(user_message)
        if used <= budget:
            break

    # Previous turns of this table's conversation (the panel shows only those),
    # newest kept, as many as still fit.
    turns = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("table") in (None, table)
    ][-_MAX_HISTORY_MESSAGES:]
    while turns and used + sum(_estimate_tokens(t["content"]) for t in turns) > budget:
        turns.pop(0)

    messages: list[dict] = [{"role": "system", "content": system}, *turns]
    messages.append({"role": "user", "content": user_message})

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=0.3,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except BadRequestError as e:
        if e.code == "context_length_exceeded":
            raise ChatContextTooLarge(
                f"{table}.py and its configuration are too large for {settings.openai_model}'s "
                "context window, even with the concept decisions left out."
            ) from e
        raise

    choice = response.choices[0]
    content = choice.message.content or ""
    updated_code = extract_code(content)

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
