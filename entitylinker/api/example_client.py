"""
Quick-start client for the EntityLinker API

Usage:
  python api/example_client.py -q "heart attack" -k 5 --reranker
  python api/example_client.py -q "complete blood count" -q "hypertension"

You can change the API URL with:
  set EL_API_URL=http://localhost:8000/api/conceptlink    (Windows PowerShell: $env:EL_API_URL="...")
"""

import os
import sys
import argparse
from typing import Any, Dict, List

import requests


API_URL = os.getenv("EL_API_URL", "http://127.0.0.1:8000/api/conceptlink")


def call_api(query: str, top_k: int = 5, use_reranker: bool = False, url: str = API_URL) -> List[Dict[str, Any]]:
    """POST a JSON body to /api/conceptlink and return the 'conceptlinks' list."""
    payload = {"query": query, "top_k": top_k, "use_reranker": use_reranker}
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "conceptlinks" not in data:
        raise ValueError(f"Unexpected response format: {data}")
    return data["conceptlinks"]


def print_results(query: str, links: List[Dict[str, Any]]) -> None:
    print(f"\nQuery: {query}")
    if not links:
        print("  (no results)")
        return

    for i, item in enumerate(links, start=1):
        name = item["concept_name"]
        code = item["concept_code"]
        cid  = item["concept_id"]
        dom  = item["domain"]
        vocab= item["vocabulary_id"]
        score= item["score"]
        just = item["justification"]

        print(f"  {i:>2}. {name}  [vocabulary:{vocab} | concept_code={code} | concept_id={cid} | domain_id={dom}]  score={score:.3f}")
        print(f"      ↳ {just}")


def main() -> None:
    p = argparse.ArgumentParser(description="Example client for EntityLinker API (no CSV).")
    p.add_argument("-q", "--query", action="append", help="Query term (can be used multiple times).")
    p.add_argument("-k", "--top-k", type=int, default=5, help="Top-k candidates to return (default: 5).")
    p.add_argument("--reranker", action="store_true", help="Use GPT-based reranking (requires OPENAI_API_KEY on server).")
    p.add_argument("--url", default=API_URL, help=f"API endpoint URL (default: {API_URL})")
    args = p.parse_args()

    queries = args.query or [
        "heart attack",
        "complete blood count",
        "peripheral blood karyotype",
    ]

    for q in queries:
        try:
            links = call_api(q, top_k=args.top_k, use_reranker=args.reranker, url=args.url)
            print_results(q, links)
        except requests.RequestException as e:
            print(f"[ERROR] Request failed for '{q}': {e}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
