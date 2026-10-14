"""
Runs each generated OMOP table script as an isolated subprocess, in dependency order.
Captures per-table stdout/stderr and stops on first failure.
"""
import asyncio
import json
import os
import re
import shutil
import sys
from collections import Counter, deque
from pathlib import Path

_RUNTIME_MODULE = Path(__file__).parent / "etl_runtime.py"

# Full logs of the last run, written next to the output CSVs: every line
# (detailed) and every line except INFO (basic).
RUN_LOG_FILENAME = "etl_run.log"
BASIC_RUN_LOG_FILENAME = "etl_run_basic.log"
# Per-table counts of the last run (rows written, dropped, warnings) for the summary table.
RUN_STATS_FILENAME = "etl_run_stats.json"
# Prefix of the per-row diagnostics etl_runtime._info prints; the basic log drops them.
INFO_PREFIX = "INFO:"
WARNING_PREFIX = "WARNING:"
# Script output lines per table kept in the stored basic log. A setup problem
# that hits every row prints one WARNING per row (x variable), which can reach
# millions of lines; past this, only the last _TAIL_LINES are kept (so a crash
# traceback still shows) and the full basic log is a download.
_LINES_PER_TABLE = 500
_TAIL_LINES = 20
# Distinct warning kinds listed in each table's summary.
_SUMMARY_KINDS = 20
# Max bytes per stdout line (asyncio's default of 64 KiB would fail on a long traceback line).
_STREAM_LIMIT = 16 * 1024 * 1024

_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
# A WARNING/ERROR that reports one row not written ("skipping row …",
# "dropping measurement row …", "… patient dropped", "skipping variable …").
_DROP_RE = re.compile(r"\b(skipping|dropping|dropped)\b")
# Lines that sum up drops already reported line by line, or report them later
# as a total: "N row(s) dropped across M missing visit(s)" is counted by its N
# instead, and "every row referencing it is dropped" isn't a row of its own.
_DROP_TOTAL_RE = re.compile(r"^WARNING: (\d+) row\(s\) dropped across")
_DROP_ANNOUNCE_RE = re.compile(r"every row referencing it is dropped")


def _warning_kind(line: str) -> str:
    """Collapse a WARNING line to its kind by masking the row-specific parts
    (quoted values, row numbers, ids), so repeats of the same problem group together."""
    return _NUMBER_RE.sub("#", _QUOTED_RE.sub("'…'", line.strip()))


class _TableLog:
    """Basic-log bookkeeping for one table's script output."""

    def __init__(self) -> None:
        self.kept: list[str] = []
        self.hidden = 0
        self.tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self.warnings: Counter[str] = Counter()
        self.info = 0
        self.dropped = 0

    def add(self, line: str) -> None:
        if line.startswith(WARNING_PREFIX):
            self.warnings[_warning_kind(line)] += 1
        if line.startswith((WARNING_PREFIX, "ERROR:")):
            if m := _DROP_TOTAL_RE.match(line):
                self.dropped += int(m.group(1))
            elif _DROP_RE.search(line) and not _DROP_ANNOUNCE_RE.search(line):
                self.dropped += 1
        if len(self.kept) < _LINES_PER_TABLE:
            self.kept.append(line)
        else:
            self.hidden += 1
            self.tail.append(line)

    def render(self, table: str) -> str:
        out = list(self.kept)
        skipped = self.hidden - len(self.tail)
        if skipped > 0:
            out.append(
                f"… {skipped:,} more lines not shown — download the full basic log to see them …\n"
            )
        out.extend(self.tail)
        total = sum(self.warnings.values())
        if total > 1:
            out.append(f"[TABLE: {table}] Warning summary — {total:,} warnings:\n")
            for kind, n in self.warnings.most_common(_SUMMARY_KINDS):
                out.append(f"  {n:>9,} × {kind}\n")
            other = len(self.warnings) - _SUMMARY_KINDS
            if other > 0:
                out.append(f"  … and {other:,} other kinds of warning\n")
        return "".join(out)


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


def _record_stats(entry: dict, table_log: "_TableLog", status: str, csv_path: Path) -> None:
    entry["status"] = status
    entry["dropped"] = table_log.dropped
    entry["warnings"] = sum(table_log.warnings.values())
    entry["info"] = table_log.info
    # Data rows in the table's output CSV (minus the header). Only for a table
    # that finished — a failed one may have left a CSV from an earlier run.
    if status == "ok" and csv_path.is_file():
        with open(csv_path, "rb") as fh:
            entry["rows_written"] = max(0, sum(1 for _ in fh) - 1)


