#!/usr/bin/env python3
"""Summarize first convergence time per run and per config directory.

Given a results directory, this script:
1) Finds run history JSON files.
2) Computes first convergence point per run (first time converged == true).
3) Groups runs by config directory (the parent directory of each history file).
4) Reports average first-convergence time and iteration per config directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple


HISTORY_PATTERNS: Tuple[str, ...] = (
    "optimization_history_*.json",
    "dual_loop_actor_speculator_*.json",
)


@dataclass
class RunConvergence:
    file_path: Path
    converged: bool
    first_converged_iteration: Optional[int]
    first_converged_timestamp: Optional[float]
    run_start_timestamp: Optional[float]
    convergence_time_s: Optional[float]
    convergence_source: Optional[str]


@dataclass
class ConfigConvergence:
    config_dir: Path
    runs: List[RunConvergence]

    @property
    def runs_total(self) -> int:
        return len(self.runs)

    @property
    def runs_converged(self) -> int:
        return sum(1 for run in self.runs if run.converged)

    @property
    def runs_not_converged(self) -> int:
        return self.runs_total - self.runs_converged

    @property
    def avg_convergence_time_s(self) -> Optional[float]:
        values = [run.convergence_time_s for run in self.runs if run.convergence_time_s is not None]
        return mean(values) if values else None

    @property
    def avg_convergence_iteration(self) -> Optional[float]:
        values = [run.first_converged_iteration for run in self.runs if run.first_converged_iteration is not None]
        return mean(values) if values else None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "t"}:
            return True
        if v in {"false", "0", "no", "n", "f"}:
            return False
    return None


def _extract_convergence_flags(entry: Dict[str, Any]) -> List[Tuple[str, bool]]:
    flags: List[Tuple[str, bool]] = []

    val = _as_bool(entry.get("converged"))
    if val is not None:
        flags.append(("entry.converged", val))

    timing = entry.get("tuner_timing")
    if isinstance(timing, dict):
        val = _as_bool(timing.get("converged"))
        if val is not None:
            flags.append(("tuner_timing.converged", val))

        for role in ("quick", "reasoning", "reasoning_final"):
            role_obj = timing.get(role)
            if isinstance(role_obj, dict):
                val = _as_bool(role_obj.get("converged"))
                if val is not None:
                    flags.append((f"tuner_timing.{role}.converged", val))

    legacy_reasoning = entry.get("reasoning_tuner_timing")
    if isinstance(legacy_reasoning, dict):
        val = _as_bool(legacy_reasoning.get("converged"))
        if val is not None:
            flags.append(("reasoning_tuner_timing.converged", val))

    metrics = entry.get("metrics")
    if isinstance(metrics, dict):
        all_quick = metrics.get("all_quick_tuner_timings")
        if isinstance(all_quick, list):
            for idx, item in enumerate(all_quick):
                if isinstance(item, dict):
                    val = _as_bool(item.get("converged"))
                    if val is not None:
                        flags.append((f"metrics.all_quick_tuner_timings[{idx}].converged", val))

    return flags


def _extract_run_start_timestamp(history: List[Dict[str, Any]]) -> Optional[float]:
    candidates: List[float] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue

        sys_metrics = entry.get("system_metrics")
        if isinstance(sys_metrics, dict):
            t = _as_float(sys_metrics.get("window_start_time"))
            if t is not None:
                candidates.append(t)

        metrics = entry.get("metrics")
        if isinstance(metrics, dict):
            t = _as_float(metrics.get("window_start_time"))
            if t is not None:
                candidates.append(t)

            nested_sys = metrics.get("system_metrics")
            if isinstance(nested_sys, dict):
                t = _as_float(nested_sys.get("window_start_time"))
                if t is not None:
                    candidates.append(t)

    if candidates:
        return min(candidates)

    if history and isinstance(history[0], dict):
        return _as_float(history[0].get("timestamp"))
    return None


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def summarize_run(history_file: Path) -> RunConvergence:
    data = _load_json(history_file)
    history_raw = data.get("history", [])
    if not isinstance(history_raw, list):
        history_raw = []

    ordered_entries: List[Tuple[int, Dict[str, Any]]] = []
    for idx, entry in enumerate(history_raw, start=1):
        if not isinstance(entry, dict):
            continue
        iteration = _as_int(entry.get("iteration"))
        if iteration is None:
            iteration = idx
        ordered_entries.append((iteration, entry))
    ordered_entries.sort(key=lambda item: item[0])

    history = [entry for _, entry in ordered_entries]
    run_start_ts = _extract_run_start_timestamp(history)

    first_iter: Optional[int] = None
    first_ts: Optional[float] = None
    first_source: Optional[str] = None

    for iteration, entry in ordered_entries:
        flags = _extract_convergence_flags(entry)
        true_sources = [source for source, value in flags if value]
        if not true_sources:
            continue

        first_iter = iteration
        first_ts = _as_float(entry.get("timestamp"))
        first_source = true_sources[0]
        break

    converged = first_iter is not None
    convergence_time_s: Optional[float] = None
    if converged and first_ts is not None and run_start_ts is not None:
        convergence_time_s = max(0.0, first_ts - run_start_ts)

    return RunConvergence(
        file_path=history_file,
        converged=converged,
        first_converged_iteration=first_iter,
        first_converged_timestamp=first_ts,
        run_start_timestamp=run_start_ts,
        convergence_time_s=convergence_time_s,
        convergence_source=first_source,
    )


def discover_history_files(results_root: Path) -> Dict[Path, List[Path]]:
    grouped: Dict[Path, set[Path]] = defaultdict(set)
    for pattern in HISTORY_PATTERNS:
        for file_path in results_root.rglob(pattern):
            if file_path.is_file():
                grouped[file_path.parent].add(file_path)

    return {
        config_dir: sorted(files)
        for config_dir, files in sorted(grouped.items(), key=lambda item: str(item[0]))
    }


def summarize_results_root(results_root: Path) -> List[ConfigConvergence]:
    grouped_files = discover_history_files(results_root)
    summaries: List[ConfigConvergence] = []

    for config_dir, files in grouped_files.items():
        runs: List[RunConvergence] = []
        for file_path in files:
            try:
                runs.append(summarize_run(file_path))
            except Exception as exc:
                print(f"Warning: failed to parse {file_path}: {exc}", file=sys.stderr)
        if runs:
            summaries.append(ConfigConvergence(config_dir=config_dir, runs=runs))

    return summaries


def _relative_or_abs(path: Path, base: Path) -> str:
    try:
        rel = path.relative_to(base)
        if str(rel) == ".":
            return path.name
        return str(rel)
    except ValueError:
        return str(path)


def print_human(summaries: List[ConfigConvergence], results_root: Path, details: bool) -> None:
    if not summaries:
        print("No history JSON files found.")
        return

    for summary in summaries:
        config_label = _relative_or_abs(summary.config_dir, results_root)
        avg_time = summary.avg_convergence_time_s
        avg_iter = summary.avg_convergence_iteration

        print(f"config_dir: {config_label}")
        print(
            f"  runs_total={summary.runs_total}, "
            f"runs_converged={summary.runs_converged}, "
            f"runs_not_converged={summary.runs_not_converged}"
        )
        print(
            "  avg_first_convergence_time_s="
            + (f"{avg_time:.3f}" if avg_time is not None else "n/a")
        )
        print(
            "  avg_first_convergence_iteration="
            + (f"{avg_iter:.3f}" if avg_iter is not None else "n/a")
        )

        if details:
            print("  runs:")
            for run in summary.runs:
                file_name = run.file_path.name
                if run.converged:
                    conv_time = f"{run.convergence_time_s:.3f}s" if run.convergence_time_s is not None else "n/a"
                    print(
                        f"    - {file_name}: converged=true, "
                        f"iter={run.first_converged_iteration}, "
                        f"time={conv_time}, "
                        f"source={run.convergence_source}"
                    )
                else:
                    print(f"    - {file_name}: converged=false")
        print()


def print_json(summaries: List[ConfigConvergence], results_root: Path) -> None:
    payload = {
        "results_root": str(results_root),
        "config_dirs": [],
    }
    for summary in summaries:
        payload["config_dirs"].append(
            {
                "config_dir": _relative_or_abs(summary.config_dir, results_root),
                "runs_total": summary.runs_total,
                "runs_converged": summary.runs_converged,
                "runs_not_converged": summary.runs_not_converged,
                "avg_first_convergence_time_s": summary.avg_convergence_time_s,
                "avg_first_convergence_iteration": summary.avg_convergence_iteration,
                "runs": [
                    {
                        "file": run.file_path.name,
                        "converged": run.converged,
                        "first_converged_iteration": run.first_converged_iteration,
                        "first_converged_timestamp": run.first_converged_timestamp,
                        "run_start_timestamp": run.run_start_timestamp,
                        "convergence_time_s": run.convergence_time_s,
                        "convergence_source": run.convergence_source,
                    }
                    for run in summary.runs
                ],
            }
        )
    print(json.dumps(payload, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Given a results dir, compute first convergence time per run and "
            "average it per config directory."
        )
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Results root or a single config directory containing history JSON files.",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print per-run convergence details for each config directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_root = args.results_dir.resolve()
    if not results_root.exists():
        raise SystemExit(f"Results directory not found: {results_root}")
    if not results_root.is_dir():
        raise SystemExit(f"Expected a directory: {results_root}")

    summaries = summarize_results_root(results_root)
    if args.json:
        print_json(summaries, results_root)
    else:
        print_human(summaries, results_root, details=args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
