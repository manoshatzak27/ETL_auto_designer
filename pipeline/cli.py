"""Command line interface.

    python -m pipeline.cli check
    python -m pipeline.cli match --name SBP --description "Systolic blood pressure (mmHg)"
    python -m pipeline.cli match --description "Hair sample collected" --explain
    python -m pipeline.cli --domain Measurement match --name height
    python -m pipeline.cli domains
    python -m pipeline.cli shell
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
from typing import Sequence

from .config import PipelineConfig
from .db import Database, shared_database
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
    parser.add_argument("--domain", action="append", metavar="DOMAIN",
                        help="only accept concepts from this OMOP domain; "
                             "repeat the flag or comma-separate for several. "
                             "Names are case-insensitive; `domains` lists them")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="skip Stage 5 (its weight is redistributed)")
    parser.add_argument("--no-log", action="store_true",
                        help="do not write to the pipeline log tables")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify the database is ready")

    sub.add_parser("domains",
                   help="list the domains --domain accepts, with concept counts")

    match = sub.add_parser("match", help="map a single column")
    match.add_argument("--name", help="source column name; optional if "
                                      "--description is given")
    match.add_argument("--description", help="source column description; "
                                             "may be used on its own")
    match.add_argument("--table", help="source table name")
    match.add_argument("--source-system", help="source system identifier")
    match.add_argument("--json", action="store_true", help="emit JSON")
    match.add_argument("--explain", action="store_true",
                       help="per-stage breakdown: what each stage was given, "
                            "how it ranked, and the composite arithmetic")
    match.add_argument("--candidates", type=int, default=5,
                       help="how many candidates to show (default 5)")

    shell = sub.add_parser(
        "shell", help="interactive: type columns, see matches",
        description="One column per line, as `name | description` or just free "
                    "text. The database connection and the embedding model are "
                    "opened once and reused, so each query costs milliseconds "
                    "rather than a model load.")
    shell.add_argument("--candidates", type=int, default=5,
                       help="how many candidates to show (default 5)")
    shell.add_argument("--source-system", help="source system identifier")
    shell.add_argument("--explain", action="store_true",
                       help="per-stage breakdown for every query")

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


def requested_domains(args: argparse.Namespace) -> list[str]:
    """The --domain values, flattened.

    Split on commas only, never on whitespace: several OMOP domain names
    contain a space ("Meas Value", "Spec Anatomic Site").
    """
    values = getattr(args, "domain", None) or []
    return [part.strip() for value in values
            for part in value.split(",") if part.strip()]


def apply_domain_restriction(config: PipelineConfig, names: list[str]) -> int:
    """Resolve and install the domain restriction. Returns an exit code.

    Resolved against the database rather than accepted as typed, because a
    misspelled domain would otherwise filter every candidate away and report
    206 unmapped columns — which looks like a broken pipeline, not a typo.
    The shared pool is reused, so this costs one query and no extra connection.
    """
    try:
        config.restrict_domains = tuple(shared_database(config).resolve_domains(names))
    except ValueError as exc:
        print(f"--domain: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"--domain: cannot reach the vocabulary database: {exc}",
              file=sys.stderr)
        return 1
    # stderr, so that `match --json | jq` stays valid.
    print(f"Restricted to domain: {', '.join(config.restrict_domains)}",
          file=sys.stderr)
    return 0


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


def cmd_domains(config: PipelineConfig) -> int:
    db = shared_database(config)
    rows = db.domain_counts()
    print(format_table(rows))
    print(f"\n{len(rows)} domains hold standard, valid concepts. Restrict a "
          "search to one or more with, e.g.:")
    print("  --domain Measurement --domain Observation")
    print("  --domain 'Meas Value'")
    empty = sorted(set(db.domain_ids()) - {r["domain_id"] for r in rows})
    if empty:
        print(f"\nDefined but empty here (non-standard by nature): "
              f"{', '.join(empty)}")
    return 0


def _idf_weights(matcher: ConceptMatcher, result: MatchResult) -> dict[str, float]:
    """Stage 3's corpus weights for the query tokens, for the explain view."""
    tokens = {t for variant in result.context.variants for t in variant.tokens}
    if not tokens:
        return {}
    try:
        return matcher.stage3.idf.weights(tokens)
    except Exception:            # explaining must never break the query itself
        return {}


