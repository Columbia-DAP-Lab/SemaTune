#!/usr/bin/env python3
"""
Plot cycle-schedule comparison with 4 bars per setup:
1) MLOS tuning-only windows
2) Tuxbot tuning-only windows
3) MLOS last-N stable windows
4) Tuxbot last-N stable windows

This script reads already-generated history JSONs (no new benchmark runs).
It is experiment-agnostic and targets directories like:
  results/<experiment>/*_tuneX_stableY/
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np


HISTORY_PATTERNS: Tuple[str, ...] = ("optimization_history_*.json", "dual_loop_*.json")
SETUP_DIR_RE = re.compile(r"^(?P<tuner>.+)_tune(?P<tune>\d+)_stable(?P<stable>\d+)$")


@dataclass
class SeriesSummary:
    runs: int
    mean: Optional[float]
    median: Optional[float]
    std: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare MLOS vs Tuxbot for cycle schedules using four bars per setup "
            "(tuning-only and stable-only for each tuner)."
        )
    )
    parser.add_argument("experiment", help="Experiment name (e.g., silo_hi_p99, tpcc_hi_p99, dcperf_spark_tput).")
    parser.add_argument(
        "--results-root",
        default=None,
        help=(
            "Root directory containing *_tuneX_stableY dirs for this experiment. "
            "Default: results/<experiment>"
        ),
    )
    parser.add_argument(
        "--stable-windows",
        type=int,
        default=10,
        help="Stable windows per setup (default: 10). Used for stable slice and directory matching.",
    )
    parser.add_argument(
        "--tuning-setups",
        nargs="*",
        type=int,
        default=None,
        help="Optional explicit tuning setup list (e.g., 1 5 10 20 30 40 50).",
    )
    parser.add_argument(
        "--metric",
        default=None,
        help="Optional metric override (e.g., latency_p99, throughput, queries_per_hour).",
    )
    parser.add_argument(
        "--goal",
        choices=("minimize", "maximize"),
        default=None,
        help="Optional goal override. If omitted, inferred from configs.",
    )
    parser.add_argument(
        "--bar-stat",
        choices=("mean", "median"),
        default="mean",
        help="Statistic shown by bars (default: mean).",
    )
    parser.add_argument(
        "--fixed-history-dir",
        default=None,
        help="Optional direct fixed tuner directory containing history JSON files.",
    )
    parser.add_argument(
        "--fixed-results-dir",
        default=None,
        help=(
            "Optional full-config results directory used to locate fixed runs "
            "(example: all_results/results_config_full_param_silo_hi_p99)."
        ),
    )
    parser.add_argument(
        "--fixed-tuner",
        default="fixed",
        help="Fixed tuner directory name under --fixed-results-dir/<experiment>/ (default: fixed).",
    )
    parser.add_argument(
        "--full-group-results-dir",
        default=None,
        help=(
            "Optional full-config results directory to import one extra combined group "
            "(example: all_results/results_config_full_param_tpcc_hi_p99)."
        ),
    )
    parser.add_argument(
        "--full-group-mlos-tuner",
        default="mlos",
        help="Tuner directory name for MLOS under --full-group-results-dir/<experiment>/.",
    )
    parser.add_argument(
        "--full-group-tuxbot-tuner",
        default="llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3",
        help="Tuner directory name for Tuxbot under --full-group-results-dir/<experiment>/.",
    )
    parser.add_argument(
        "--full-group-start",
        type=int,
        default=0,
        help="Start iteration for imported full group window (inclusive, default: 0).",
    )
    parser.add_argument(
        "--full-group-end",
        type=int,
        default=60,
        help="End iteration for imported full group window (inclusive, default: 60).",
    )
    parser.add_argument(
        "--full-group-tuning",
        type=int,
        default=60,
        help="Tuning-range label number for imported full group (default: 60 -> label 0-60).",
    )
    parser.add_argument(
        "--output-dir",
        default="plots",
        help="Output directory for plot/table/csv (default: plots).",
    )
    parser.add_argument("--plot-output", default=None, help="Optional explicit .png output path.")
    parser.add_argument("--table-output", default=None, help="Optional explicit .md output path.")
    parser.add_argument("--csv-output", default=None, help="Optional explicit .csv output path.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print discovered inputs and computed run counts without writing files.",
    )
    parser.add_argument(
        "--converged-only",
        action="store_true",
        help=(
            "Use only converged runs. A run is considered converged when history length and max "
            "iteration reach config.max_iterations + config.post_tuning_windows."
        ),
    )
    parser.add_argument(
        "--include-fixed-row",
        action="store_true",
        help="Include an explicit fixed-baseline row in the output table.",
    )
    return parser.parse_args()


def canonical_goal(raw_goal: Optional[str]) -> str:
    goal = (raw_goal or "").strip().lower()
    if goal in {"maximize", "max", "higher_is_better"}:
        return "maximize"
    return "minimize"


def to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def summarize(values: Sequence[float]) -> SeriesSummary:
    if not values:
        return SeriesSummary(runs=0, mean=None, median=None, std=None)
    mean_val = statistics.fmean(values)
    median_val = statistics.median(values)
    std_val = statistics.stdev(values) if len(values) > 1 else 0.0
    return SeriesSummary(runs=len(values), mean=mean_val, median=median_val, std=std_val)


def fmt_float(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "NA"
    return f"{value:.{digits}f}"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "NA"
    return f"{value:+.2f}%"


def improvement_pct(baseline: Optional[float], candidate: Optional[float], goal: str) -> Optional[float]:
    if baseline is None or candidate is None or baseline == 0:
        return None
    if goal == "maximize":
        return ((candidate - baseline) / abs(baseline)) * 100.0
    return ((baseline - candidate) / abs(baseline)) * 100.0


def load_json(fp: Path) -> Optional[Dict[str, Any]]:
    try:
        with fp.open("r") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"Warning: could not read {fp}: {exc}")
        return None
    if not isinstance(data, dict):
        print(f"Warning: skipping non-dict JSON payload in {fp}")
        return None
    return data


def pick_iteration(entry: Dict[str, Any], fallback_pos: int) -> int:
    for key in ("iteration", "window_number", "index"):
        if key in entry:
            try:
                return int(entry[key])
            except (TypeError, ValueError):
                pass
    return fallback_pos


def extract_entry_value(entry: Dict[str, Any], metric_name: Optional[str]) -> Optional[float]:
    metrics = entry.get("metrics", {}) or {}
    system_metrics = entry.get("system_metrics", {}) or {}

    candidates: List[Any] = [entry.get("raw_metric_value")]
    if metric_name:
        candidates.append(metrics.get(metric_name))
        candidates.append(system_metrics.get(metric_name))
    candidates.append(entry.get("reward"))

    for candidate in candidates:
        parsed = to_float(candidate)
        if parsed is not None:
            return parsed
    return None


def detect_metric_and_goal(
    history_data: Dict[str, Any], forced_metric: Optional[str], forced_goal: Optional[str]
) -> Tuple[Optional[str], str]:
    config = history_data.get("config", {}) or {}
    metric = forced_metric or config.get("optimization_metric")
    metric_name = str(metric).strip() if metric else None
    goal = canonical_goal(forced_goal or config.get("optimization_goal"))
    return metric_name, goal


def to_int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out


def expected_total_windows(
    history_data: Dict[str, Any],
    fallback_tuning_windows: Optional[int] = None,
    fallback_stable_windows: Optional[int] = None,
) -> Optional[int]:
    config = history_data.get("config", {}) or {}
    max_iter = to_int(config.get("max_iterations"))
    post = to_int(config.get("post_tuning_windows"))

    if max_iter is None:
        max_iter = fallback_tuning_windows
    if post is None:
        post = fallback_stable_windows

    if max_iter is None:
        return None
    if post is None:
        post = 0
    return max_iter + post


def is_converged_history(
    history_data: Dict[str, Any],
    fallback_tuning_windows: Optional[int] = None,
    fallback_stable_windows: Optional[int] = None,
) -> bool:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return False

    expected = expected_total_windows(
        history_data,
        fallback_tuning_windows=fallback_tuning_windows,
        fallback_stable_windows=fallback_stable_windows,
    )
    if expected is None or expected <= 0:
        return False

    rows = [e for e in history if isinstance(e, dict)]
    if len(rows) < expected:
        return False

    max_iter = 0
    for pos, entry in enumerate(rows, start=1):
        max_iter = max(max_iter, pick_iteration(entry, pos))
    return max_iter >= expected


def iter_history_files(run_dir: Path) -> Iterable[Path]:
    for pattern in HISTORY_PATTERNS:
        for fp in sorted(run_dir.glob(pattern)):
            if fp.is_file():
                yield fp


def classify_tuner_from_setup_dir_name(name: str) -> Optional[str]:
    lower = name.lower()
    if "mlos" in lower and "llm" not in lower:
        return "mlos"
    if "tuxbot" in lower or "llm" in lower:
        return "tuxbot"
    return None


def parse_setup_dir_name(name: str) -> Optional[Tuple[int, int]]:
    m = SETUP_DIR_RE.match(name)
    if not m:
        return None
    return int(m.group("tune")), int(m.group("stable"))


def choose_workload_dir(results_dir: Path, experiment: str) -> Optional[Path]:
    direct = results_dir / experiment
    if direct.is_dir():
        return direct
    children = sorted([p for p in results_dir.iterdir() if p.is_dir()])
    for child in children:
        if child.name == experiment:
            return child
    return children[0] if children else None


def resolve_fixed_history_dir(args: argparse.Namespace, repo_root: Path) -> Optional[Path]:
    if args.fixed_history_dir:
        direct = Path(args.fixed_history_dir).resolve()
        return direct if direct.is_dir() else None

    if args.fixed_results_dir:
        base = Path(args.fixed_results_dir).resolve()
    else:
        base = (repo_root / "all_results" / f"results_config_full_param_{args.experiment}").resolve()

    if not base.is_dir():
        return None

    # If passed path is already a tuner dir with history files, use it directly.
    direct_files = list(iter_history_files(base))
    if direct_files:
        return base

    workload_dir = choose_workload_dir(base, args.experiment)
    if workload_dir is None:
        return None

    fixed_dir = workload_dir / args.fixed_tuner
    if fixed_dir.is_dir():
        return fixed_dir
    return None


def select_window_values(
    history_data: Dict[str, Any],
    metric_name: Optional[str],
    start_iter: int,
    end_iter: int,
) -> List[float]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return []
    values: List[float] = []
    for pos, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        iteration = pick_iteration(entry, pos)
        if iteration < start_iter or iteration > end_iter:
            continue
        value = extract_entry_value(entry, metric_name)
        if value is not None:
            values.append(value)
    return values


def load_full_group_from_tuner_dir(
    tuner_dir: Path,
    metric_name: Optional[str],
    goal: Optional[str],
    start_iter: int,
    end_iter: int,
    stable_windows: int,
    converged_only: bool,
    converged_fallback_tuning: int,
) -> Tuple[List[float], List[float], int, int, int, Counter[str], Counter[str]]:
    tuning_means: List[float] = []
    stable_means: List[float] = []
    files_seen = 0
    files_used = 0
    files_nonconverged = 0
    metric_counter: Counter[str] = Counter()
    goal_counter: Counter[str] = Counter()

    if not tuner_dir.is_dir():
        return tuning_means, stable_means, files_seen, files_used, files_nonconverged, metric_counter, goal_counter

    for history_file in iter_history_files(tuner_dir):
        files_seen += 1
        data = load_json(history_file)
        if data is None:
            continue
        if converged_only and not is_converged_history(
            data,
            fallback_tuning_windows=converged_fallback_tuning,
            fallback_stable_windows=0,
        ):
            files_nonconverged += 1
            continue

        m_name, g_name = detect_metric_and_goal(data, metric_name, goal)
        if m_name:
            metric_counter[m_name] += 1
        if g_name:
            goal_counter[g_name] += 1

        vals = select_window_values(
            history_data=data,
            metric_name=m_name,
            start_iter=start_iter,
            end_iter=end_iter,
        )
        if not vals:
            continue

        tuning_means.append(statistics.fmean(vals))
        stable_means.append(statistics.fmean(vals[-stable_windows:]) if len(vals) >= stable_windows else statistics.fmean(vals))
        files_used += 1

    return tuning_means, stable_means, files_seen, files_used, files_nonconverged, metric_counter, goal_counter


def extract_phase_values(
    history_data: Dict[str, Any],
    metric_name: Optional[str],
    tuning_windows: int,
    stable_windows: int,
) -> Tuple[List[float], List[float]]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return [], []

    rows: List[Tuple[int, int, float, Optional[bool]]] = []
    for pos, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        value = extract_entry_value(entry, metric_name)
        if value is None:
            continue
        iter_idx = pick_iteration(entry, pos)
        post_raw = entry.get("post_tuning_phase")
        post_flag = post_raw if isinstance(post_raw, bool) else None
        rows.append((iter_idx, pos, value, post_flag))

    if not rows:
        return [], []

    rows.sort(key=lambda x: (x[0], x[1]))
    has_phase_flags = any(r[3] is not None for r in rows)

    if has_phase_flags:
        tuning_values = [r[2] for r in rows if r[3] is False]
        stable_values = [r[2] for r in rows if r[3] is True]
    else:
        ordered_values = [r[2] for r in rows]
        tuning_values = ordered_values[: max(0, tuning_windows)]
        stable_values = ordered_values[-stable_windows:] if stable_windows > 0 else []

    if tuning_windows > 0 and len(tuning_values) > tuning_windows:
        tuning_values = tuning_values[:tuning_windows]
    if stable_windows > 0 and len(stable_values) > stable_windows:
        stable_values = stable_values[-stable_windows:]

    return tuning_values, stable_values


def extract_all_values(history_data: Dict[str, Any], metric_name: Optional[str]) -> List[float]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return []

    rows: List[Tuple[int, int, float]] = []
    for pos, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        value = extract_entry_value(entry, metric_name)
        if value is None:
            continue
        iter_idx = pick_iteration(entry, pos)
        rows.append((iter_idx, pos, value))
    rows.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in rows]


def render_markdown(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def draw_series_bars(
    ax: Any,
    x_positions: np.ndarray,
    values: Sequence[Optional[float]],
    stds: Sequence[Optional[float]],
    offset: float,
    width: float,
    color: str,
    hatch: Optional[str],
    label: str,
) -> None:
    valid_idx = [i for i, v in enumerate(values) if v is not None]
    if not valid_idx:
        return
    x = [float(x_positions[i] + offset) for i in valid_idx]
    heights = [float(values[i]) for i in valid_idx]
    yerr = [0.0 if stds[i] is None else float(stds[i]) for i in valid_idx]
    ax.bar(
        x,
        heights,
        width=width,
        yerr=yerr,
        capsize=3,
        color=color,
        edgecolor="black",
        linewidth=0.8,
        hatch=hatch or "",
        alpha=0.9,
        label=label,
    )


def main() -> None:
    args = parse_args()
    repo_root = Path.cwd().resolve()

    if args.stable_windows <= 0:
        raise SystemExit("Error: --stable-windows must be positive.")

    if args.results_root:
        results_root = Path(args.results_root).resolve()
    else:
        results_root = (repo_root / "results" / args.experiment).resolve()
    if not results_root.is_dir():
        raise SystemExit(f"Error: results root not found: {results_root}")

    setup_dirs_by_key: DefaultDict[Tuple[int, str], List[Path]] = defaultdict(list)
    all_setup_dirs = sorted([p for p in results_root.iterdir() if p.is_dir()])
    for d in all_setup_dirs:
        parsed = parse_setup_dir_name(d.name)
        if parsed is None:
            continue
        tune_windows, stable_windows = parsed
        if stable_windows != args.stable_windows:
            continue
        tuner_bucket = classify_tuner_from_setup_dir_name(d.name)
        if tuner_bucket not in {"mlos", "tuxbot"}:
            continue
        setup_dirs_by_key[(tune_windows, tuner_bucket)].append(d)

    discovered_tunes = sorted({k[0] for k in setup_dirs_by_key})
    if args.tuning_setups:
        target_tunes = sorted({int(v) for v in args.tuning_setups})
    else:
        target_tunes = discovered_tunes

    if not target_tunes:
        raise SystemExit(
            f"Error: no setup dirs found under {results_root} matching *_tuneX_stable{args.stable_windows}."
        )

    run_means: DefaultDict[Tuple[int, str, str], List[float]] = defaultdict(list)
    metric_counter: Counter[str] = Counter()
    goal_counter: Counter[str] = Counter()
    files_seen = 0
    files_used = 0
    files_missing_stable = 0
    files_nonconverged = 0
    full_group_files_seen = 0
    full_group_files_used = 0
    full_group_files_nonconverged = 0

    for tune_windows in target_tunes:
        for bucket in ("mlos", "tuxbot"):
            setup_dirs = sorted(setup_dirs_by_key.get((tune_windows, bucket), []))
            for setup_dir in setup_dirs:
                for history_file in iter_history_files(setup_dir):
                    files_seen += 1
                    history_data = load_json(history_file)
                    if history_data is None:
                        continue

                    if args.converged_only and not is_converged_history(
                        history_data,
                        fallback_tuning_windows=tune_windows,
                        fallback_stable_windows=args.stable_windows,
                    ):
                        files_nonconverged += 1
                        continue

                    metric_name, goal = detect_metric_and_goal(history_data, args.metric, args.goal)
                    if metric_name:
                        metric_counter[metric_name] += 1
                    goal_counter[goal] += 1

                    tuning_values, stable_values = extract_phase_values(
                        history_data=history_data,
                        metric_name=metric_name,
                        tuning_windows=tune_windows,
                        stable_windows=args.stable_windows,
                    )

                    used_this_file = False
                    if tuning_values:
                        run_means[(tune_windows, bucket, "tuning")].append(statistics.fmean(tuning_values))
                        used_this_file = True
                    if len(stable_values) >= args.stable_windows:
                        # Keep exactly the last-N stable windows for each run.
                        run_means[(tune_windows, bucket, "stable")].append(
                            statistics.fmean(stable_values[-args.stable_windows :])
                        )
                        used_this_file = True
                    else:
                        files_missing_stable += 1

                    if used_this_file:
                        files_used += 1

    # Optional import of one extra "full-param" group (typically 0-60 window).
    if args.full_group_results_dir:
        full_results_dir = Path(args.full_group_results_dir).resolve()
        full_workload = choose_workload_dir(full_results_dir, args.experiment)
        if full_workload is None:
            print(f"Warning: could not find workload dir under full-group source: {full_results_dir}")
        else:
            if args.full_group_end < args.full_group_start:
                raise SystemExit("Error: --full-group-end must be >= --full-group-start.")

            mlos_dir = full_workload / args.full_group_mlos_tuner
            tux_dir = full_workload / args.full_group_tuxbot_tuner
            full_tuning_label = args.full_group_tuning

            for bucket, tuner_dir in (("mlos", mlos_dir), ("tuxbot", tux_dir)):
                tuning_vals, stable_vals, seen, used, nonconv, m_counter, g_counter = load_full_group_from_tuner_dir(
                    tuner_dir=tuner_dir,
                    metric_name=args.metric,
                    goal=args.goal,
                    start_iter=args.full_group_start,
                    end_iter=args.full_group_end,
                    stable_windows=args.stable_windows,
                    converged_only=args.converged_only,
                    converged_fallback_tuning=max(1, args.full_group_end),
                )
                full_group_files_seen += seen
                full_group_files_used += used
                full_group_files_nonconverged += nonconv
                metric_counter.update(m_counter)
                goal_counter.update(g_counter)
                run_means[(full_tuning_label, bucket, "tuning")].extend(tuning_vals)
                run_means[(full_tuning_label, bucket, "stable")].extend(stable_vals)

            if run_means[(full_tuning_label, "mlos", "tuning")] or run_means[(full_tuning_label, "tuxbot", "tuning")]:
                target_tunes = sorted(set(target_tunes) | {full_tuning_label})

    metric_name = args.metric or (metric_counter.most_common(1)[0][0] if metric_counter else "objective")
    goal = canonical_goal(args.goal) if args.goal else (goal_counter.most_common(1)[0][0] if goal_counter else "minimize")

    fixed_dir = resolve_fixed_history_dir(args, repo_root)
    fixed_run_means: List[float] = []
    fixed_files_seen = 0
    fixed_files_used = 0
    fixed_files_nonconverged = 0
    if fixed_dir is not None and fixed_dir.is_dir():
        for history_file in iter_history_files(fixed_dir):
            fixed_files_seen += 1
            data = load_json(history_file)
            if data is None:
                continue
            if args.converged_only and not is_converged_history(
                data,
                fallback_tuning_windows=args.stable_windows,
                fallback_stable_windows=0,
            ):
                fixed_files_nonconverged += 1
                continue
            all_values = extract_all_values(history_data=data, metric_name=metric_name)
            # For fixed runs, use last stable-windows points as baseline line.
            if len(all_values) >= args.stable_windows:
                slice_vals = all_values[-args.stable_windows :]
            else:
                slice_vals = all_values
            if not slice_vals:
                continue
            fixed_run_means.append(statistics.fmean(slice_vals))
            fixed_files_used += 1

    fixed_summary = summarize(fixed_run_means)
    fixed_level = fixed_summary.mean if args.bar_stat == "mean" else fixed_summary.median

    # Dry-run summary only.
    if args.dry_run:
        print("[dry-run] Cycle phase comparison preview")
        print(f"[dry-run] Experiment: {args.experiment}")
        print(f"[dry-run] Results root: {results_root}")
        print(f"[dry-run] Target setups (tuning windows): {', '.join(str(v) for v in target_tunes)}")
        print(f"[dry-run] Stable windows: {args.stable_windows}")
        print(f"[dry-run] Metric: {metric_name}")
        print(f"[dry-run] Goal: {goal}")
        print(f"[dry-run] Scanned history files: {files_seen}")
        print(f"[dry-run] Used history files: {files_used}")
        if args.converged_only:
            print(f"[dry-run] Skipped non-converged files: {files_nonconverged}")
        if args.full_group_results_dir:
            print(
                f"[dry-run] Full-group files seen/used/skipped_nonconverged: "
                f"{full_group_files_seen}/{full_group_files_used}/{full_group_files_nonconverged}"
            )
        print(f"[dry-run] Files missing full stable slice: {files_missing_stable}")
        if fixed_dir is not None:
            print(f"[dry-run] Fixed history dir: {fixed_dir}")
            print(f"[dry-run] Fixed files used: {fixed_files_used}/{fixed_files_seen}")
            if args.converged_only:
                print(f"[dry-run] Fixed files skipped (non-converged): {fixed_files_nonconverged}")
            print(f"[dry-run] Fixed baseline ({args.bar_stat}): {fmt_float(fixed_level)}")
        else:
            print("[dry-run] Fixed history dir: not found")
        for tune_windows in target_tunes:
            m_tune = summarize(run_means[(tune_windows, "mlos", "tuning")]).runs
            t_tune = summarize(run_means[(tune_windows, "tuxbot", "tuning")]).runs
            m_stable = summarize(run_means[(tune_windows, "mlos", "stable")]).runs
            t_stable = summarize(run_means[(tune_windows, "tuxbot", "stable")]).runs
            print(
                f"[dry-run] setup 0-{tune_windows}: "
                f"mlos_tuning_runs={m_tune}, tuxbot_tuning_runs={t_tune}, "
                f"mlos_stable_runs={m_stable}, tuxbot_stable_runs={t_stable}"
            )
        return

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"stable{args.stable_windows}"

    plot_path = Path(args.plot_output).resolve() if args.plot_output else (
        output_dir / f"{args.experiment}_phase4_mlos_tuxbot_tuning_vs_stable_{suffix}.png"
    )
    table_path = Path(args.table_output).resolve() if args.table_output else (
        output_dir / f"{args.experiment}_phase4_mlos_tuxbot_tuning_vs_stable_{suffix}.md"
    )
    csv_path = Path(args.csv_output).resolve() if args.csv_output else (
        output_dir / f"{args.experiment}_phase4_mlos_tuxbot_tuning_vs_stable_{suffix}.csv"
    )

    # Build table.
    headers = [
        "Setup",
        "Tuning Range",
        "MLOS Tuning Runs",
        "Tuxbot Tuning Runs",
        "MLOS Stable Runs",
        "Tuxbot Stable Runs",
        "MLOS Tuning Mean",
        "Tuxbot Tuning Mean",
        "MLOS Stable Mean",
        "Tuxbot Stable Mean",
        "MLOS Tuning vs Fixed",
        "Tuxbot Tuning vs Fixed",
        "MLOS Stable vs Fixed",
        "Tuxbot Stable vs Fixed",
    ]
    rows: List[List[str]] = []
    if args.include_fixed_row:
        rows.append(
            [
                "fixed_baseline",
                "fixed",
                str(fixed_files_used),
                str(fixed_files_used),
                str(fixed_files_used),
                str(fixed_files_used),
                fmt_float(fixed_level),
                fmt_float(fixed_level),
                fmt_float(fixed_level),
                fmt_float(fixed_level),
                fmt_pct(0.0 if fixed_level is not None else None),
                fmt_pct(0.0 if fixed_level is not None else None),
                fmt_pct(0.0 if fixed_level is not None else None),
                fmt_pct(0.0 if fixed_level is not None else None),
            ]
        )

    for tune_windows in target_tunes:
        m_tune = summarize(run_means[(tune_windows, "mlos", "tuning")])
        t_tune = summarize(run_means[(tune_windows, "tuxbot", "tuning")])
        m_stable = summarize(run_means[(tune_windows, "mlos", "stable")])
        t_stable = summarize(run_means[(tune_windows, "tuxbot", "stable")])
        rows.append(
            [
                f"tune{tune_windows}_stable{args.stable_windows}",
                f"0-{tune_windows}",
                str(m_tune.runs),
                str(t_tune.runs),
                str(m_stable.runs),
                str(t_stable.runs),
                fmt_float(m_tune.mean),
                fmt_float(t_tune.mean),
                fmt_float(m_stable.mean),
                fmt_float(t_stable.mean),
                fmt_pct(improvement_pct(fixed_level, m_tune.mean, goal)),
                fmt_pct(improvement_pct(fixed_level, t_tune.mean, goal)),
                fmt_pct(improvement_pct(fixed_level, m_stable.mean, goal)),
                fmt_pct(improvement_pct(fixed_level, t_stable.mean, goal)),
            ]
        )

    md_lines = [
        f"# Cycle Phase Comparison ({args.experiment})",
        "",
        f"- Metric: `{metric_name}`",
        f"- Goal: `{goal}`",
        f"- Bar statistic: `{args.bar_stat}`",
        f"- Stable slice: `last {args.stable_windows} windows`",
        f"- Converged-only filter: `{'on' if args.converged_only else 'off'}`",
        f"- Fixed baseline source: `{fixed_dir if fixed_dir else 'not found'}`",
        "",
        render_markdown(headers, rows),
        "",
    ]
    table_path.write_text("\n".join(md_lines))
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    # Plot.
    x_positions = np.arange(len(target_tunes), dtype=float)
    width = 0.18
    fig, ax = plt.subplots(1, 1, figsize=(max(8.0, 1.35 * len(target_tunes) + 4.0), 5.6))

    series_defs = [
        ("mlos", "tuning", "MLOS tuning-only", "#16A085", ""),
        ("tuxbot", "tuning", "Tuxbot tuning-only", "#E74C3C", ""),
        ("mlos", "stable", f"MLOS stable (last {args.stable_windows})", "#16A085", "///"),
        ("tuxbot", "stable", f"Tuxbot stable (last {args.stable_windows})", "#E74C3C", "///"),
    ]
    for idx, (bucket, phase, label, color, hatch) in enumerate(series_defs):
        values: List[Optional[float]] = []
        stds: List[Optional[float]] = []
        for tune_windows in target_tunes:
            s = summarize(run_means[(tune_windows, bucket, phase)])
            values.append(s.mean if args.bar_stat == "mean" else s.median)
            stds.append(s.std)
        offset = (idx - 1.5) * width
        draw_series_bars(
            ax=ax,
            x_positions=x_positions,
            values=values,
            stds=stds,
            offset=offset,
            width=width,
            color=color,
            hatch=hatch,
            label=label,
        )

    if fixed_level is not None:
        ax.axhline(
            fixed_level,
            color="#7F8C8D",
            linestyle="--",
            linewidth=1.8,
            label="Default (fixed)",
        )

    stat_label = "Mean" if args.bar_stat == "mean" else "Median"
    ylabel = (
        f"{stat_label} {metric_name} (lower is better)"
        if goal == "minimize"
        else f"{stat_label} {metric_name} (higher is better)"
    )
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Setup (tuning window range)")
    ax.set_title(f"{args.experiment}: tuning-only vs stable-state (MLOS/Tuxbot)")
    ax.set_xticks(x_positions)
    ax.set_xticklabels([f"0-{t}" for t in target_tunes], rotation=25, ha="right")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        fig.legend(
            handles,
            labels,
            loc="lower center",
            ncol=3,
            bbox_to_anchor=(0.5, -0.02),
            frameon=True,
        )

    fig.tight_layout(rect=[0, 0.08, 1, 1])
    fig.savefig(plot_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"Experiment: {args.experiment}")
    print(f"Results root: {results_root}")
    print(f"Scanned history files: {files_seen}")
    print(f"Used history files: {files_used}")
    if args.converged_only:
        print(f"Skipped non-converged files: {files_nonconverged}")
    if args.full_group_results_dir:
        print(
            f"Full-group files seen/used/skipped_nonconverged: "
            f"{full_group_files_seen}/{full_group_files_used}/{full_group_files_nonconverged}"
        )
    print(f"Metric: {metric_name}")
    print(f"Goal: {goal}")
    if fixed_dir is not None:
        print(f"Fixed baseline dir: {fixed_dir}")
        print(f"Fixed baseline files used: {fixed_files_used}/{fixed_files_seen}")
        if args.converged_only:
            print(f"Fixed non-converged skipped: {fixed_files_nonconverged}")
        print(f"Fixed baseline ({args.bar_stat}): {fmt_float(fixed_level)}")
    else:
        print("Fixed baseline dir: not found")
    print(f"Setups: {', '.join(f'0-{t}' for t in target_tunes)}")
    print(f"Table: {table_path}")
    print(f"CSV:   {csv_path}")
    print(f"Plot:  {plot_path}")


if __name__ == "__main__":
    main()
