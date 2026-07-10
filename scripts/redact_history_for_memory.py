#!/usr/bin/env python3
"""Redact a completed history file into a memory-store-friendly artifact."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory import redact_history_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to the completed optimizer history JSON file.")
    parser.add_argument("--output", help="Optional output path for the redacted JSON.")
    parser.add_argument(
        "--keep-app-metrics",
        action="store_true",
        help="Keep application metrics instead of redacting them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None

    redacted = redact_history_file(
        input_path,
        output_path=output_path,
        keep_app_metrics=args.keep_app_metrics,
    )

    final_output = output_path or input_path.with_name(f"{input_path.stem}_redacted.json")
    print(f"Wrote redacted artifact: {final_output}")
    print(f"Run ID: {redacted['run_id']}")
    first_entry = redacted["first_history_entry"]
    print(f"First history entry: iter={first_entry['iteration']} phase={first_entry['phase']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