def _name_probes(matcher: ConceptMatcher, result: MatchResult) -> list[str] | None:
    """The exact-match keys Stage 1 tried, for the explain view."""
    try:
        return matcher.stage1.name_probes(result.context)
    except Exception:        # explaining must never break the query itself
        return None


def cmd_match(config: PipelineConfig, args: argparse.Namespace) -> int:
    if not (args.name or args.description):
        print("match needs --name, --description, or both", file=sys.stderr)
        return 1

    matcher = ConceptMatcher(config)
    try:
        # An absent name is passed through as "" rather than folded into the
        # description: Stage 0 joins name and description, so reusing the
        # description as the name would search every one of its words twice.
        result = matcher.match(args.name or "", args.description, args.table,
                               args.source_system)
        if args.json:
            print(json.dumps(result.as_dict(), indent=2, default=str))
        elif args.explain:
            print(render_explain(result, config, args.candidates,
                                 _idf_weights(matcher, result),
                                 _name_probes(matcher, result)))
        else:
            print(render_result(result, args.candidates, config.restrict_domains))
        matcher.finish_run()
    finally:
        matcher.close()
    return 0 if result.status != "error" else 1


_SHELL_BANNER = """\
Type a column to map, one per line:

    creat_mgdl | Serum creatinine mg/dL      name and description
    creat_mgdl                               name only
    | Serum creatinine mg/dL                 description only

    :domain Measurement                      accept only this domain from now on
    :domain Measurement, Observation         several
    :domain off                              drop the restriction
    :domain                                  show what is in force

Ctrl-D or `quit` to leave.
"""


def _split_shell_input(line: str) -> tuple[str, str | None]:
    """Split a shell line into (name, description).

    `|` separates the two because it does not occur in identifiers and, unlike
    a comma or a colon, does not occur in LOINC-style descriptions either.
    Without one the whole line is the name — Stage 0 concatenates name and
    description anyway, so free text still searches exactly as typed. A line
    that *starts* with the separator is description-only, which is the one
    combination the concatenation cannot express.
    """
    name, sep, description = line.partition("|")
    return name.strip(), (description.strip() or None) if sep else None


def _shell_domain(matcher: ConceptMatcher, argument: str) -> None:
    """Handle `:domain ...` — set, clear or report the restriction.

    Mutating the live config is safe here and nowhere else: the shell is one
    thread asking one question at a time, and the alternative (restarting the
    process to change a filter) would cost the model load that the shell exists
    to amortize.
    """
    config = matcher.config
    current = ", ".join(config.restrict_domains) or "(none)"
    if not argument:
        print(f"  domain restriction: {current}")
        return
    if argument.lower() in {"off", "none", "any", "clear", "-"}:
        config.restrict_domains = ()
        print("  domain restriction: (none) — every domain accepted")
        return
    try:
        resolved = matcher.db.resolve_domains(argument.split(","))
    except ValueError as exc:
        print(f"  {exc}\n  restriction unchanged: {current}")
        return
    config.restrict_domains = tuple(resolved)
    print(f"  domain restriction: {', '.join(resolved)}")


