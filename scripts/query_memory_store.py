#!/usr/bin/env python3
"""Query the persistent memory store by free text or JSON system snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory import query_store  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-path", required=True, help="Persistent Chroma directory.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query-text", help="Free-text retrieval query.")
    group.add_argument("--query-json", help="JSON file with goal + current system metrics.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of hits to print.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = query_store(
        store_path=args.store_path,
        query_text=args.query_text,
        query_json=args.query_json,
        top_k=args.top_k,
    )
    print("Effective query:")
    print(result["query_text"])
    print("\nHits:")
    for idx, hit in enumerate(result["hits"], start=1):
        metadata = hit.get("metadata", {})
        print(
            f"{idx}. id={hit['id']} stage={hit['stage']} source_kind={metadata.get('source_kind')} "
            f"distance={hit['distance']:.6f} adjusted={hit['adjusted_distance']:.6f}"
        )
        print(f"   phase={metadata.get('phase')} iter={metadata.get('iteration_start')}..{metadata.get('iteration_end')}")
        print(f"   doc={str(hit.get('document') or '').strip()[:400]}")
        if hit.get("run_summary_text"):
            print(f"   run_summary={str(hit.get('run_summary_text') or '').strip()[:800]}")
        resolved = hit.get("resolved_target")
        if resolved:
            resolved_meta = resolved.get("metadata", {})
            print(
                f"   target_id={resolved.get('id')} "
                f"target_source_kind={resolved_meta.get('source_kind')} "
                f"target_collection={metadata.get('target_collection')}"
            )
            print(f"   target_doc={str(resolved.get('document') or '').strip()[:400]}")
            if resolved_meta.get("run_summary_text") and not hit.get("run_summary_text"):
                print(f"   target_run_summary={str(resolved_meta.get('run_summary_text') or '').strip()[:800]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
