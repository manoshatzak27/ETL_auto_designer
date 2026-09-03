"""Compare Stage 5 embedding models against the semantic gold set.

    .venv/bin/python benchmarks/compare_models.py
    .venv/bin/python benchmarks/compare_models.py --models none NeuML/pubmedbert-base-embeddings

Why this exists: swapping the embedding model is a one-line config change, but
whether it *helps* is an empirical question, and Stage 5 carries only 20% of the
composite score. Most of the gold set is decided by Stages 2-4 regardless of
model, so a raw accuracy number understates the difference. The `none` baseline
(embeddings disabled, weight redistributed) is therefore always run first, and
what matters is the delta from it.

The gold set is deliberately made of paraphrases and lay terms — "sugar level"
for glucose, "brain attack" for stroke — because a column whose description
contains the concept name exits at Stage 1 and never reaches Stage 5 at all.

Grading is hierarchy-aware, and it has to be. Naming one "correct" concept_id
per column is not possible in OMOP: "Glucose measurement", "Glucose
measurement, blood" and "Glucose measurement, serum" are all defensible answers
for a column called `sugar_lvl`, and demanding exactly one scored a working
pipeline at 2%. So an answer counts when it is the expected concept, a
descendant of it within two levels, or its immediate parent — resolved through
`vocab.concept_ancestor`. Strict exact-id accuracy is reported alongside, since
the gap between the two is itself informative.

Metrics
    top1    selected concept is accepted (hierarchy-aware)
    strict  selected concept is one of the literal expected ids
    top5    an accepted concept appears in the top five candidates
    auto    share auto-accepted (>=90) — high with low top1 means confidently wrong
    acc@auto  top-1 accuracy restricted to auto-accepted rows; the number that
              decides whether auto-accept can be trusted without review
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
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
    rows = []
    for row in csv.DictReader(path.read_text(encoding="utf-8").splitlines()):
        rows.append({
            "name": row["name"],
            "description": row["description"],
            "expected": {int(c) for c in row["expected"].split(";") if c.strip()},
            "rationale": row.get("rationale", ""),
        })
    return rows


def expand_accepted(db, expected: set[int],
                    descendant_depth: int = 2) -> set[int]:
    """Widen an expected id into the family of defensible answers.

    Descendants (more specific: "Glucose measurement, blood") are accepted to
    two levels; the immediate parent is accepted as well, since choosing one
    step more general is a reasonable reading of an underspecified column.
    Anything further afield is a genuine miss.
    """
    ids = sorted(expected)
    rows = db.fetch_all(
        """
        SELECT DISTINCT descendant_concept_id AS concept_id
        FROM vocab.concept_ancestor
        WHERE ancestor_concept_id = ANY(%(ids)s)
          AND min_levels_of_separation <= %(depth)s
        UNION
        SELECT DISTINCT ancestor_concept_id
        FROM vocab.concept_ancestor
        WHERE descendant_concept_id = ANY(%(ids)s)
          AND min_levels_of_separation = 1
        """,
        {"ids": ids, "depth": descendant_depth},
    )
    return expected | {r["concept_id"] for r in rows}


def evaluate(model: str, gold: list[dict], verbose: bool) -> dict:
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

    matcher = ConceptMatcher(config)
    if config.embeddings_enabled and not matcher.stage5.enabled:
        matcher.close()
        return {"model": model, "error": matcher.stage5.model.unavailable_reason}

    if config.embeddings_enabled:
        matcher.stage5.model.encode(["warmup"])     # keep load out of the timing

    # Cached on the case dicts so the expansion is paid once, not once per model.
    for case in gold:
        if "accepted" not in case:
            case["accepted"] = expand_accepted(matcher.db, case["expected"])

    hits = strict_hits = top5 = auto = auto_hits = 0
    misses: list[tuple[str, str, str]] = []
    started = time.perf_counter()

    for case in gold:
        result = matcher.match(case["name"], case["description"], log_result=False)
        chosen = result.concept_id
        correct = chosen in case["accepted"]
        in_top5 = any(c.concept_id in case["accepted"] for c in result.candidates[:5])

        hits += correct
        strict_hits += chosen in case["expected"]
        top5 += in_top5
        if result.status == "auto_accept":
            auto += 1
            auto_hits += correct
        if not correct:
            misses.append((
                case["name"],
                f"{chosen} {result.selected.concept_name[:44] if result.selected else '(none)'}",
                f"want {sorted(case['expected'])}",
            ))

    elapsed = time.perf_counter() - started
    matcher.close()

    total = len(gold)
    return {
        "model": model,
        "top1": 100.0 * hits / total,
        "strict": 100.0 * strict_hits / total,
        "top5": 100.0 * top5 / total,
        "auto": 100.0 * auto / total,
        "acc_at_auto": (100.0 * auto_hits / auto) if auto else float("nan"),
        "ms_per_column": 1000.0 * elapsed / total,
        "misses": misses if verbose else misses[:6],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="'none' disables Stage 5; anything else is a "
                             "sentence-transformers model id")
    parser.add_argument("--gold", type=Path, default=GOLD_SET)
    parser.add_argument("--verbose", action="store_true", help="list every miss")
    args = parser.parse_args()

    gold = load_gold(args.gold)
    print(f"Gold set: {len(gold)} semantic cases from {args.gold.name}\n")

    results = []
    for model in args.models:
        print(f"--- {model}", flush=True)
        outcome = evaluate(model, gold, args.verbose)
        if outcome.get("error"):
            print(f"    unavailable: {outcome['error']}\n")
            continue
        results.append(outcome)
        print(f"    top1 {outcome['top1']:.1f}%   strict {outcome['strict']:.1f}%   "
              f"top5 {outcome['top5']:.1f}%   auto {outcome['auto']:.1f}%   "
              f"acc@auto {outcome['acc_at_auto']:.1f}%   "
              f"{outcome['ms_per_column']:.0f} ms/col")
        for name, got, want in outcome["misses"]:
            print(f"      miss {name:24} got {got:48} {want}")
        print(flush=True)

    if not results:
        return 1

    baseline = next((r for r in results if r["model"] == "none"), None)
    print("\nSummary")
    print(f"  {'model':44} {'top1':>7} {'strict':>7} {'top5':>7} {'auto':>7} "
          f"{'acc@auto':>9} {'ms/col':>8}")
    print(f"  {'-' * 44} {'-' * 7} {'-' * 7} {'-' * 7} {'-' * 7} {'-' * 9} {'-' * 8}")
    for r in results:
        delta = "" if not baseline or r is baseline else \
            f"  ({r['top1'] - baseline['top1']:+.1f} vs no embeddings)"
        print(f"  {r['model'][:44]:44} {r['top1']:6.1f}% {r['strict']:6.1f}% "
              f"{r['top5']:6.1f}% {r['auto']:6.1f}% {r['acc_at_auto']:8.1f}% "
              f"{r['ms_per_column']:7.0f}{delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