def cmd_shell(config: PipelineConfig, args: argparse.Namespace) -> int:
    matcher = ConceptMatcher(config)
    mapped = 0
    try:
        # Force the lazy connection pool and model open before the first
        # prompt, so the wait happens visibly here instead of looking like the
        # first query is inexplicably slow.
        print("Opening database and embedding model...", flush=True)
        if matcher.stage5.enabled:
            matcher.stage5.model.encode(["warmup"])
        else:
            matcher.db.vocabulary_version()
        print(f"\n{_SHELL_BANNER}")

        while True:
            try:
                line = input("column> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in {"quit", "exit", ":q"}:
                break
            if line.split()[0] == ":domain":
                _shell_domain(matcher, line[len(":domain"):].strip())
                print()
                continue

            name, description = _split_shell_input(line)
            if not (name or description):
                continue
            try:
                result = matcher.match(name, description,
                                       source_system=args.source_system)
            except Exception as exc:               # keep the session alive
                print(f"  failed: {exc}\n")
                continue
            mapped += 1
            if args.explain:
                print(render_explain(result, config, args.candidates,
                                     _idf_weights(matcher, result),
                                     _name_probes(matcher, result)))
            else:
                print(render_result(result, args.candidates,
                                    config.restrict_domains))
            print()
    finally:
        if mapped:
            matcher.finish_run()
        matcher.close()
    return 0


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


def render_result(result: MatchResult, candidate_limit: int = 5,
                  restrict_domains: Sequence[str] = ()) -> str:
    ctx = result.context
    lines = [
        f"Column      : {ctx.column_name}",
        f"Description : {ctx.column_description or '-'}",
        "",
        "Stage 0 — context",
        f"  search string : {ctx.search_string!r}",
        f"  tokens        : {ctx.tokens}",
    ]
    if len(ctx.variants) > 1:
        # Which interpretations were actually searched. Worth showing: the
        # winning concept is often lifted by a variant other than the primary,
        # and without this the printed search string looks unrelated to it.
        lines.append("  searched as   :")
        lines += [f"    [{v.label}] {v.search_string!r}" for v in ctx.variants]
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
    if restrict_domains:
        lines.append("  restricted to : " + ", ".join(restrict_domains)
                     + "  (hard filter — nothing else can be returned)")

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


# Boosts Stage 6 applies to the weighted mean, in the order _composite applies
# them. Everything else in `Candidate.boosts` was recorded by an earlier stage
# and is already baked into that stage's own score.
_STAGE6_MULTIPLIERS = ("final_vocabulary", "final_domain", "broad_class")


def render_explain(result: MatchResult, config: PipelineConfig,
                   candidate_limit: int = 5,
                   idf_weights: dict[str, float] | None = None,
                   name_probes: list[str] | None = None) -> str:
    """Per-stage breakdown: what each stage was given, did, and handed on."""
    ctx = result.context
    timings = {t.stage: t for t in result.timings}
    lines: list[str] = []

    def head(title: str, stage_key: str | None = None) -> None:
        bar = ""
        if stage_key and stage_key in timings:
            t = timings[stage_key]
            flow = ("" if t.candidates_in is None
                    else f"{t.candidates_in:>4} -> ")
            out = "" if t.candidates_out is None else f"{t.candidates_out}"
            bar = f"{t.elapsed_ms:9.1f} ms   {flow}{out}"
        lines.append("")
        lines.append(f"{title:<44}{bar}")
        lines.append("-" * 78)

    # ── Stage 0 ─────────────────────────────────────────────────────────
    head("Stage 0 — preprocessing", "stage0_preprocess")
    lines += [
        f"  column name    : {ctx.column_name or '(none)'}",
        f"  description    : {ctx.column_description or '(none)'}",
        f"  raw input      : {ctx.raw_input!r}",
        f"  normalized     : {ctx.normalized_input!r}",
        f"  lookup key     : {ctx.lookup_key!r}",
    ]
    if ctx.components:
        lines.append(f"  components     : {ctx.components}")
    lines.append("  extracted      : "
                 + (", ".join(filter(None, [
                     ", ".join(f"code {c.vocabulary_id}:{c.code}" for c in ctx.codes),
                     ", ".join(f"unit {u.canonical} ({u.unit_type})" for u in ctx.units),
                     ", ".join(f"{n.kind} {n.operator or ''}{list(n.values)}"
                               for n in ctx.numeric_constraints),
                     ", ".join(f"date {d}" for d in ctx.date_formats),
                 ])) or "(nothing)"))
    if ctx.abbreviations:
        lines.append("  abbreviations  : " + ", ".join(
            f"{a.abbreviation}->{a.expansion}" + (" (ambiguous)" if a.ambiguous else "")
            for a in ctx.abbreviations))
    if ctx.spelling_corrections:
        lines.append("  spelling       : " + ", ".join(
            f"{typed}->{fixed}" for typed, fixed in ctx.spelling_corrections)
            + "  (searched as an extra interpretation; the original is kept)")
    lines.append(f"  hints          : domain={ctx.domain_hint or '(none)'} "
                 f"vocabulary={ctx.vocabulary_hint or '(none)'} "
                 f"numeric={ctx.numeric_flag} temporal={ctx.temporal_flag}")
    if config.restrict_domains:
        lines.append("  restricted to  : " + ", ".join(config.restrict_domains))
        lines.append("                   a hard filter in Stages 1, 2 and 5. It "
                     "outranks the domain hint above,")
        lines.append("                   and the Stage 6.4 retry cannot widen "
                     "past it.")
    lines.append(f"  interpretations: {len(ctx.variants)} "
                 f"(each one searched separately through Stages 2-5)")
    for variant in ctx.variants:
        lines.append(f"    [{variant.label}] {variant.search_string!r}")
        lines.append(f"        tokens   {list(variant.tokens)}")
        if variant.protected_phrases:
            lines.append(f"        phrases  {list(variant.protected_phrases)}")
    if idf_weights:
        lines.append("  token IDF (the Stage 3 weights; higher = rarer, "
                     "so more discriminating):")
        for token, weight in sorted(idf_weights.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {token:<28} {weight:6.2f}")

    # ── Stage 1 ─────────────────────────────────────────────────────────
    head("Stage 1 — exact and code match", "stage1_exact")
    lines.append("  order          : manual mapping, then code, then exact "
                 "concept name, then exact synonym")
    lines.append("  codes probed   : " + (", ".join(
        f"{c.vocabulary_id}:{c.code}" for c in ctx.codes) or "(none extracted)"))
    if name_probes is None:
        lines.append(f"  name probes    : (not available)")
    else:
        lines.append("  name probes    : " + (
            ", ".join(repr(p) for p in name_probes) or
            "(none — every candidate key was a bare acronym, which is excluded)"))
    if ctx.domain_hint:
        lines.append(f"  domain filter  : {ctx.domain_hint} (a hard filter here, "
                     "not a preference)")
    if config.restrict_domains:
        lines.append("  restriction    : " + ", ".join(config.restrict_domains)
                     + " — applies to the deterministic hits too,")
        lines.append("                   including a stored manual mapping, "
                     "which is otherwise never overruled")
    if result.decided_at_stage == "stage1":
        lines.append(f"  RESULT         : hit — {result.decision_reason}")
    else:
        lines.append("  RESULT         : no hit; continuing to the ranked stages")

    # ── Stages 2-5 ──────────────────────────────────────────────────────
    for title, key, attr in (
        ("Stage 2 — full-text search", "stage2_fts", "fts_score"),
        ("Stage 3 — IDF-weighted Jaccard", "stage3_jaccard", "jaccard_score"),
        ("Stage 4 — fuzzy / trigram", "stage4_fuzzy", "fuzzy_score"),
        ("Stage 5 — embeddings", "stage5_embedding", "embedding_score"),
    ):
        head(title, key)
        scored = [c for c in result.candidates if getattr(c, attr) is not None]
        if not scored:
            lines.append("  (stage did not run, or scored nothing)")
            continue
        if key == "stage2_fts":
            lines.append("  rung 0 = every term, adjacent | 1 = phrase, looser | "
                         "2 = every term, any order | 3 = some terms")
        # `scored` is the surviving candidate set, not everything the stage saw
        # — Stage 2 may have returned 30 and handed 15 on. The counts in the
        # header bar are the real per-stage flow; this is only the reordering.
        lines.append(f"  how this stage alone ranked the {len(scored)} "
                     f"candidates that survived to the end:")
        for cand in sorted(scored, key=lambda c: -getattr(c, attr))[:candidate_limit]:
            extra = ""
            if key == "stage2_fts":
                extra = (f"  rung={int(cand.boosts.get('fts_rung', -1))}"
                         f" raw={cand.raw_rank:.4f}")
            lines.append(f"    {getattr(cand, attr):6.1f}{extra}  "
                         f"{cand.concept_id} {cand.concept_name[:52]}")
            if key == "stage2_fts" and cand.variant_label != "primary":
                lines.append(f"            found by interpretation "
                             f"[{cand.variant_label}]")

    # ── Stage 6 ─────────────────────────────────────────────────────────
    head("Stage 6 — composite scoring", "stage6_composite")
    lines.append("  weights        : " + "  ".join(
        f"{name} {config.weight_for(name):.2f}"
        for name in ("fts", "jaccard", "fuzzy", "embedding")))
    lines.append(f"  thresholds     : auto_accept >= {config.auto_accept_threshold}, "
                 f"review >= {config.review_threshold}, "
                 f"ambiguity margin {config.ambiguity_margin}")
    for rank, cand in enumerate(result.candidates[:candidate_limit], start=1):
        lines.append("")
        marker = "  <- selected" if (result.selected is not None
                                     and cand.concept_id == result.selected.concept_id) else ""
        lines.append(f"  #{rank} {cand.concept_id} {cand.concept_name[:56]}{marker}")
        lines.append(f"       {cand.vocabulary_id} / {cand.domain_id} / "
                     f"{cand.concept_class_id}")
        available = {n: v for n, v in cand.stage_scores().items() if v is not None}
        total_weight = sum(config.weight_for(n) for n in available)
        for name, value in available.items():
            weight = config.weight_for(name)
            lines.append(f"       {name:<10} {value:6.1f} x {weight:.2f} "
                         f"= {value * weight:6.2f}")
        running = (sum(v * config.weight_for(n) for n, v in available.items())
                   / total_weight) if total_weight > 0 else 0.0
        if len(available) < 4:
            lines.append(f"       {'':<10} weights renormalized over "
                         f"{'/'.join(available)} (sum {total_weight:.2f})")
        lines.append(f"       {'weighted':<10} {running:6.2f}")
        for name in _STAGE6_MULTIPLIERS:
            if name in cand.boosts:
                running = min(running * cand.boosts[name], 100.0)
                lines.append(f"       x {name:<8} {cand.boosts[name]:6.2f} "
                             f"-> {running:6.2f}")
        if "hierarchy_depth" in cand.boosts:
            running = min(running * cand.boosts["hierarchy_depth"], 100.0)
            lines.append(f"       x {'hierarchy':<8} "
                         f"{cand.boosts['hierarchy_depth']:6.2f} -> {running:6.2f}")
        lines.append(f"       {'COMPOSITE':<10} {cand.composite_score:6.2f}")
        if abs(running - cand.composite_score) > 0.05:
            # Say so rather than let a silently-wrong sum look authoritative.
            lines.append(f"       (reconstruction differs by "
                         f"{cand.composite_score - running:+.2f} — an adjustment "
                         f"above is not accounted for here)")
        earlier = {k: v for k, v in cand.boosts.items()
                   if k not in _STAGE6_MULTIPLIERS and k
                   not in ("hierarchy_depth", "fts_rung")}
        if earlier:
            lines.append("       already in the stage scores: " + ", ".join(
                f"{k}={v:.2f}" for k, v in earlier.items()))
        if cand.notes:
            lines += [f"       note: {note}" for note in cand.notes]

    # ── Stage 7 ─────────────────────────────────────────────────────────
    head("Stage 7 — validation and decision")
    lines += [
        f"  status         : {result.status.upper()}",
        f"  confidence     : {result.confidence:.1f}",
        f"  decided at     : {result.decided_at_stage or '-'}",
        f"  reason         : {result.decision_reason or '-'}",
    ]
    lines.append("  flags          : " + ("(none)" if not result.validation_flags else ""))
    lines += [f"    ! {flag}" for flag in result.validation_flags]

    lines.append("")
    lines.append(f"  {'total':<26} {result.elapsed_ms:8.1f} ms")
    return "\n".join(line.rstrip() for line in lines)


# ── entry point ─────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args)

    names = requested_domains(args)
    if names and apply_domain_restriction(config, names) != 0:
        return 1

    handlers = {
        "check": lambda: cmd_check(config),
        "domains": lambda: cmd_domains(config),
        "match": lambda: cmd_match(config, args),
        "shell": lambda: cmd_shell(config, args),
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