async def execute_etl_scripts(
    scripts: dict,
    source_path: str,
    output_dir: str,
    project_id: str,
    mapping_files: dict,
    project_name: str = "",
) -> tuple[str, str, list[str], list[dict]]:
    """
    Returns (log: str, status: 'success'|'error', output_files: list[str], stats: list[dict])

    Scripts always run in detailed mode. Every line goes to RUN_LOG_FILENAME
    and every non-INFO line to BASIC_RUN_LOG_FILENAME in output_dir. The
    returned log is the basic view capped at _LINES_PER_TABLE lines of script
    output per table, plus a per-table summary of warnings by kind, so it stays
    small enough to store on the project and send to the browser.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_RUNTIME_MODULE, output_path / "etl_runtime.py")

    safe_name = re.sub(r"[^\w]", "_", project_name).strip("_") if project_name else project_id

    env = os.environ.copy()
    env["ETL_SOURCE_PATH"] = source_path
    env["ETL_OUTPUT_DIR"] = output_dir
    env["ETL_OUTPUT_MODE"] = "detailed"
    env["ETL_MAPPING_FILES"] = json.dumps(mapping_files)
    for key, path in (mapping_files or {}).items():
        env[f"ETL_MAPPING_{key}"] = str(path)

    tables_in_order = [t for t in EXECUTION_ORDER if t in scripts]
    stats: dict[str, dict] = {
        t: {"table": t, "status": "not_run", "rows_written": None, "dropped": 0, "warnings": 0, "info": 0}
        for t in tables_in_order
    }
    script_files: list[str] = []
    log_lines: list[str] = []
    overall_status = "success"

    def save_stats() -> None:
        """Write the counts so far. Rewritten as each table starts and finishes,
        so the page can poll it for progress; replaced atomically so a poll never
        reads a half-written file."""
        tmp = output_path / f"{RUN_STATS_FILENAME}.tmp"
        tmp.write_text(json.dumps(list(stats.values())), encoding="utf-8")
        tmp.replace(output_path / RUN_STATS_FILENAME)

    save_stats()

    with (
        open(output_path / RUN_LOG_FILENAME, "w", encoding="utf-8") as detailed_log,
        open(output_path / BASIC_RUN_LOG_FILENAME, "w", encoding="utf-8") as basic_log,
    ):
        def emit(text: str) -> None:
            """Executor's own lines (table headers, status) — always shown."""
            detailed_log.write(text)
            basic_log.write(text)
            log_lines.append(text)

        for table in tables_in_order:
            script_path = output_path / f"etl_{safe_name}_{table}.py"
            script_path.write_text(scripts[table], encoding="utf-8")
            script_files.append(str(script_path))

            emit(f"\n{'=' * 50}\n[TABLE: {table}] Starting...\n{'=' * 50}\n")
            table_log = _TableLog()
            stats[table]["status"] = "running"
            save_stats()

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(script_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                    limit=_STREAM_LIMIT,
                )

                # Stream line by line — a run can print millions of lines,
                # which must not be held in memory all at once.
                async def drain() -> None:
                    while raw := await proc.stdout.readline():
                        line = raw.decode("utf-8", errors="replace")
                        detailed_log.write(line)
                        if line.startswith(INFO_PREFIX):
                            table_log.info += 1
                        else:
                            basic_log.write(line)
                            table_log.add(line)
                    await proc.wait()

                await asyncio.wait_for(drain(), timeout=300)

                log_lines.append(table_log.render(table))
                _record_stats(stats[table], table_log, "ok" if proc.returncode == 0 else "failed", output_path / f"{table}.csv")
                save_stats()
                if proc.returncode == 0:
                    emit(f"[TABLE: {table}] OK\n")
                else:
                    emit(f"[TABLE: {table}] FAILED (exit code {proc.returncode})\n")
                    overall_status = "error"
                    break
            except asyncio.TimeoutError:
                if proc and proc.returncode is None:
                    proc.kill()
                log_lines.append(table_log.render(table))
                _record_stats(stats[table], table_log, "failed", output_path / f"{table}.csv")
                save_stats()
                emit(f"[TABLE: {table}] ERROR: Timed out after 300 seconds.\n")
                overall_status = "error"
                break
            except Exception as exc:
                if proc and proc.returncode is None:
                    proc.kill()
                log_lines.append(table_log.render(table))
                _record_stats(stats[table], table_log, "failed", output_path / f"{table}.csv")
                save_stats()
                emit(f"[TABLE: {table}] ERROR: {exc}\n")
                overall_status = "error"
                break

    output_files = [str(p) for p in sorted(output_path.glob("*.csv"))]

    return "".join(log_lines), overall_status, output_files, list(stats.values())
