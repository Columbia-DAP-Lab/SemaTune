#!/usr/bin/env python3
"""
Check tuner result subdirectories for history JSON file counts.

By default it expects 5 history files per tuner directory and prints only
directories that do not match:

    (count) /abs/path/to/tuner_dir

History file patterns counted (directly inside each tuner dir):
- optimization_history*.json
- dual_loop*.json

Usage:
  python3 scripts/check_history_file_counts.py [results_dir ...]

If no results_dir arguments are provided, all local results_* directories are used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Set


DEFAULT_PATTERNS: Sequence[str] = ("optimization_history*.json", "dual_loop*.json")
IGNORE_DIR_NAMES: Set[str] = {"logs", "plots", ".git", "__pycache__"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print tuner result directories whose history JSON count is not the expected value."
        )
    )
    parser.add_argument(
        "results_dirs",
        nargs="*",
        help="Result roots to scan. If omitted, auto-discovers local results_* directories.",
    )
    parser.add_argument(
        "--expected",
        type=int,
        default=5,
        help="Expected history-file count per tuner directory (default: 5).",
    )
    return parser.parse_args()


def discover_default_results_dirs(cwd: Path) -> List[Path]:
    return sorted([p.resolve() for p in cwd.glob("results_*") if p.is_dir()])


def has_direct_history_files(directory: Path) -> bool:
    for pattern in DEFAULT_PATTERNS:
        if any(directory.glob(pattern)):
            return True
    return False


def iter_non_ignored_subdirs(directory: Path) -> Iterable[Path]:
    for child in sorted(directory.iterdir()):
        if child.is_dir() and child.name not in IGNORE_DIR_NAMES:
            yield child


def detect_tuner_dirs(results_root: Path) -> List[Path]:
    """
    Detect tuner dirs from either:
    - a tuner dir itself
    - a workload dir (children are tuner dirs)
    - a results root dir (children are workloads; grandchildren are tuner dirs)
    """
    if has_direct_history_files(results_root):
        return [results_root.resolve()]

    level1 = list(iter_non_ignored_subdirs(results_root))
    if not level1:
        return []

    # If immediate children have direct history files, root is a workload dir.
    if any(has_direct_history_files(child) for child in level1):
        return [p.resolve() for p in level1]

    # Otherwise root is likely a results root (level1 workloads, level2 tuners).
    tuner_dirs: List[Path] = []
    for workload_dir in level1:
        for tuner_dir in iter_non_ignored_subdirs(workload_dir):
            tuner_dirs.append(tuner_dir.resolve())
    return sorted(tuner_dirs)


def count_history_files_in_tuner_dir(tuner_dir: Path) -> int:
    files = set()
    for pattern in DEFAULT_PATTERNS:
        for path in tuner_dir.glob(pattern):
            if path.is_file():
                files.add(path.resolve())
    return len(files)


def main() -> int:
    args = parse_args()
    if args.expected < 0:
        print("Error: --expected must be >= 0", file=sys.stderr)
        return 2

    if args.results_dirs:
        results_roots = [Path(p).expanduser().resolve() for p in args.results_dirs]
    else:
        results_roots = discover_default_results_dirs(Path.cwd())

    if not results_roots:
        print("No results directories found to scan.", file=sys.stderr)
        return 1

    missing_roots = [p for p in results_roots if not p.is_dir()]
    if missing_roots:
        for p in missing_roots:
            print(f"Error: not a directory: {p}", file=sys.stderr)
        return 2

    all_tuner_dirs: List[Path] = []
    for root in results_roots:
        all_tuner_dirs.extend(detect_tuner_dirs(root))

    # Deduplicate while keeping sort stability.
    unique_tuner_dirs = sorted(set(all_tuner_dirs))
    if not unique_tuner_dirs:
        print("No tuner directories detected under the provided roots.", file=sys.stderr)
        return 1

    mismatch_count = 0
    for tuner_dir in unique_tuner_dirs:
        count = count_history_files_in_tuner_dir(tuner_dir)
        if count != args.expected:
            mismatch_count += 1
            print(f"({count}) {tuner_dir}")

    return 0 if mismatch_count == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
