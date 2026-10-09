"""Compare value-level matching against a human-curated value mapping.

    .venv/bin/python benchmarks/compare_value_mappings.py
    .venv/bin/python benchmarks/compare_value_mappings.py --context name --no-yes-no
    .venv/bin/python benchmarks/compare_value_mappings.py --out benchmarks/value_mappings/comparison.csv

compare_user_mappings.py grades what a COLUMN maps to; this grades what each of
a column's VALUES maps to ("Prednison" -> prednisone, "Yes" -> Joint swelling).
It replays the strategy the ETL Auto-Designer's /match-values endpoint runs on
top of this pipeline, through the same MatchService the HTTP endpoint uses, so
a number here is what a user of that app sees:

  1. yes/no columns — every value is yes/no/missing-marker, at least one yes —
     match the COLUMN (name + description) once, restricted to the five stem
     domains; "Yes" values take that answer and "No" values become concept 0
     (deliberately not mapped). Off with --no-yes-no.
  2. every other column: pass 1 matches each value unrestricted, the domains of
     the picks are counted, the most frequent stem domain wins; pass 2 re-matches
     the values outside it restricted to it. Shortlists outside the winning
     domain are dropped.

--context decides what rides along with each value in step 2: nothing (the
shipped behaviour), the column name, or the column description (falling back to
the name). Measured, both kinds of context make values worse — see the README.

Each graded value falls in one bucket, mirroring what the app does with it:

  set_right        committed automatically (auto_accept) and equal to the human's
  offered          held for review, the human's concept is in the shortlist
  review_miss      held for review, the human's concept is not in the shortlist
  unmapped         nothing offered
  set_wrong        committed automatically to another concept
  zero_wrong       set to 0 (not mapped) where the human chose a concept

Values are split as compare_user_mappings.py splits columns: reachable (the
human's concept is in this database), custom/absent (no setting can find it;
only the harmful buckets mean anything there), and "not mapped" (the human
chose 0).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_user_mappings import classify_targets      # noqa: E402
from pipeline.config import PipelineConfig              # noqa: E402
from pipeline.service import MatchService               # noqa: E402

GOLD = Path(__file__).resolve().parent / "value_mappings" / "value_gold.csv"

# Kept identical to the app (backend/app/api/concept_mapping.py).
STEM_DOMAINS = ["Measurement", "Observation", "Drug", "Procedure", "Condition"]
AFFIRMATIVE = {"yes", "y", "true", "present"}
NEGATIVE = {"no", "n", "false", "absent"}
MISSING = {"unknown", "unk", "not known", "don't know", "dont know",
           "n/a", "na", "missing", "not applicable"}

BUCKETS = ["set_right", "offered", "review_miss", "unmapped", "set_wrong", "zero_wrong"]


def load_gold(path: Path) -> dict[str, dict]:
    """{variable: {description, values: [(value, user_concept_id | None, name)]}}"""
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    columns: dict[str, dict] = {}
    for row in csv.DictReader(lines):
        col = columns.setdefault(row["variable"], {"description": row["description"], "values": []})
        raw = row["user_concept_id"].strip()
        col["values"].append((row["value"], int(raw) if raw else None, row["user_concept_name"]))
    return columns


def yes_no_split(values: list[str]):
    yes, no, missing = [], [], []
    for value in values:
        key = value.strip().lower()
        if key in AFFIRMATIVE:
            yes.append(value)
        elif key in NEGATIVE:
            no.append(value)
        elif key in MISSING:
            missing.append(value)
        else:
            return None
    return (yes, no, missing) if yes else None


def fixed(concept_id: int | None) -> dict:
    return {"status": "auto_accept" if concept_id is not None else "unmapped",
            "concept_id": concept_id, "concept_name": "Not mapped" if concept_id == 0 else None,
            "domain_id": None, "confidence": 100.0 if concept_id is not None else 0.0,
            "candidates": []}


def match_column(service: MatchService, name: str, description: str, values: list[str],
                 context: str, yes_no: bool) -> tuple[dict[str, dict], str | None, bool]:
    """(results by value, column domain, was it handled as yes/no)."""
    split = yes_no_split(values) if yes_no else None
    if split:
        yes, no, missing = split
        found = service.match([{"name": name, "description": description or None}],
                              "benchmark", "value benchmark", 5, STEM_DOMAINS)["results"][0]
        results = {v: found for v in yes}
        results.update({v: fixed(0) for v in no})
        results.update({v: fixed(None) for v in missing})
        return results, found.get("domain_id") if found.get("concept_id") else None, True

    ctx = {"none": None, "name": name, "description": description or name}[context]

    def one_pass(vals: list[str], domains: list[str] | None) -> list[dict]:
        cols = [{"name": v, "description": ctx} if ctx else {"name": v} for v in vals]
        return service.match(cols, "benchmark", "value benchmark", 5, domains)["results"]

    first = one_pass(values, None)
    domain_of = lambda r: r.get("domain_id") if r.get("concept_id") and not r.get("error") else None
    votes = Counter(d for d in map(domain_of, first) if d)
    stem = Counter({d: n for d, n in votes.items() if d in STEM_DOMAINS})
    winner = (stem or votes).most_common(1)[0][0] if votes else None
    results = dict(zip(values, first))
    if winner:
        stale = [v for v, r in results.items() if domain_of(r) != winner]
        if stale:
            results.update(zip(stale, one_pass(stale, [winner])))
        for r in results.values():
            r["candidates"] = [c for c in r.get("candidates") or [] if c.get("domain_id") == winner]
    return results, winner, False


def bucket(r: dict, gold: int) -> str:
    status, cid = r["status"], r["concept_id"]
    if cid == 0 and status == "auto_accept":
        return "set_right" if gold == 0 else "zero_wrong"
    if cid and status not in ("review", "unmapped"):
        return "set_right" if cid == gold else "set_wrong"
    if status == "review":
        return "offered" if any(c["concept_id"] == gold for c in r["candidates"]) else "review_miss"
    return "unmapped"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--context", choices=["none", "name", "description"], default="none")
    parser.add_argument("--no-yes-no", action="store_true")
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--limit", type=int, help="first N columns only")
    args = parser.parse_args()

    config = PipelineConfig.load()
    config.log_to_db = False
    if args.no_embeddings:
        config.embeddings_enabled = False

    gold = load_gold(args.gold)
    if args.limit:
        gold = dict(list(gold.items())[: args.limit])

    service = MatchService(config)
    targets = [{"user_concept_id": cid, "user_concept_name": cname}
               for col in gold.values() for _, cid, cname in col["values"] if cid]
    classify_targets(service.batcher().matcher.db, targets)
    status_of = {t["user_concept_id"]: t["target_status"] for t in targets}

    rows: list[dict] = []
    domain_hits = Counter()
    started = time.perf_counter()
    for index, (name, col) in enumerate(gold.items(), start=1):
        values = [v for v, _, _ in col["values"]]
        results, domain, as_yes_no = match_column(
            service, name, col["description"], values, args.context, not args.no_yes_no)
        for value, cid, cname in col["values"]:
            if cid is None:
                continue
            r = results[value]
            group = "not_mapped" if cid == 0 else status_of.get(cid, "absent")
            rows.append({
                "variable": name, "value": value, "yes_no": as_yes_no, "column_domain": domain,
                "group": group, "user_concept_id": cid, "user_concept_name": cname,
                "picked_concept_id": r.get("concept_id"), "picked_concept_name": r.get("concept_name") or "",
                "status": r["status"], "confidence": round(r.get("confidence") or 0, 1),
                "bucket": bucket(r, cid),
                "top5": " | ".join(f"{c['concept_id']}:{c['concept_name'][:40]}"
                                   for c in r.get("candidates") or []),
            })
        print(f"  {index}/{len(gold)}", end="\r", flush=True)
    elapsed = time.perf_counter() - started
    service.close()

    # ── report ──────────────────────────────────────────────────────────
    n_values = sum(len(c["values"]) for c in gold.values())
    yes_no_cols = len({r["variable"] for r in rows if r["yes_no"]})
    print(f"\n{len(gold)} columns, {n_values} values ({len(rows)} graded) in {elapsed:.0f}s"
          f" — context={args.context}, yes/no handling {'off' if args.no_yes_no else 'on'}"
          f" ({yes_no_cols} yes/no columns)\n")

    groups = defaultdict(Counter)
    for r in rows:
        groups[r["group"]][r["bucket"]] += 1
    titles = {"reachable": "human's concept is in this database",
              "not_mapped": "human chose 0 (not mapped)",
              "custom": "human's concept is custom (id >= 2e9)",
              "absent": "human's concept is absent from this database"}
    print(f"{'':14}" + "".join(f"{b:>13}" for b in BUCKETS) + f"{'total':>8}")
    for group in ["reachable", "not_mapped", "custom", "absent"]:
        if groups[group]:
            c = groups[group]
            print(f"{group:14}" + "".join(f"{c[b]:13}" for b in BUCKETS) + f"{sum(c.values()):8}"
                  f"   {titles[group]}")

    reach = groups["reachable"]
    if reach:
        total = sum(reach.values())
        useful = reach["set_right"] + reach["offered"]
        print(f"\nReachable: right or one click away {useful}/{total} ({100 * useful / total:.1f}%),"
              f" wrong and committed {reach['set_wrong'] + reach['zero_wrong']}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["variable"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
