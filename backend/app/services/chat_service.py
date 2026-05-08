"""
AI chat service for discussing and modifying generated ETL scripts.

The AI receives the full current script as context and can answer questions
or return an updated version inside a ```python ... ``` fence.
"""
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
]


def _extract_code(text: str) -> str | None:
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def _build_system_prompt(project, table: str) -> str:
    code = (project.generated_scripts or {}).get(table, "")

    lines = [
        "You are an expert OMOP CDM v5.4 ETL engineer helping a user review and modify generated Python ETL scripts.",
        "",
        f"Source dataset: {project.source_filename}",
        f"Columns: {project.source_columns}",
        f"Row count: {project.source_row_count}",
        "",
        f"You are currently discussing: {table}.py",
        "",
        "You can:",
        "1. Explain what the code does and answer questions about specific sections.",
        "2. Make code modifications when asked.",
        "3. Point out potential bugs or improvements.",
        "",
        "IMPORTANT — when making code changes:",
        "- Return the COMPLETE updated Python script inside a single ```python ... ``` block.",
        "- After the block, briefly explain what you changed and why.",
        "- The code must be standalone Python using only pandas, numpy, and the standard library.",
        "- Environment variables used by the script:",
        "    ETL_SOURCE_PATH   — path to the source CSV",
        "    ETL_OUTPUT_DIR    — directory where OMOP output CSVs are written",
        "    ETL_MAPPING_FILES — JSON string {name: filepath} for the concept mapping CSVs",
        "      • variable_mapping.csv  (variable_source_code → concept_id)",
        "      • value_mapping.csv     (variable_source_code, value_source_code → value_as_concept_id)",
        "      • variable_value_mapping.csv  (variable_source_code, value_source_code → concept_id)",
        "- Output: write semicolon-delimited (;) UTF-8 CSV to ETL_OUTPUT_DIR/{table}.csv".format(table=table),
        "- Always include: if __name__ == '__main__': main()",
        "",
    ]

    if code:
        lines += [
            f"Here is the current {table}.py:",
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
        max_tokens=8192,
    )

    content = response.choices[0].message.content or ""
    updated_code = _extract_code(content)

    return {
        "response": content,
        "code_updated": updated_code is not None,
        "updated_code": updated_code,
    }
