"""
Runs each generated OMOP table script as an isolated subprocess, in dependency order.
Captures per-table stdout/stderr and stops on first failure.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

EXECUTION_ORDER = [
    "location",
    "care_site",
    "provider",
    "person",
    "visit_occurrence",
    "observation_period",
    "stem_table",
    "death",
    # Domain routing tables — read from stem_table.csv, must run after stem_table
    "measurement",
    "observation",
    "drug_exposure",
    "procedure_occurrence",
    "condition_occurrence",
]


async def execute_etl_scripts(
    scripts: dict,
    source_path: str,
    output_dir: str,
    project_id: str,
    mapping_files: dict,
    project_name: str = "",
) -> tuple[str, str, list[str]]:
    """
    Returns (log: str, status: 'success'|'error', output_files: list[str])
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w]", "_", project_name).strip("_") if project_name else project_id

    env = os.environ.copy()
    env["ETL_SOURCE_PATH"] = source_path
    env["ETL_OUTPUT_DIR"] = output_dir
    env["ETL_MAPPING_FILES"] = json.dumps(mapping_files)
    for key, path in (mapping_files or {}).items():
        env[f"ETL_MAPPING_{key}"] = str(path)

    tables_in_order = [t for t in EXECUTION_ORDER if t in scripts]
    script_files: list[str] = []
    log_lines: list[str] = []
    overall_status = "success"

    for table in tables_in_order:
        script_path = output_path / f"etl_{safe_name}_{table}.py"
        script_path.write_text(scripts[table], encoding="utf-8")
        script_files.append(str(script_path))

        log_lines.append(f"\n{'=' * 50}\n[TABLE: {table}] Starting...\n{'=' * 50}\n")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            log_lines.append(stdout.decode("utf-8", errors="replace"))

            if proc.returncode == 0:
                log_lines.append(f"[TABLE: {table}] OK\n")
            else:
                log_lines.append(f"[TABLE: {table}] FAILED (exit code {proc.returncode})\n")
                overall_status = "error"
                break
        except asyncio.TimeoutError:
            log_lines.append(f"[TABLE: {table}] ERROR: Timed out after 300 seconds.\n")
            overall_status = "error"
            break
        except Exception as exc:
            log_lines.append(f"[TABLE: {table}] ERROR: {exc}\n")
            overall_status = "error"
            break

    output_files = [str(p) for p in sorted(output_path.glob("*.csv"))]

    return "".join(log_lines), overall_status, output_files
