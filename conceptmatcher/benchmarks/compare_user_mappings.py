"""Compare the pipeline's choices against a human-curated mapping file.

    .venv/bin/python benchmarks/compare_user_mappings.py \
        --gold benchmarks/user_mappings/user_gold.csv \
        --out  benchmarks/user_mappings/comparison.csv

Unlike gold_set.csv (which grades by name rule because there is no single right
concept), this grades by concept_id: the file holds decisions a person actually
made, so "did the pipeline pick the same id" is the exact question.

Three things have to be separated before any agreement number means anything:

* **custom concepts** — ids >= 2,000,000,000 are the OMOP-reserved range for
  locally-defined concepts. They exist only in this project's own
  custom_mappings.csv, not in Athena, so the pipeline cannot return them at any
  accuracy. They are excluded from the rate and counted on their own.
* **absent concepts** — a chosen id that is not in `vocab.concept` at all.
  Either it is non-standard or invalid (and so was filtered out of this
  database by design), or it is not in this Athena release. Also unreachable,
  also excluded, and worth reporting back because it means the mapping cannot
  be loaded into a standard-concept-only CDM either.
* **reachable concepts** — everything left. This is the only subset where
  agreement or disagreement is informative.

For the reachable subset it reports exact agreement, whether the human's choice
appeared anywhere in the pipeline's top-5, and how the two concepts relate in
the vocabulary hierarchy (parent/child/sibling), because a disagreement between
"Body weight" and "Body weight measured" is a different kind of event from a
disagreement between "Body weight" and "Heavy chain disease".
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import PipelineConfig      # noqa: E402
from pipeline.db import Database                # noqa: E402
from pipeline.runner import ConceptMatcher      # noqa: E402

CUSTOM_ID_FLOOR = 2_000_000_000


def load_gold(path: Path) -> list[dict]:
    rows = []
    for row in csv.DictReader(path.read_text(encoding="utf-8").splitlines()):
        if not (row.get("variable") or "").strip():
            continue
        rows.append({
            "variable": row["variable"].strip(),
            "description": (row.get("description") or "").strip(),
            "user_concept_id": int(row["user_concept_id"]),
            "user_concept_name": (row.get("user_concept_name") or "").strip(),
        })
    return rows


def classify_targets(db: Database, rows: list[dict]) -> None:
    """Tag every row reachable / custom / absent, and fetch the real name."""
    ids = sorted({r["user_concept_id"] for r in rows
                  if r["user_concept_id"] < CUSTOM_ID_FLOOR})
    present = {}
    if ids:
        for row in db.fetch_all(
            """
            SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id
            FROM vocab.concept WHERE concept_id = ANY(%s)
              AND standard_concept = 'S' AND invalid_reason IS NULL
            """,
            (ids,),
        ):
            present[row["concept_id"]] = row

    for r in rows:
        cid = r["user_concept_id"]
        if cid >= CUSTOM_ID_FLOOR:
            r["target_status"] = "custom"
            r["target_vocab"] = "CUSTOM"
            r["target_class"] = ""
        elif cid in present:
            r["target_status"] = "reachable"
            r["target_vocab"] = present[cid]["vocabulary_id"]
            r["target_class"] = present[cid]["concept_class_id"]
            r["user_concept_name"] = present[cid]["concept_name"]
        else:
            r["target_status"] = "absent"
            r["target_vocab"] = ""
            r["target_class"] = ""


def relation(db: Database, a: int | None, b: int) -> str:
    """How the pipeline's pick relates to the human's, in the hierarchy."""
    if a is None or a == b:
        return ""
    row = db.fetch_one(
        """
        SELECT
          (SELECT min_levels_of_separation FROM vocab.concept_ancestor
            WHERE ancestor_concept_id = %(a)s AND descendant_concept_id = %(b)s) AS a_above_b,
          (SELECT min_levels_of_separation FROM vocab.concept_ancestor
            WHERE ancestor_concept_id = %(b)s AND descendant_concept_id = %(a)s) AS b_above_a,
          EXISTS (
            SELECT 1 FROM vocab.concept_ancestor x
            JOIN vocab.concept_ancestor y ON y.ancestor_concept_id = x.ancestor_concept_id
            WHERE x.descendant_concept_id = %(a)s AND y.descendant_concept_id = %(b)s
              AND x.min_levels_of_separation = 1 AND y.min_levels_of_separation = 1
          ) AS sibling
        """,
        {"a": a, "b": b},
    )
    if not row:
        return "unrelated"
    if row["a_above_b"] is not None:
        return f"picked is ancestor (+{row['a_above_b']})"
    if row["b_above_a"] is not None:
        return f"picked is descendant (-{row['b_above_a']})"
    if row["sibling"]:
        return "sibling"
    return "unrelated"


def name_overlap(left: str, right: str) -> float:
    """Trigram similarity between two concept names.

    Used to separate a genuine miss from an instrument variant. LOINC carries
    the same questionnaire item once per instrument — "Are you able to shampoo
    your hair [PROMIS]" and "Over the past week, are you able to shampoo your
    hair [HAQ]" are different concept_ids for the same question. Picking the
    other instrument's copy is a curation disagreement, not a failure to
    understand the column, and lumping the two together hides both.
    """
    from pipeline import text_utils

    return text_utils.trigram_similarity(left, right)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path,
                        default=Path(__file__).resolve().parent / "user_mappings" / "user_gold.csv")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    config = PipelineConfig.load()
    config.log_to_db = False
    if args.no_embeddings:
        config.embeddings_enabled = False

    rows = load_gold(args.gold)
    if args.limit:
        rows = rows[: args.limit]

    matcher = ConceptMatcher(config)
    classify_targets(matcher.db, rows)
    if config.embeddings_enabled and matcher.stage5.enabled:
        matcher.stage5.model.encode(["warmup"])

    started = time.perf_counter()
    for index, r in enumerate(rows, start=1):
        result = matcher.match(r["variable"], r["description"], log_result=False)
        picked = result.selected
        r["picked_concept_id"] = picked.concept_id if picked else None
        r["picked_concept_name"] = picked.concept_name if picked else ""
        r["picked_vocab"] = picked.vocabulary_id if picked else ""
        r["picked_class"] = picked.concept_class_id if picked else ""
        r["status"] = result.status
        r["confidence"] = round(result.confidence, 1)
        r["agree"] = (picked is not None
                      and picked.concept_id == r["user_concept_id"])
        r["user_in_top5"] = any(c.concept_id == r["user_concept_id"]
                                for c in result.candidates[:5])
        r["top5"] = " | ".join(f"{c.concept_id}:{c.concept_name[:40]}"
                               for c in result.candidates[:5])
        if index % 20 == 0:
            print(f"  {index}/{len(rows)}", end="\r", flush=True)

    for r in rows:
        r["relation"] = ("" if r["agree"] or r["target_status"] != "reachable"
                         else relation(matcher.db, r["picked_concept_id"],
                                       r["user_concept_id"]))
        r["name_similarity"] = round(
            name_overlap(r["picked_concept_name"], r["user_concept_name"]), 3)
        # Same question, different LOINC instrument copy.
        r["same_question"] = (not r["agree"]
                              and r["target_status"] == "reachable"
                              and r["name_similarity"] >= 0.45)
    elapsed = time.perf_counter() - started
    matcher.close()

    # ── report ──────────────────────────────────────────────────────────
    buckets = Counter(r["target_status"] for r in rows)
    reachable = [r for r in rows if r["target_status"] == "reachable"]
    agreed = [r for r in reachable if r["agree"]]
    top5 = [r for r in reachable if r["user_in_top5"]]

    print(f"\n{len(rows)} variables compared in {elapsed:.0f}s "
          f"({1000 * elapsed / max(len(rows), 1):.0f} ms each)\n")

    print("Can the pipeline reach the concept the human chose?")
    print(f"  reachable (standard + valid, in this DB) : {buckets['reachable']:4}")
    print(f"  custom concept (id >= 2e9, not in Athena): {buckets['custom']:4}")
    print(f"  absent from this DB (non-standard etc.)  : {buckets['absent']:4}")

    same_q = [r for r in reachable if r["same_question"]]

    if reachable:
        print(f"\nAgreement on the {len(reachable)} reachable variables")
        print(f"  exact same concept_id            : {len(agreed):4}"
              f"  ({100 * len(agreed) / len(reachable):.1f}%)")
        print(f"  + same question, other instrument: {len(same_q):4}"
              f"  ({100 * len(same_q) / len(reachable):.1f}%)")
        print(f"  = agreed on meaning              : {len(agreed) + len(same_q):4}"
              f"  ({100 * (len(agreed) + len(same_q)) / len(reachable):.1f}%)")
        print(f"  human's choice in pipeline top-5 : {len(top5):4}"
              f"  ({100 * len(top5) / len(reachable):.1f}%)")

        rel = Counter(r["relation"] for r in reachable if not r["agree"])
        if rel:
            print("\n  When they differ, how the two concepts relate:")
            for name, count in rel.most_common():
                print(f"    {name or '(none)':32} {count:4}")

        by_status = Counter(r["status"] for r in reachable)
        print("\n  Pipeline status on those variables:")
        for name, count in by_status.most_common():
            n_ok = sum(1 for r in reachable if r["status"] == name and r["agree"])
            print(f"    {name:12} {count:4}   agreed {n_ok:4} "
                  f"({100 * n_ok / count:.0f}%)")

    if buckets["absent"]:
        print("\nChosen concepts absent from this standard-only database:")
        for r in rows:
            if r["target_status"] == "absent":
                print(f"    {r['variable']:24} {r['user_concept_id']:>10} "
                      f"{r['user_concept_name'][:50]}")

    print("\nDisagreements on reachable concepts (pipeline vs human):")
    shown = 0
    for r in reachable:
        if r["agree"]:
            continue
        shown += 1
        print(f"  {r['variable']:24} [{r['status']}/{r['confidence']}] {r['relation']}"
              f"{'  SAME-QUESTION' if r['same_question'] else ''}")
        print(f"      pipeline {str(r['picked_concept_id']):>10}  "
              f"{r['picked_concept_name'][:62]}")
        print(f"      human    {r['user_concept_id']:>10}  "
              f"{r['user_concept_name'][:62]}"
              f"{'   <- in top5' if r['user_in_top5'] else ''}")
    if not shown:
        print("  (none)")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fields = ["variable", "description", "target_status",
                  "user_concept_id", "user_concept_name", "target_vocab", "target_class",
                  "picked_concept_id", "picked_concept_name", "picked_vocab", "picked_class",
                  "status", "confidence", "agree", "user_in_top5", "same_question",
                  "name_similarity", "relation", "top5"]
        with args.out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
