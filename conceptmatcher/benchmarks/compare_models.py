"""Compare Stage 5 embedding models end to end against the gold set.

    .venv/bin/python benchmarks/compare_models.py
    .venv/bin/python benchmarks/compare_models.py --models none NeuML/pubmedbert-base-embeddings

Why this exists: swapping the embedding model is a one-line config change, but
whether it helps is an empirical question. Stage 5 carries 20% of the composite
score and only ever reranks the shortlist Stage 2 produced, so the honest way
to choose a model is to measure, with `none` (Stage 5 disabled, its weight
redistributed) as the baseline every other row is compared against.

Grading is by name rule rather than by concept_id — see the header of
gold_set.csv for why pinning ids does not work in OMOP.

The gold set is split into two kinds and reported separately:

  literal   realistic clinical column text. Stage 2 finds the right concept;
            the question is whether it ends up ranked first. This is the part
            Stage 5 can actually influence, so it is the number to choose a
            model on.
  semantic  lay paraphrase sharing no vocabulary with the concept name. Stage 5
            only reranks what Stage 2 retrieved, so a concept that never
            entered the shortlist cannot be recovered by any model. Low scores
            here are a property of the architecture, not of the model.

For a measurement of the model alone, isolated from the rest of the pipeline,
use stage5_rerank.py instead.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import EMBEDDING_MODEL_PRESETS, PipelineConfig   # noqa: E402
from pipeline.runner import ConceptMatcher                            # noqa: E402

GOLD_SET = Path(__file__).resolve().parent / "gold_set.csv"

DEFAULT_MODELS = [
    "none",
    "sentence-transformers/all-MiniLM-L6-v2",
    "NeuML/pubmedbert-base-embeddings",
    "abhinand/MedEmbed-small-v0.1",
]


def load_gold(path: Path) -> list[dict]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    rows = []
    for row in csv.DictReader(lines):
        rows.append({
            "name": row["name"],
            "description": row["description"],
            "require": [t.strip().lower() for t in row["require"].split(";") if t.strip()],
            "forbid": [t.strip().lower() for t in (row.get("forbid") or "").split(";") if t.strip()],
            "kind": (row.get("kind") or "literal").strip(),
        })
    return rows


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str]:
    # Word-bounded, not a raw substring: "concentration" contains "ratio", so
    # plain `in` made the forbid rule for BUN/Creatinine *ratio* reject the
    # perfectly good "Albumin mass concentration in serum".
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def accepts(case: dict, concept_name: str | None) -> bool:
    if not concept_name:
        return False
    name = concept_name.lower()
    if any(_term_pattern(bad).search(name) for bad in case["forbid"]):
        return False
    return any(_term_pattern(good).search(name) for good in case["require"])


def build_config(model: str) -> PipelineConfig:
    config = PipelineConfig.load()
    config.log_to_db = False
    if model == "none":
        config.embeddings_enabled = False
    else:
        config.embeddings_enabled = True
        config.embedding_model = model
        preset = EMBEDDING_MODEL_PRESETS.get(model, {})
        config.embedding_query_prefix = preset.get("query_prefix", "")
        config.embedding_document_prefix = preset.get("document_prefix", "")
    return config


def evaluate(model: str, gold: list[dict], verbose: bool) -> dict:
    config = build_config(model)
    matcher = ConceptMatcher(config)

    if config.embeddings_enabled:
        if not matcher.stage5.enabled:
            reason = matcher.stage5.model.unavailable_reason
            matcher.close()
            return {"model": model, "error": reason}
        matcher.stage5.model.encode(["warmup"])   # keep the load out of timing

    buckets = {"literal": [0, 0], "semantic": [0, 0]}    # [hits, total]
    top5 = auto = auto_hits = 0
    misses: list[tuple[str, str]] = []
    started = time.perf_counter()

    for case in gold:
        result = matcher.match(case["name"], case["description"], log_result=False)
        selected = result.selected
        correct = accepts(case, selected.concept_name if selected else None)

        bucket = buckets.setdefault(case["kind"], [0, 0])
        bucket[0] += correct
        bucket[1] += 1

        top5 += any(accepts(case, c.concept_name) for c in result.candidates[:5])
        if result.status == "auto_accept":
            auto += 1
            auto_hits += correct
        if not correct:
            misses.append((
                f"{case['kind'][:3]} {case['name']}",
                f"{selected.concept_name[:52] if selected else '(none)'}",
            ))

    elapsed = time.perf_counter() - started
    matcher.close()

    total = len(gold)
    hits = sum(b[0] for b in buckets.values())
    return {
        "model": model,
        "overall": 100.0 * hits / total,
        "literal": (100.0 * buckets["literal"][0] / buckets["literal"][1]
                    if buckets["literal"][1] else float("nan")),
        "semantic": (100.0 * buckets["semantic"][0] / buckets["semantic"][1]
                     if buckets["semantic"][1] else float("nan")),
        "top5": 100.0 * top5 / total,
        "auto": 100.0 * auto / total,
        "acc_at_auto": (100.0 * auto_hits / auto) if auto else float("nan"),
        "ms_per_column": 1000.0 * elapsed / total,
        "misses": misses if verbose else misses[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--gold", type=Path, default=GOLD_SET)
    parser.add_argument("--verbose", action="store_true", help="list every miss")
    args = parser.parse_args()

    gold = load_gold(args.gold)
    kinds = {k: sum(1 for c in gold if c["kind"] == k) for k in {c["kind"] for c in gold}}
    print(f"Gold set: {len(gold)} cases from {args.gold.name} "
          f"({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})\n")

    results = []
    for model in args.models:
        print(f"--- {model}", flush=True)
        outcome = evaluate(model, gold, args.verbose)
        if outcome.get("error"):
            print(f"    unavailable: {outcome['error']}\n")
            continue
        results.append(outcome)
        print(f"    overall {outcome['overall']:.1f}%   literal {outcome['literal']:.1f}%"
              f"   semantic {outcome['semantic']:.1f}%   top5 {outcome['top5']:.1f}%"
              f"   auto {outcome['auto']:.1f}%   acc@auto {outcome['acc_at_auto']:.1f}%"
              f"   {outcome['ms_per_column']:.0f} ms/col")
        for name, got in outcome["misses"]:
            print(f"      miss {name:28} -> {got}")
        print(flush=True)

    if not results:
        return 1

    baseline = next((r for r in results if r["model"] == "none"), None)
    print("\nSummary  (literal% is the number to choose a model on)")
    print(f"  {'model':40} {'overall':>8} {'literal':>8} {'semantic':>9} "
          f"{'top5':>7} {'acc@auto':>9} {'ms/col':>8}")
    print(f"  {'-' * 40} {'-' * 8} {'-' * 8} {'-' * 9} {'-' * 7} {'-' * 9} {'-' * 8}")
    for r in results:
        delta = "" if not baseline or r is baseline else \
            f"  ({r['literal'] - baseline['literal']:+.1f} literal vs none)"
        print(f"  {r['model'][:40]:40} {r['overall']:7.1f}% {r['literal']:7.1f}% "
              f"{r['semantic']:8.1f}% {r['top5']:6.1f}% {r['acc_at_auto']:8.1f}% "
              f"{r['ms_per_column']:7.0f}{delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
