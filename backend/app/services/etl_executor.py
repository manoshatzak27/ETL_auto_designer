"""
Writes generated Python ETL code to a temp file, executes it as a subprocess,
captures stdout/stderr, and returns the log + list of produced output files.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


async def execute_etl_code(
    code: str,
    source_path: str,
    output_dir: str,
    project_id: str,
    mapping_files: dict,
) -> tuple[str, str, list[str]]:
    """
    Returns (log: str, status: 'success'|'error', output_files: list[str])
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Write generated code to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix=f"etl_{project_id}_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp_path = tmp.name
        tmp.write(code)

    env = os.environ.copy()
    env["ETL_SOURCE_PATH"] = source_path
    env["ETL_OUTPUT_DIR"] = output_dir
    env["ETL_MAPPING_FILES"] = json.dumps(mapping_files)

    log_lines: list[str] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        log_lines.append(stdout.decode("utf-8", errors="replace"))
        status = "success" if proc.returncode == 0 else "error"
        if proc.returncode != 0:
            log_lines.append(f"\n[Exit code: {proc.returncode}]")
    except asyncio.TimeoutError:
        log_lines.append("\n[ERROR] Execution timed out after 300 seconds.")
        status = "error"
    except Exception as exc:
        log_lines.append(f"\n[ERROR] {exc}")
        status = "error"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Collect output CSV files
    output_files = [str(p) for p in output_path.glob("*.csv")]

    return "".join(log_lines), status, output_files
