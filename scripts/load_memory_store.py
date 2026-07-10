#!/usr/bin/env python3
"""Load redacted histories and summaries into the persistent memory store."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory import load_into_store  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Memory artifact files to ingest.")
    parser.add_argument("--store-path", required=True, help="Persistent Chroma directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = load_into_store(args.files, store_path=args.store_path)
    print(f"Loaded memory artifacts into: {args.store_path}")
    for collection_name, payload in result["collections"].items():
        print(f"- {collection_name}: upserted={payload['upserted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
