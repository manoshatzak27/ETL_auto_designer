"""How well the pipeline survives misspelt input.

    .venv/bin/python benchmarks/compare_typos.py
    .venv/bin/python benchmarks/compare_typos.py --source drug --verbose
    .venv/bin/python benchmarks/compare_typos.py --no-embeddings --domain Drug --source drug

Runs every case of typo_set.csv twice — once with the clean description, once
misspelt — and reports, per typo type:

  top1       the selected concept passes the name rule
  top5       a passing concept is among the first five candidates
  retained   of the cases the CLEAN text gets right at top-1, the share the
             misspelt text still gets right. The number to judge a typo fix on:
             it does not reward or punish cases that were wrong to begin with.
  found      a passing concept was selected AND the decision is not unmapped —
             i.e. the user would actually be handed it, at least for review
  unmapped   decision was unmapped, whatever the candidates were
  wrong@auto auto-accepted a concept that fails the name rule. The cost side of
             any typo tolerance: a looser match that starts auto-accepting the
             wrong drug is worse than one that reports unmapped.

Only the description is passed, never the column name. gold_set.csv's names
("creat_serum") would otherwise rescue a misspelt description through the
identifier path and hide exactly what this measures. The clean row is
therefore NOT the gold-set number — it is the baseline for this benchmark only.

Grading is gold_set.csv's name rule; regenerate the cases with
make_typo_set.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_models import accepts                       # noqa: E402
from pipeline.config import PipelineConfig               # noqa: E402
from pipeline.runner import ConceptMatcher               # noqa: E402

TYPO_SET = Path(__file__).resolve().parent / "typo_set.csv"


def _terms(text: str) -> list[str]:
    return [t.strip().lower() for t in (text or "").split(";") if t.strip()]


def load_typos(path: Path) -> list[dict]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    rows = []
    for row in csv.DictReader(lines):
        rows.append({**row, "require": _terms(row["require"]),
                     "forbid": _terms(row["forbid"])})
    return rows


def grade(matcher: ConceptMatcher, case: dict, description: str) -> dict:
    result = matcher.match("", description, log_result=False)
    selected = result.selected
    correct = accepts(case, selected.concept_name if selected else None)
    return {
        "correct": correct,
        "top5": any(accepts(case, c.concept_name) for c in result.candidates[:5]),
        "status": result.status,
        "got": selected.concept_name if selected else "(none)",
        "confidence": result.confidence,
    }


class Tally:
    def __init__(self) -> None:
        self.n = self.top1 = self.top5 = self.found = 0
        self.unmapped = self.wrong_auto = 0
        self.retained = self.retainable = 0

    def add(self, outcome: dict, clean: dict | None) -> None:
        self.n += 1
        self.top1 += outcome["correct"]
        self.top5 += outcome["top5"]
        self.found += outcome["correct"] and outcome["status"] != "unmapped"
        self.unmapped += outcome["status"] == "unmapped"
        self.wrong_auto += outcome["status"] == "auto_accept" and not outcome["correct"]
        if clean is not None and clean["correct"]:
            self.retainable += 1
            self.retained += outcome["correct"]

    def row(self, label: str) -> str:
        pct = lambda k: 100.0 * k / self.n if self.n else float("nan")
        kept = (f"{100.0 * self.retained / self.retainable:7.1f}%"
                if self.retainable else f"{'—':>8}")
        return (f"  {label:12} {self.n:4} {pct(self.top1):7.1f}% {pct(self.top5):7.1f}% "
                f"{kept} {pct(self.found):7.1f}% {self.unmapped:9} {self.wrong_auto:10}")


HEADER = (f"  {'':12} {'n':>4} {'top1':>8} {'top5':>8} {'retained':>8} "
          f"{'found':>8} {'unmapped':>9} {'wrong@auto':>10}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--typos", type=Path, default=TYPO_SET)
    parser.add_argument("--source", choices=("lab", "drug"), help="only this source")
    parser.add_argument("--types", nargs="+", help="only these typo types")
    parser.add_argument("--domain", nargs="+", default=[],
                        help="restrict matching to these OMOP domains (as --domain)")
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--verbose", action="store_true",
                        help="list every typo the clean text got right and the typo lost")
    args = parser.parse_args()

    rows = load_typos(args.typos)
    if args.source:
        rows = [r for r in rows if r["source"] == args.source]
    if args.types:
        rows = [r for r in rows if r["typo_type"] in args.types]
    if not rows:
        print("no cases selected")
        return 1

    config = PipelineConfig.load()
    config.log_to_db = False
    if args.no_embeddings:
        config.embeddings_enabled = False
    matcher = ConceptMatcher(config)
    if args.domain:
        config.restrict_domains = tuple(matcher.db.resolve_domains(args.domain))
    if config.embeddings_enabled and matcher.stage5.enabled:
        matcher.stage5.model.encode(["warmup"])     # keep the load out of timing

    cases = {r["case"]: r for r in rows}
    print(f"Typo set: {len(rows)} misspellings of {len(cases)} cases "
          f"({args.typos.name}); embeddings "
          f"{'on' if config.embeddings_enabled and matcher.stage5.enabled else 'off'}"
          f"{'; restricted to ' + ', '.join(config.restrict_domains) if config.restrict_domains else ''}\n",
          flush=True)

    started = time.perf_counter()
    clean: dict[str, dict] = {}
    clean_tally: dict[str, Tally] = defaultdict(Tally)
    for case_id, case in cases.items():
        clean[case_id] = grade(matcher, case, case["clean_description"])
        clean_tally[case["source"]].add(clean[case_id], None)
        clean_tally["all"].add(clean[case_id], None)

    by_type: dict[str, Tally] = defaultdict(Tally)
    by_source: dict[str, Tally] = defaultdict(Tally)
    losses: list[str] = []
    for row in rows:
        outcome = grade(matcher, row, row["description"])
        base = clean[row["case"]]
        for tally in (by_type[row["typo_type"]], by_source[row["source"]], by_type["all typos"]):
            tally.add(outcome, base)
        if base["correct"] and not outcome["correct"]:
            losses.append(f"    {row['typo_type']:10} {row['typo']:22} "
                          f"[{outcome['status']}/{outcome['confidence']:.1f}] -> {outcome['got'][:60]}")
    elapsed = time.perf_counter() - started
    matcher.close()

    print("Clean descriptions (the baseline)")
    print(HEADER)
    for label in sorted(k for k in clean_tally if k != "all") + ["all"]:
        print(clean_tally[label].row(label))

    print("\nMisspelt, by typo type")
    print(HEADER)
    for label in sorted(k for k in by_type if k != "all typos") + ["all typos"]:
        print(by_type[label].row(label))

    print("\nMisspelt, by source")
    print(HEADER)
    for label in sorted(by_source):
        print(by_source[label].row(label))

    total = len(rows) + len(cases)
    print(f"\n{total} queries in {elapsed:.0f}s ({1000.0 * elapsed / total:.0f} ms each)")

    if losses:
        shown = losses if args.verbose else losses[:15]
        print(f"\nLost to the typo ({len(losses)}; clean text right, misspelt wrong):")
        print("\n".join(shown))
        if len(shown) < len(losses):
            print(f"    ... {len(losses) - len(shown)} more; --verbose lists all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
