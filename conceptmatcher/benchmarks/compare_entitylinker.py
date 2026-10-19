"""Compare this pipeline against the ETL Auto-Designer's EntityLinker.

    .venv/bin/python benchmarks/compare_entitylinker.py --suite user
    .venv/bin/python benchmarks/compare_entitylinker.py --suite gold

Three systems, the same inputs, the same grader:

  pipeline     this repo — Stage 0-6 lexical + embedding ensemble over every
               standard concept (3.5M, all vocabularies)
  linker       EntityLinker — SapBERT embeddings + FAISS cosine over SNOMED
               and LOINC only, no lexical stage
  linker+gpt   the same, with its GPT reasoning reranker over the top_k

Run with both services up (EntityLinker on :8001, the stack's Postgres on
:5432). The GPT reranker is a paid API call of roughly 6s per column, so it is
sampled with --limit unless you ask for the whole set.

Two asymmetries to keep in mind when reading the numbers; neither is fixable
from here, and both are reported rather than corrected:

* **Input shape.** EntityLinker takes one string. This pipeline takes a column
  name *and* a description and uses both. Every system here is handed the
  description, which is the informative half for these columns — variable names
  in the curated set are opaque codes like `HAQ01HAQ01SQ6`. The pipeline is
  additionally given the name, because withholding it would measure a
  configuration nobody runs.
* **Search space.** EntityLinker indexes SNOMED + LOINC. This pipeline searches
  every standard concept, so it can return an RxNorm, PPI or UK Biobank concept
  that EntityLinker is structurally incapable of returning — more chances to be
  wrong, on a metric that only counts exact agreement. The `user` suite is
  therefore restricted to the columns whose curated target is SNOMED or LOINC
  (114 of the 117 reachable ones), so at least the right answer is reachable by
  both.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import PipelineConfig       # noqa: E402
from pipeline.runner import ConceptMatcher       # noqa: E402

LINKER_URL = "http://localhost:8001/api/conceptlink"
GOLD_SET = Path(__file__).resolve().parent / "gold_set.csv"
USER_GOLD = Path(__file__).resolve().parent / "user_mappings" / "user_gold.csv"
LINKER_VOCABULARIES = {"SNOMED", "LOINC"}
CUSTOM_ID_FLOOR = 2_000_000_000


# ── the two systems ─────────────────────────────────────────────────────


def ask_linker(query: str, top_k: int, use_reranker: bool,
               timeout: float = 180.0) -> list[dict]:
    """POST one term to EntityLinker, returning its ranked concepts."""
    body = json.dumps({"query": query, "top_k": top_k,
                       "use_reranker": use_reranker}).encode("utf-8")
    request = urllib.request.Request(
        LINKER_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        # A failed call must not be scored as a wrong answer — it is reported.
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    return payload.get("conceptlinks", [])


def ask_pipeline(matcher: ConceptMatcher, name: str, description: str,
                 top_k: int) -> tuple[list[dict], str]:
    """The pipeline's ranked concepts, with its *selected* answer first.

    `result.selected` is what the pipeline actually returns, and it is not
    always `candidates[0]`: Stage 7 can redirect a deprecated concept, and a
    Stage 1 exit produces a single candidate by a different route. Judging
    candidates[0] instead scored this pipeline one case lower than its own
    benchmark does, which is a bug in the comparison, not in the pipeline.
    """
    result = matcher.match(name, description, log_result=False)
    ranked = [{"concept_id": c.concept_id, "concept_name": c.concept_name,
               "vocabulary_id": c.vocabulary_id, "score": c.composite_score}
              for c in result.candidates[:top_k]]
    if result.selected is not None:
        chosen = {"concept_id": result.selected.concept_id,
                  "concept_name": result.selected.concept_name,
                  "vocabulary_id": result.selected.vocabulary_id,
                  "score": result.selected.composite_score}
        ranked = [chosen] + [r for r in ranked
                             if r["concept_id"] != chosen["concept_id"]]
    return ranked[: max(top_k, 1)], result.status


# ── grading ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def accepts_name(case: dict, concept_name: str | None) -> bool:
    """gold suite: the name-rule grader from compare_models.py."""
    if not concept_name:
        return False
    name = concept_name.lower()
    if any(_term_pattern(bad).search(name) for bad in case["forbid"]):
        return False
    return any(_term_pattern(good).search(name) for good in case["require"])


def load_gold_cases() -> list[dict]:
    lines = [l for l in GOLD_SET.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    cases = []
    for row in csv.DictReader(lines):
        cases.append({
            "key": row["name"],
            "name": row["name"],
            "description": row["description"],
            "require": [t.strip().lower() for t in row["require"].split(";") if t.strip()],
            "forbid": [t.strip().lower() for t in (row.get("forbid") or "").split(";") if t.strip()],
            "kind": (row.get("kind") or "literal").strip(),
        })
    return cases


def load_user_cases(matcher: ConceptMatcher) -> list[dict]:
    """user suite: curated concept_ids, restricted to what both tools can reach."""
    rows = []
    for row in csv.DictReader(USER_GOLD.read_text(encoding="utf-8").splitlines()):
        if not (row.get("variable") or "").strip():
            continue
        rows.append({"variable": row["variable"].strip(),
                     "description": (row.get("description") or "").strip(),
                     "target": int(row["user_concept_id"])})

    ids = sorted({r["target"] for r in rows if r["target"] < CUSTOM_ID_FLOOR})
    present = {r["concept_id"]: r for r in matcher.db.fetch_all(
        "SELECT concept_id, concept_name, vocabulary_id FROM vocab.concept "
        "WHERE concept_id = ANY(%s)", (ids,))}

    cases = []
    for row in rows:
        concept = present.get(row["target"])
        if concept is None or concept["vocabulary_id"] not in LINKER_VOCABULARIES:
            continue
        cases.append({"key": row["variable"], "name": row["variable"],
                      "description": row["description"], "target": row["target"],
                      "target_name": concept["concept_name"],
                      "target_vocab": concept["vocabulary_id"]})
    return cases


# ── reporting ───────────────────────────────────────────────────────────


def summarize(label: str, records: list[dict], total: int, elapsed: float) -> dict:
    hits1 = sum(1 for r in records if r["top1"])
    hits5 = sum(1 for r in records if r["top5"])
    errors = sum(1 for r in records if r["error"])
    return {"system": label, "top1": hits1, "top5": hits5, "errors": errors,
            "n": total, "top1_pct": 100.0 * hits1 / max(total, 1),
            "top5_pct": 100.0 * hits5 / max(total, 1),
            "ms": 1000.0 * elapsed / max(total, 1)}


def print_table(rows: list[dict]) -> None:
    print(f"\n  {'system':<12} {'top-1':>8} {'top-5':>8} {'errors':>7} {'ms/col':>9}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*7} {'-'*9}")
    for r in rows:
        print(f"  {r['system']:<12} {r['top1_pct']:7.1f}% {r['top5_pct']:7.1f}% "
              f"{r['errors']:7} {r['ms']:9.0f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=("user", "gold"), default="user")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int,
                        help="first N cases only (use for the paid reranker)")
    parser.add_argument("--skip-gpt", action="store_true",
                        help="omit the GPT reranker run")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    config = PipelineConfig.load()
    config.log_to_db = False
    matcher = ConceptMatcher(config)

    cases = load_user_cases(matcher) if args.suite == "user" else load_gold_cases()
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no comparable cases", file=sys.stderr)
        return 1

    systems = ["pipeline", "linker"] + ([] if args.skip_gpt else ["linker+gpt"])
    print(f"{len(cases)} cases, suite={args.suite}, systems={', '.join(systems)}")

    def judge(case: dict, concepts: list[dict]) -> tuple[bool, bool, str]:
        error = next((c["error"] for c in concepts if c.get("error")), "")
        if error:
            return False, False, error
        if args.suite == "user":
            ids = [c.get("concept_id") for c in concepts]
            return (bool(ids) and ids[0] == case["target"],
                    case["target"] in ids, "")
        names = [c.get("concept_name") for c in concepts]
        return (bool(names) and accepts_name(case, names[0]),
                any(accepts_name(case, n) for n in names), "")

    results: dict[str, list[dict]] = {s: [] for s in systems}
    timings: dict[str, float] = {s: 0.0 for s in systems}
    rows_out: list[dict] = []

    for index, case in enumerate(cases, start=1):
        query = case["description"] or case["name"]
        row = {"key": case["key"], "query": query}
        if args.suite == "user":
            row["target"] = case["target"]
            row["target_name"] = case["target_name"]

        for system in systems:
            started = time.perf_counter()
            if system == "pipeline":
                concepts, status = ask_pipeline(
                    matcher, case["name"], case["description"], args.top_k)
                row["pipeline_status"] = status
            else:
                concepts = ask_linker(query, args.top_k,
                                      use_reranker=(system == "linker+gpt"))
            timings[system] += time.perf_counter() - started

            top1, top5, error = judge(case, concepts)
            results[system].append({"top1": top1, "top5": top5, "error": error})
            row[f"{system}_top1"] = top1
            row[f"{system}_top5"] = top5
            row[f"{system}_pick"] = (concepts[0].get("concept_name")
                                     if concepts and not error else "")
            row[f"{system}_pick_id"] = (concepts[0].get("concept_id")
                                        if concepts and not error else "")
            row[f"{system}_error"] = error

        rows_out.append(row)
        print(f"  {index}/{len(cases)}", end="\r", flush=True)

    matcher.close()

    table = [summarize(s, results[s], len(cases), timings[s]) for s in systems]
    print_table(table)

    # Where they disagree is more informative than the totals.
    print(f"\n  Cases the pipeline got and '{systems[1]}' did not:")
    shown = 0
    for row in rows_out:
        if row.get("pipeline_top1") and not row.get("linker_top1"):
            shown += 1
            if shown <= 8:
                print(f"    {row['key']:<22} {str(row.get('target_name',''))[:34]:<34} "
                      f"linker: {str(row.get('linker_pick'))[:32]}")
    print(f"    ({shown} total)")

    print(f"\n  Cases '{systems[1]}' got and the pipeline did not:")
    shown = 0
    for row in rows_out:
        if row.get("linker_top1") and not row.get("pipeline_top1"):
            shown += 1
            if shown <= 8:
                print(f"    {row['key']:<22} {str(row.get('target_name',''))[:34]:<34} "
                      f"pipeline: {str(row.get('pipeline_pick'))[:30]}")
    print(f"    ({shown} total)")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted({k for row in rows_out for k in row})
        with args.out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"\n  Wrote {len(rows_out)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
