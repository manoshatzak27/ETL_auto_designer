"""Command line interface.

    python -m pipeline.cli check
    python -m pipeline.cli match --name SBP --description "Systolic blood pressure (mmHg)"
    python -m pipeline.cli batch columns.csv --out mappings.csv
    python -m pipeline.cli report [--run 3]
    python -m pipeline.cli feedback --column SBP --concept 4152194
    python -m pipeline.cli abbreviations
    python -m pipeline.cli refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import PipelineConfig
from .db import Database
from .models import MatchResult
from .runner import ConceptMatcher
from .stage7_validate import FeedbackStore
from .stage8_batch import (
    BatchMatcher,
    read_columns,
    refresh_derived_structures,
    write_results,
)
from .stage9_report import Reporter, format_table

_STATUS_ORDER = {"auto_accept": 0, "manual": 1, "review": 2, "unmapped": 3, "error": 4}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.cli",
        description="Map source columns onto standard OMOP concepts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", type=Path,
                        help="JSON file of PipelineConfig overrides")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="skip Stage 5 (its weight is redistributed)")
    parser.add_argument("--no-log", action="store_true",
                        help="do not write to the pipeline log tables")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify the database is ready")

    match = sub.add_parser("match", help="map a single column")
    match.add_argument("--name", required=True, help="source column name")
    match.add_argument("--description", help="source column description")
    match.add_argument("--table", help="source table name")
    match.add_argument("--source-system", help="source system identifier")
    match.add_argument("--json", action="store_true", help="emit JSON")
    match.add_argument("--candidates", type=int, default=5,
                       help="how many candidates to show (default 5)")

    batch = sub.add_parser("batch", help="map many columns from CSV or JSON")
    batch.add_argument("input", type=Path, help="CSV (name,description) or JSON")
    batch.add_argument("--out", type=Path, help="write results to CSV or JSON")
    batch.add_argument("--source-system")
    batch.add_argument("--note")
    batch.add_argument("--workers", type=int)
    batch.add_argument("--report", action="store_true",
                       help="print the Stage 9 report when finished")

    report = sub.add_parser("report", help="Stage 9 statistics and monitoring")
    report.add_argument("--run", type=int, help="limit to one run_id")

    feedback = sub.add_parser("feedback", help="record a human correction")
    feedback.add_argument("--column", required=True)
    feedback.add_argument("--description")
    feedback.add_argument("--concept", type=int, required=True)
    feedback.add_argument("--source-system")
    feedback.add_argument("--note")

    abbrev = sub.add_parser("abbreviations",
                            help="list abbreviations Stage 4 discovered")
    abbrev.add_argument("--min-seen", type=int, default=2)
    abbrev.add_argument("--promote", action="store_true",
                        help="mark the listed entries as promoted")

    weights = sub.add_parser("weights",
                             help="Stage 7.5 scoring-weight suggestions")
    weights.add_argument("--run", type=int)
    weights.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"),
                         help="persist a weight, e.g. --set stage_weights.fts 0.30")

    refresh = sub.add_parser("refresh",
                             help="Step 8.3: rebuild derived structures")
    refresh.add_argument("--full", action="store_true",
                         help="also drop the entire embedding cache")

    return parser


def load_config(args: argparse.Namespace) -> PipelineConfig:
    overrides: dict = {}
    if args.config:
        overrides = json.loads(args.config.read_text(encoding="utf-8"))
    config = PipelineConfig.load(overrides)
    if args.no_embeddings:
        config.embeddings_enabled = False
    if args.no_log:
        config.log_to_db = False
    if getattr(args, "workers", None):
        config.batch_workers = args.workers
    return config


# ── commands ────────────────────────────────────────────────────────────


def cmd_check(config: PipelineConfig) -> int:
    db = Database(config)
    try:
        problems = db.check_ready()
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1
    if problems:
        print("Not ready:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"Connected to {config.host}:{config.port}/{config.dbname} as {config.user}")
    print(f"  vocabulary version : {db.vocabulary_version()}")
    print(f"  concepts           : "
          f"{db.fetch_value('SELECT count(*) FROM pipeline.concept_search'):,}")
    print(f"  IDF tokens         : "
          f"{db.fetch_value('SELECT count(*) FROM pipeline.token_idf'):,}")
    print(f"  cached embeddings  : "
          f"{db.fetch_value('SELECT count(*) FROM pipeline.concept_embedding'):,}")
    print(f"  manual mappings    : "
          f"{db.fetch_value('SELECT count(*) FROM pipeline.manual_mapping WHERE active'):,}")

    matcher = ConceptMatcher(config, db)
    if matcher.stage5.enabled:
        print(f"  embedding model    : {config.embedding_model} "
              f"({matcher.stage5.model.dimension} dims)")
    else:
        reason = matcher.stage5.model.unavailable_reason or "disabled by config"
        print(f"  embedding model    : UNAVAILABLE — {reason}")
        print("                       Stage 5 will be skipped and its weight "
              "redistributed.")
    db.close()
    return 0


def cmd_match(config: PipelineConfig, args: argparse.Namespace) -> int:
    matcher = ConceptMatcher(config)
    try:
        result = matcher.match(args.name, args.description, args.table,
                               args.source_system)
        if args.json:
            print(json.dumps(result.as_dict(), indent=2, default=str))
        else:
            print(render_result(result, args.candidates))
        matcher.finish_run()
    finally:
        matcher.close()
    return 0 if result.status != "error" else 1


def cmd_batch(config: PipelineConfig, args: argparse.Namespace) -> int:
    columns = read_columns(args.input)
    if not columns:
        print(f"No columns found in {args.input}", file=sys.stderr)
        return 1

    print(f"Mapping {len(columns)} columns with {config.batch_workers} workers...")
    started = time.perf_counter()

    batch = BatchMatcher(config=config)

    def progress(done: int, total: int, _: MatchResult) -> None:
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total}", end="\r", flush=True)

    try:
        run_id, results = batch.run(columns, args.source_system, args.note, progress)
    finally:
        batch.close()

    elapsed = time.perf_counter() - started
    print(f"\nRun {run_id} finished in {elapsed:.1f}s "
          f"({1000 * elapsed / len(columns):.0f} ms/column)")

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    for status in sorted(counts, key=lambda s: _STATUS_ORDER.get(s, 9)):
        share = 100.0 * counts[status] / len(results)
        print(f"  {status:12} {counts[status]:5}  ({share:5.1f}%)")

    if args.out:
        write_results(results, args.out)
        print(f"Wrote {len(results)} rows to {args.out}")

    if args.report:
        db = Database(config)
        print(Reporter(db).render(run_id))
        db.close()
    return 0


def cmd_report(config: PipelineConfig, args: argparse.Namespace) -> int:
    db = Database(config)
    try:
        print(Reporter(db).render(args.run))
    finally:
        db.close()
    return 0


def cmd_feedback(config: PipelineConfig, args: argparse.Namespace) -> int:
    db = Database(config)
    try:
        concept = db.fetch_one(
            "SELECT concept_id, concept_name, domain_id, vocabulary_id "
            "FROM vocab.concept WHERE concept_id = %s", (args.concept,))
        if not concept:
            print(f"concept_id {args.concept} is not a standard, valid concept "
                  "in this database", file=sys.stderr)
            return 1

        manual_id = FeedbackStore(config, db).record_manual_mapping(
            args.column, args.concept, args.description,
            args.source_system, config.created_by, args.note)
        print(f"Recorded manual mapping {manual_id}: {args.column!r} -> "
              f"{concept['concept_id']} {concept['concept_name']} "
              f"[{concept['vocabulary_id']}/{concept['domain_id']}]")
        print("Stage 1 will short-circuit to this concept on the next run.")
    finally:
        db.close()
    return 0


def cmd_abbreviations(config: PipelineConfig, args: argparse.Namespace) -> int:
    db = Database(config)
    try:
        store = FeedbackStore(config, db)
        rows = store.pending_abbreviations(args.min_seen)
        print(format_table(rows))
        if args.promote and rows:
            count = store.promote_abbreviations(
                (r["abbreviation"], r["expansion"]) for r in rows)
            print(f"\nMarked {count} entries promoted. Add them to "
                  "pipeline/resources/abbreviations_local.json to take effect.")
    finally:
        db.close()
    return 0


def cmd_weights(config: PipelineConfig, args: argparse.Namespace) -> int:
    db = Database(config)
    try:
        store = FeedbackStore(config, db)
        if args.set:
            key, value = args.set
            store.set_weight(key, float(value), config.created_by, "set via CLI")
            print(f"Set {key} = {value}")
            return 0

        print("Current stored weights\n======================")
        print(format_table(db.fetch_all(
            "SELECT weight_key, weight, updated_at, note "
            "FROM pipeline.scoring_weight ORDER BY weight_key")))
        print("\nSuggested adjustments from human corrections (Step 7.5)")
        print("=======================================================")
        rows = store.suggest_weight_adjustments(args.run)
        print(format_table(rows))
        if rows:
            print("\nA positive avg_overrating means that stage scored the concept")
            print("the pipeline picked higher than the one a human chose — i.e. its")
            print("weight is a candidate for reduction. Apply with:")
            print("  python -m pipeline.cli weights --set stage_weights.<stage> <value>")
    finally:
        db.close()
    return 0


def cmd_refresh(config: PipelineConfig, args: argparse.Namespace) -> int:
    matcher = ConceptMatcher(config)
    try:
        print("Rebuilding derived structures (this takes a few minutes)...")
        stats = refresh_derived_structures(matcher, args.full)
        for key, value in stats.items():
            print(f"  {key:24} {value}")
    finally:
        matcher.close()
    return 0


# ── rendering ───────────────────────────────────────────────────────────


def render_result(result: MatchResult, candidate_limit: int = 5) -> str:
    ctx = result.context
    lines = [
        f"Column      : {ctx.column_name}",
        f"Description : {ctx.column_description or '-'}",
        "",
        "Stage 0 — context",
        f"  search string : {ctx.search_string!r}",
        f"  tokens        : {ctx.tokens}",
    ]
    if ctx.protected_phrases:
        lines.append(f"  phrases       : {ctx.protected_phrases}")
    if ctx.units:
        lines.append("  units         : " + ", ".join(
            f"{u.canonical} ({u.unit_type})" for u in ctx.units))
    if ctx.codes:
        lines.append("  codes         : " + ", ".join(
            f"{c.vocabulary_id}:{c.code}" for c in ctx.codes))
    if ctx.abbreviations:
        lines.append("  abbreviations : " + ", ".join(
            f"{a.abbreviation}->{a.expansion}" + (" (ambiguous)" if a.ambiguous else "")
            for a in ctx.abbreviations))
    if ctx.numeric_constraints:
        lines.append("  numerics      : " + ", ".join(
            f"{n.kind} {n.operator or ''} {list(n.values)}"
            for n in ctx.numeric_constraints))
    lines.append(f"  domain hint   : {ctx.domain_hint or '(none)'}  "
                 f"numeric={ctx.numeric_flag} temporal={ctx.temporal_flag}")

    lines += ["", "Result", f"  status     : {result.status.upper()}",
              f"  confidence : {result.confidence:.1f}",
              f"  decided at : {result.decided_at_stage or '-'}",
              f"  reason     : {result.decision_reason or '-'}"]
    if result.selected:
        s = result.selected
        lines += [
            f"  concept    : {s.concept_id}  {s.concept_name}",
            f"               {s.vocabulary_id} / {s.domain_id} / "
            f"{s.concept_class_id}  code={s.concept_code}",
        ]
    if result.validation_flags:
        lines.append("  flags      :")
        lines += [f"    ! {flag}" for flag in result.validation_flags]

    if result.candidates:
        lines += ["", f"Candidates (top {min(candidate_limit, len(result.candidates))})"]
        lines.append(f"  {'score':>7} {'fts':>6} {'jac':>6} {'fuzzy':>6} {'embed':>6}  concept")
        for cand in result.candidates[:candidate_limit]:
            lines.append(
                f"  {cand.composite_score:7.2f} {_fmt(cand.fts_score):>6} "
                f"{_fmt(cand.jaccard_score):>6} {_fmt(cand.fuzzy_score):>6} "
                f"{_fmt(cand.embedding_score):>6}  {cand.concept_id} "
                f"{cand.concept_name[:60]}")

    if result.timings:
        lines += ["", "Timings"]
        lines += [f"  {t.stage:26} {t.elapsed_ms:8.1f} ms" for t in result.timings]
        lines.append(f"  {'total':26} {result.elapsed_ms:8.1f} ms")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


# ── entry point ─────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args)

    handlers = {
        "check": lambda: cmd_check(config),
        "match": lambda: cmd_match(config, args),
        "batch": lambda: cmd_batch(config, args),
        "report": lambda: cmd_report(config, args),
        "feedback": lambda: cmd_feedback(config, args),
        "abbreviations": lambda: cmd_abbreviations(config, args),
        "weights": lambda: cmd_weights(config, args),
        "refresh": lambda: cmd_refresh(config, args),
    }
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
