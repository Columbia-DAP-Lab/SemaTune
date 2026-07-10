#!/usr/bin/env python3
"""Summarize a redacted history file with Gemini 2.5 Flash Lite."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory import summarize_redacted_history  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to a *_redacted.json file.")
    parser.add_argument("--output-json", help="Optional output path for the JSON summary.")
    parser.add_argument("--output-text", help="Optional output path for the text summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_json = Path(args.output_json) if args.output_json else None
    output_text = Path(args.output_text) if args.output_text else None

    summary = summarize_redacted_history(
        input_path,
        output_json_path=output_json,
        output_text_path=output_text,
    )
    final_json = output_json or input_path.with_name(
        input_path.name.replace("_redacted.json", "_memory_summary.json")
        if input_path.name.endswith("_redacted.json")
        else f"{input_path.stem}_memory_summary.json"
    )
    final_text = output_text or final_json.with_suffix(".txt")

    print(f"Wrote summary JSON: {final_json}")
    print(f"Wrote summary text: {final_text}")
    print(f"Summary model: {summary['model_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
