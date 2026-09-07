"""Measure Stage 5 embedding models in isolation, as pure rerankers.

    .venv/bin/python benchmarks/stage5_rerank.py

compare_models.py measures the whole pipeline, where Stage 5 carries 20% of the
composite and can only shift a decision when the leaders are near-tied. That
answers "does swapping the model change the output" but not "is the model any
good", and the two questions have different answers.

This script isolates the model. For each gold case it takes the *real* Stage 2
shortlist — the exact candidate set Stage 5 would receive — and asks each model
to rank it by cosine similarity alone. The comparison points are:

    lexical   the Stage 2 ordering as it stands, i.e. what the pipeline would
              do with Stage 5 removed entirely
    <model>   ranking by embedding cosine only

Cases where no acceptable concept is in the shortlist at all are excluded and
counted separately: no reranker can retrieve what retrieval did not return, and
leaving them in would just add a constant to every row.

Metrics
    top1   an acceptable concept is ranked first
    mrr    mean reciprocal rank of the first acceptable concept
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.compare_models import GOLD_SET, accepts, load_gold   # noqa: E402
from pipeline.config import EMBEDDING_MODEL_PRESETS, PipelineConfig  # noqa: E402
from pipeline.db import Database                                     # noqa: E402
from pipeline.stage0_preprocess import Preprocessor                  # noqa: E402
from pipeline.stage2_fts import FullTextSearcher                     # noqa: E402
from pipeline.stage5_embeddings import EmbeddingModel                # noqa: E402

MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "NeuML/pubmedbert-base-embeddings",
    "abhinand/MedEmbed-small-v0.1",
]


def build_shortlists(gold: list[dict], config: PipelineConfig) -> list[dict]:
    """The real Stage 2 candidate set for each gold case."""
    db = Database(config)
    pre = Preprocessor(config)
    fts = FullTextSearcher(config, db)

    cases = []
    for case in gold:
        ctx = pre.run(case["name"], case["description"])
        candidates = fts.run(ctx, ctx.variants[0])
        names = [c.concept_name for c in candidates]
        if not names:
            continue
        cases.append({
            "case": case,
            "query": " ".join(t.replace("_", " ") for t in ctx.tokens),
            "names": names,
            "acceptable": [accepts(case, n) for n in names],
        })
    db.close()
    return cases


def score_ranking(order: list[int], acceptable: list[bool]) -> tuple[int, float]:
    """(top-1 hit, reciprocal rank) for one ranked candidate ordering."""
    for position, index in enumerate(order, start=1):
        if acceptable[index]:
            return (1 if position == 1 else 0), 1.0 / position
    return 0, 0.0


def evaluate_model(model_name: str, cases: list[dict]) -> dict | None:
    preset = EMBEDDING_MODEL_PRESETS.get(model_name, {})
    model = EmbeddingModel(model_name,
                           query_prefix=preset.get("query_prefix", ""),
                           document_prefix=preset.get("document_prefix", ""))
    if not model.available:
        print(f"    unavailable: {model.unavailable_reason}")
        return None

    model.encode(["warmup"])
    hits = 0
    rr = 0.0
    started = time.perf_counter()

    for entry in cases:
        query = model.encode([entry["query"]], is_query=True)
        documents = model.encode(entry["names"])
        if query is None or documents is None:
            continue
        similarity = documents @ query[0]
        order = list(np.argsort(-similarity))
        hit, reciprocal = score_ranking(order, entry["acceptable"])
        hits += hit
        rr += reciprocal

    elapsed = time.perf_counter() - started
    total = len(cases)
    return {
        "model": model_name,
        "top1": 100.0 * hits / total,
        "mrr": rr / total,
        "ms_per_case": 1000.0 * elapsed / total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=MODELS)
    parser.add_argument("--gold", type=Path, default=GOLD_SET)
    args = parser.parse_args()

    config = PipelineConfig.load()
    config.log_to_db = False

    gold = load_gold(args.gold)
    print(f"Building Stage 2 shortlists for {len(gold)} gold cases...")
    cases = build_shortlists(gold, config)

    winnable = [c for c in cases if any(c["acceptable"])]
    skipped = len(cases) - len(winnable)
    print(f"  {len(winnable)} winnable, {skipped} excluded "
          f"(no acceptable concept in the shortlist — unwinnable by any reranker)")
    print(f"  mean shortlist size: "
          f"{sum(len(c['names']) for c in winnable) / max(len(winnable), 1):.1f}\n")

    results = []
    lexical_hits = sum(score_ranking(list(range(len(c["names"]))), c["acceptable"])[0]
                       for c in winnable)
    lexical_rr = sum(score_ranking(list(range(len(c["names"]))), c["acceptable"])[1]
                     for c in winnable)
    results.append({
        "model": "lexical (Stage 2 order, no embeddings)",
        "top1": 100.0 * lexical_hits / len(winnable),
        "mrr": lexical_rr / len(winnable),
        "ms_per_case": 0.0,
    })

    for model_name in args.models:
        print(f"--- {model_name}", flush=True)
        outcome = evaluate_model(model_name, winnable)
        if outcome:
            results.append(outcome)
            print(f"    top1 {outcome['top1']:.1f}%   mrr {outcome['mrr']:.3f}   "
                  f"{outcome['ms_per_case']:.0f} ms/case")
        print(flush=True)

    print("\nReranking the Stage 2 shortlist, embeddings only")
    print(f"  {'ranker':44} {'top1':>7} {'mrr':>7} {'ms/case':>9}")
    print(f"  {'-' * 44} {'-' * 7} {'-' * 7} {'-' * 9}")
    for r in results:
        print(f"  {r['model'][:44]:44} {r['top1']:6.1f}% {r['mrr']:7.3f} "
              f"{r['ms_per_case']:8.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
