#!/usr/bin/env python3
"""
Compare MLOS vs Tuxbot across parameter-count result directories.

This script is experiment-agnostic and works for layouts like:
  results_config_<N>_param_<experiment>_<timestamp>/<experiment>/<tuner_dir>/*.json

It will:
1. Discover (or accept) result directories for one experiment.
2. Collect MLOS runs and LLM/Tuxbot runs.
3. Build a comparison table for requested iteration windows.
4. Plot side-by-side grouped bars (MLOS vs Tuxbot) per <N>_param.

Window bounds are inclusive, matching existing interval scripts in this repo.
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
EXCLUDED_DIRS = {"logs", "plots", "__pycache__", ".git"}
RESULT_DIR_PARAM_RE = re.compile(r"^results_config_(\d+)_param_")
WINDOW_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


@dataclass(frozen=True)
class WindowSpec:
    start: int
    end: int
    label: str


@dataclass
class SeriesSummary:
    runs: int
    mean: Optional[float]
    median: Optional[float]
    std: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare MLOS vs Tuxbot by parameter count for one experiment and "
            "generate a table + grouped side-by-side plot."
        )
    )
    parser.add_argument(
        "experiment",
        type=str,
        help="Experiment/workload name (e.g., silo_hi_p99, dcperf_spark_tput).",
    )
    parser.add_argument(
        "--results-dirs",
        nargs="*",
        default=None,
        help=(
            "Optional explicit results directories. If omitted, auto-discovers "
            "results_config_*_param_<experiment>_* under --root."
        ),
    )
    parser.add_argument(
        "--root",
        type=str,
        default="all_results",
        help="Root directory used for auto-discovery (default: all_results).",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        default=["0-60", "60-80"],
        help="Window ranges (inclusive) as START-END or START:END. Default: 0-60 60-80",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default=None,
        help=(
            "Optional metric override (e.g., latency_p99, throughput). "
            "If omitted, uses config.optimization_metric per file."
        ),
    )
    parser.add_argument(
        "--goal",
        type=str,
        choices=("minimize", "maximize"),
        default=None,
        help=(
            "Optional objective goal override. If omitted, uses config.optimization_goal "
            "per file and falls back to minimize."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="plots",
        help="Directory for default outputs (default: plots).",
    )
    parser.add_argument(
        "--bar-stat",
        choices=("mean", "median"),
        default="mean",
        help="Statistic shown by bars (default: mean).",
    )
    parser.add_argument(
        "--full-results-dir",
        type=str,
        default=None,
        help=(
            "Optional full-config results directory to import an extra parameter-count group "
            "(e.g., results_config_full_param_silo_hi_p99)."
        ),
    )
    parser.add_argument(
        "--full-fixed-tuner",
        type=str,
        default="fixed",
        help="Tuner directory name for fixed/default under --full-results-dir.",
    )
    parser.add_argument(
        "--full-mlos-tuner",
        type=str,
        default="mlos",
        help="Tuner directory name for MLOS under --full-results-dir.",
    )
    parser.add_argument(
        "--full-tuxbot-tuner",
        type=str,
        default="llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3",
        help="Tuner directory name for Tuxbot under --full-results-dir.",
    )
    parser.add_argument(
        "--full-import-buckets",
        type=str,
        default="default,mlos,tuxbot",
        help=(
            "Comma-separated buckets to import from --full-results-dir. "
            "Choices: default,mlos,tuxbot. Example: default"
        ),
    )
    parser.add_argument(
        "--table-output",
        type=str,
        default=None,
        help="Optional explicit markdown table output path.",
    )
    parser.add_argument(
        "--csv-output",
        type=str,
        default=None,
        help="Optional explicit CSV output path.",
    )
    parser.add_argument(
        "--plot-output",
        type=str,
        default=None,
        help="Optional explicit plot output path (.png).",
    )
    return parser.parse_args()


def parse_windows(raw_windows: Sequence[str]) -> List[WindowSpec]:
    windows: List[WindowSpec] = []
    for raw in raw_windows:
        m = WINDOW_RE.match(raw)
        if not m:
            raise SystemExit(f"Error: invalid window '{raw}'. Use START-END (example: 0-60).")
        start = int(m.group(1))
        end = int(m.group(2))
        if start > end:
            raise SystemExit(f"Error: invalid window '{raw}' because start > end.")
        windows.append(WindowSpec(start=start, end=end, label=f"{start}-{end}"))
    return windows


def parse_param_count(result_dir_name: str) -> Optional[int]:
    m = RESULT_DIR_PARAM_RE.match(result_dir_name)
    if not m:
        return None
    return int(m.group(1))


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


def pick_iteration(entry: Dict[str, Any], fallback_pos: int) -> int:
    for key in ("iteration", "window_number", "index"):
        if key in entry:
            try:
                return int(entry[key])
            except (TypeError, ValueError):
                pass
    return fallback_pos


def infer_param_count_from_config(config: Dict[str, Any]) -> Optional[int]:
    params = config.get("parameters_to_tune")
    if isinstance(params, list):
        return len(params)
    param_ranges = config.get("parameter_ranges", {})
    if isinstance(param_ranges, dict):
        return len(param_ranges)
    return None


def discover_results_dirs(root: Path, experiment: str) -> List[Path]:
    pattern = f"results_config_*_param_{experiment}_*"
    candidates = [p for p in root.glob(pattern) if p.is_dir()]
    # Keep only names that look like results_config_<N>_param_*
    candidates = [p for p in candidates if parse_param_count(p.name) is not None]
    candidates.sort(key=lambda p: (parse_param_count(p.name), p.name))
    return candidates


def resolve_results_dirs(args: argparse.Namespace) -> List[Path]:
    if args.results_dirs:
        dirs = [Path(p).resolve() for p in args.results_dirs]
        missing = [str(p) for p in dirs if not p.is_dir()]
        if missing:
            raise SystemExit(f"Error: these --results-dirs are missing or not directories: {missing}")
        dirs.sort(key=lambda p: (parse_param_count(p.name), p.name))
        return dirs
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"Error: --root is not a directory: {root}")
    return discover_results_dirs(root, args.experiment)


def resolve_full_results_dir(args: argparse.Namespace) -> Optional[Path]:
    if args.full_results_dir:
        path = Path(args.full_results_dir).resolve()
        if not path.is_dir():
            raise SystemExit(f"Error: --full-results-dir is not a directory: {path}")
        return path

    candidate = (Path(args.root).resolve() / f"results_config_full_param_{args.experiment}")
    if candidate.is_dir():
        return candidate
    return None


def parse_full_import_buckets(raw: str) -> set[str]:
    allowed = {"default", "mlos", "tuxbot"}
    items = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not items:
        raise SystemExit("Error: --full-import-buckets resolved to empty set.")
    invalid = sorted(items - allowed)
    if invalid:
        raise SystemExit(
            f"Error: invalid --full-import-buckets entries: {invalid}. "
            f"Allowed: {sorted(allowed)}"
        )
    return items


def choose_workload_dir(result_dir: Path, experiment: str) -> Optional[Path]:
    direct = result_dir / experiment
    if direct.is_dir():
        return direct

    subdirs = sorted(
        [d for d in result_dir.iterdir() if d.is_dir() and d.name not in EXCLUDED_DIRS and not d.name.startswith(".")]
    )
    if not subdirs:
        return None

    exact = [d for d in subdirs if d.name == experiment]
    if exact:
        return exact[0]

    contains = [d for d in subdirs if experiment in d.name]
    if contains:
        return contains[0]

    return subdirs[0]


def iter_history_files(workload_dir: Path) -> Iterable[Path]:
    tuner_dirs = [d for d in workload_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    for tuner_dir in sorted(tuner_dirs):
        for pattern in HISTORY_PATTERNS:
            for fp in sorted(tuner_dir.rglob(pattern)):
                if fp.is_file():
                    yield fp


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


def classify_bucket(history_file: Path, history_data: Dict[str, Any]) -> Optional[str]:
    # "Tuxbot" bucket means any LLM/tuxbot run.
    lower_path = str(history_file).lower()
    tuner_dir = history_file.parent.name.lower()
    config = history_data.get("config", {}) or {}
    tuner_type = str(config.get("tuner_type", "")).strip().lower()

    if "tuxbot" in lower_path or "tuxbot" in tuner_dir:
        return "tuxbot"
    if tuner_type == "llm" or "llm" in tuner_dir:
        return "tuxbot"

    if tuner_type == "fixed" or tuner_dir == "fixed":
        return "default"

    if tuner_type == "mlos":
        return "mlos"
    if tuner_dir == "mlos" or re.search(r"(^|_)mlos($|_)", tuner_dir):
        return "mlos"

    return None


def detect_metric_and_goal(
    history_data: Dict[str, Any], forced_metric: Optional[str], forced_goal: Optional[str]
) -> Tuple[Optional[str], str]:
    config = history_data.get("config", {}) or {}
    metric = forced_metric or config.get("optimization_metric")
    metric_name = str(metric).strip() if metric else None
    goal = canonical_goal(forced_goal or config.get("optimization_goal"))
    return metric_name, goal


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


def extract_window_values(
    history_data: Dict[str, Any], metric_name: Optional[str], window: WindowSpec
) -> List[float]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return []

    values: List[float] = []
    for pos, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        iteration = pick_iteration(entry, pos)
        if iteration < window.start or iteration > window.end:
            continue
        value = extract_entry_value(entry, metric_name)
        if value is not None:
            values.append(value)
    return values


def summarize(values: Sequence[float]) -> SeriesSummary:
    if not values:
        return SeriesSummary(runs=0, mean=None, median=None, std=None)
    mean_val = statistics.fmean(values)
    median_val = statistics.median(values)
    std_val = statistics.stdev(values) if len(values) > 1 else 0.0
    return SeriesSummary(runs=len(values), mean=mean_val, median=median_val, std=std_val)


def improvement_pct(baseline: Optional[float], candidate: Optional[float], goal: str) -> Optional[float]:
    if baseline is None or candidate is None:
        return None
    if baseline == 0:
        return None
    if goal == "maximize":
        return ((candidate - baseline) / abs(baseline)) * 100.0
    return ((baseline - candidate) / abs(baseline)) * 100.0


def fmt_float(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "NA"
    return f"{value:.{digits}f}"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "NA"
    return f"{value:+.2f}%"


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
        capsize=4,
        color=color,
        edgecolor="black",
        linewidth=0.7,
        alpha=0.9,
        label=label,
    )


def build_outputs(
    experiment: str,
    windows: Sequence[WindowSpec],
    param_counts: Sequence[int],
    run_means: DefaultDict[Tuple[int, str, str], List[float]],
    metric_name: str,
    goal: str,
    bar_stat: str,
    output_dir: Path,
    table_output: Optional[Path],
    csv_output: Optional[Path],
    plot_output: Optional[Path],
) -> Tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = "__".join(w.label.replace("-", "_") for w in windows)
    table_path = table_output or (output_dir / f"{experiment}_param_mlos_vs_tuxbot_{suffix}.md")
    csv_path = csv_output or (output_dir / f"{experiment}_param_mlos_vs_tuxbot_{suffix}.csv")
    plot_path = plot_output or (output_dir / f"{experiment}_param_mlos_vs_tuxbot_{suffix}.png")

    headers = [
        "Window",
        "Param Group",
        "Default Runs",
        "MLOS Runs",
        "Tuxbot Runs",
        "Default Mean",
        "MLOS Mean",
        "Tuxbot Mean",
        "Default Median",
        "MLOS Median",
        "Tuxbot Median",
        "MLOS vs Default",
        "Tuxbot vs Default",
        "Tuxbot vs MLOS",
    ]
    rows_md: List[List[str]] = []
    rows_csv: List[List[str]] = []

    for window in windows:
        for param_count in param_counts:
            default_summary = summarize(run_means[(param_count, window.label, "default")])
            mlos_summary = summarize(run_means[(param_count, window.label, "mlos")])
            tux_summary = summarize(run_means[(param_count, window.label, "tuxbot")])
            mlos_vs_default = improvement_pct(default_summary.mean, mlos_summary.mean, goal)
            tux_vs_default = improvement_pct(default_summary.mean, tux_summary.mean, goal)
            tux_vs_mlos = improvement_pct(mlos_summary.mean, tux_summary.mean, goal)

            rows_md.append(
                [
                    window.label,
                    f"{param_count}_param",
                    str(default_summary.runs),
                    str(mlos_summary.runs),
                    str(tux_summary.runs),
                    fmt_float(default_summary.mean),
                    fmt_float(mlos_summary.mean),
                    fmt_float(tux_summary.mean),
                    fmt_float(default_summary.median),
                    fmt_float(mlos_summary.median),
                    fmt_float(tux_summary.median),
                    fmt_pct(mlos_vs_default),
                    fmt_pct(tux_vs_default),
                    fmt_pct(tux_vs_mlos),
                ]
            )
            rows_csv.append(
                [
                    window.label,
                    f"{param_count}_param",
                    str(default_summary.runs),
                    str(mlos_summary.runs),
                    str(tux_summary.runs),
                    fmt_float(default_summary.mean),
                    fmt_float(mlos_summary.mean),
                    fmt_float(tux_summary.mean),
                    fmt_float(default_summary.median),
                    fmt_float(mlos_summary.median),
                    fmt_float(tux_summary.median),
                    fmt_pct(mlos_vs_default),
                    fmt_pct(tux_vs_default),
                    fmt_pct(tux_vs_mlos),
                ]
            )

    md_text_lines = [
        f"# MLOS vs Tuxbot Comparison ({experiment})",
        "",
        f"- Metric: `{metric_name}`",
        f"- Goal: `{goal}`",
        f"- Windows (inclusive): {', '.join(w.label for w in windows)}",
        "",
        render_markdown(headers, rows_md),
        "",
    ]
    table_path.write_text("\n".join(md_text_lines))

    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows_csv)

    fig, axes = plt.subplots(1, len(windows), figsize=(7.0 * len(windows), 5.4), sharey=True)
    if len(windows) == 1:
        axes = [axes]

    x_positions = np.arange(len(param_counts), dtype=float)
    width = 0.34

    for ax, window in zip(axes, windows):
        mlos_means: List[Optional[float]] = []
        mlos_stds: List[Optional[float]] = []
        tux_means: List[Optional[float]] = []
        tux_stds: List[Optional[float]] = []
        fixed_values_for_window: List[float] = []

        for param_count in param_counts:
            d_summary = summarize(run_means[(param_count, window.label, "default")])
            m_summary = summarize(run_means[(param_count, window.label, "mlos")])
            t_summary = summarize(run_means[(param_count, window.label, "tuxbot")])

            if not fixed_values_for_window:
                maybe_fixed = run_means[(param_count, window.label, "default")]
                if maybe_fixed:
                    fixed_values_for_window = list(maybe_fixed)

            mlos_means.append(m_summary.mean if bar_stat == "mean" else m_summary.median)
            mlos_stds.append(m_summary.std)
            tux_means.append(t_summary.mean if bar_stat == "mean" else t_summary.median)
            tux_stds.append(t_summary.std)

        draw_series_bars(
            ax=ax,
            x_positions=x_positions,
            values=mlos_means,
            stds=mlos_stds,
            offset=-width / 2.0,
            width=width,
            color="#16A085",
            label="MLOS",
        )
        draw_series_bars(
            ax=ax,
            x_positions=x_positions,
            values=tux_means,
            stds=tux_stds,
            offset=+width / 2.0,
            width=width,
            color="#E74C3C",
            label="Tuxbot",
        )

        fixed_summary = summarize(fixed_values_for_window)
        fixed_level = fixed_summary.mean if bar_stat == "mean" else fixed_summary.median
        if fixed_level is not None:
            ax.axhline(
                fixed_level,
                color="#7F8C8D",
                linestyle="--",
                linewidth=1.8,
                label="Default (fixed)",
            )

        ax.set_title(f"Windows {window.label}")
        ax.set_xticks(x_positions)
        ax.set_xticklabels([f"{pc}_param" for pc in param_counts], rotation=25, ha="right")
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)

    stat_label = "Mean" if bar_stat == "mean" else "Median"
    ylabel = (
        f"{stat_label} {metric_name} (lower is better)"
        if goal == "minimize"
        else f"{stat_label} {metric_name} (higher is better)"
    )
    axes[0].set_ylabel(ylabel)
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        axes[0].legend(handles, labels, loc="upper right")

    fig.suptitle(f"{experiment}: Default vs MLOS vs Tuxbot by Parameter Count", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(plot_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    return table_path, csv_path, plot_path


def load_run_means_from_tuner_dir(
    tuner_dir: Path,
    windows: Sequence[WindowSpec],
    forced_metric: Optional[str],
    forced_goal: Optional[str],
) -> Tuple[Optional[int], Optional[str], Optional[str], Dict[str, List[float]], int]:
    if not tuner_dir.is_dir():
        return None, None, None, {}, 0

    per_window: Dict[str, List[float]] = {w.label: [] for w in windows}
    param_count: Optional[int] = None
    metric_name: Optional[str] = None
    goal_name: Optional[str] = None
    files_used = 0

    files: List[Path] = []
    for pattern in HISTORY_PATTERNS:
        files.extend(sorted(tuner_dir.rglob(pattern)))

    for fp in files:
        if not fp.is_file():
            continue
        data = load_json(fp)
        if data is None:
            continue
        config = data.get("config", {}) or {}

        if param_count is None:
            inferred = infer_param_count_from_config(config)
            if inferred is not None:
                param_count = inferred

        m_name, g_name = detect_metric_and_goal(data, forced_metric, forced_goal)
        if m_name and metric_name is None:
            metric_name = m_name
        if g_name and goal_name is None:
            goal_name = g_name

        used_this_file = False
        for window in windows:
            values = extract_window_values(data, m_name, window)
            if not values:
                continue
            per_window[window.label].append(statistics.fmean(values))
            used_this_file = True
        if used_this_file:
            files_used += 1

    # Drop empty windows if nothing was collected
    per_window = {k: v for k, v in per_window.items() if v}
    return param_count, metric_name, goal_name, per_window, files_used


def pick_tuner_dir(workload_dir: Path, preferred_name: str, fallback_contains: Sequence[str]) -> Optional[Path]:
    preferred = workload_dir / preferred_name
    if preferred.is_dir():
        return preferred

    candidates = []
    for d in sorted([p for p in workload_dir.iterdir() if p.is_dir()]):
        name = d.name.lower()
        if all(token.lower() in name for token in fallback_contains):
            candidates.append(d)

    if candidates:
        if len(candidates) > 1:
            print(f"Warning: multiple fallback tuner dirs found for {fallback_contains}; using {candidates[0].name}")
        return candidates[0]
    return None


def main() -> None:
    args = parse_args()
    windows = parse_windows(args.windows)
    results_dirs = resolve_results_dirs(args)
    full_results_dir = resolve_full_results_dir(args)
    full_import_buckets = parse_full_import_buckets(args.full_import_buckets)

    if not results_dirs:
        raise SystemExit(
            f"Error: no results directories found for experiment '{args.experiment}'. "
            "Pass --results-dirs explicitly or check --root."
        )

    run_means: DefaultDict[Tuple[int, str, str], List[float]] = defaultdict(list)
    metric_counter: Counter[str] = Counter()
    goal_counter: Counter[str] = Counter()
    param_counts_set = set()
    files_seen = 0
    files_used = 0
    full_files_used = 0
    full_fixed_by_window: Dict[str, List[float]] = {}

    for result_dir in results_dirs:
        param_count = parse_param_count(result_dir.name)
        if param_count is None:
            print(f"Warning: skipping {result_dir} because parameter count could not be parsed.")
            continue
        param_counts_set.add(param_count)

        workload_dir = choose_workload_dir(result_dir, args.experiment)
        if workload_dir is None or not workload_dir.is_dir():
            print(f"Warning: skipping {result_dir} because workload directory was not found.")
            continue

        for history_file in iter_history_files(workload_dir):
            files_seen += 1
            history_data = load_json(history_file)
            if history_data is None:
                continue

            bucket = classify_bucket(history_file, history_data)
            if bucket not in {"default", "mlos", "tuxbot"}:
                continue

            metric_name, goal = detect_metric_and_goal(history_data, args.metric, args.goal)
            if metric_name:
                metric_counter[metric_name] += 1
            goal_counter[goal] += 1

            any_window_data = False
            for window in windows:
                window_values = extract_window_values(history_data, metric_name, window)
                if not window_values:
                    continue
                run_mean = statistics.fmean(window_values)
                run_means[(param_count, window.label, bucket)].append(run_mean)
                any_window_data = True

            if any_window_data:
                files_used += 1

    # Optional full-config import (typically 8-param for this repo).
    if full_results_dir is not None:
        workload_dir = choose_workload_dir(full_results_dir, args.experiment)
        if workload_dir is None or not workload_dir.is_dir():
            print(f"Warning: could not find workload dir under full-config results: {full_results_dir}")
        else:
            fixed_dir = pick_tuner_dir(workload_dir, args.full_fixed_tuner, ("fixed",))
            mlos_dir = pick_tuner_dir(workload_dir, args.full_mlos_tuner, ("mlos",))
            tux_dir = pick_tuner_dir(
                workload_dir,
                args.full_tuxbot_tuner,
                ("llm", "dual", "full_metrics", "mode3"),
            )

            imported_param_count: Optional[int] = None
            imported: Dict[str, Dict[str, List[float]]] = {}

            for bucket, tuner_dir in (
                ("default", fixed_dir),
                ("mlos", mlos_dir),
                ("tuxbot", tux_dir),
            ):
                if bucket not in full_import_buckets:
                    continue
                if tuner_dir is None:
                    print(f"Warning: full-config tuner dir not found for bucket '{bucket}'.")
                    continue

                param_count, metric_name, goal_name, per_window, used = load_run_means_from_tuner_dir(
                    tuner_dir=tuner_dir,
                    windows=windows,
                    forced_metric=args.metric,
                    forced_goal=args.goal,
                )
                full_files_used += used

                if metric_name:
                    metric_counter[metric_name] += 1
                if goal_name:
                    goal_counter[goal_name] += 1

                if param_count is not None:
                    imported_param_count = imported_param_count or param_count
                imported[bucket] = per_window

            if imported_param_count is not None:
                param_counts_set.add(imported_param_count)

                # Include full-config point as an explicit parameter-count group.
                for bucket in ("mlos", "tuxbot", "default"):
                    if bucket not in full_import_buckets:
                        continue
                    per_window = imported.get(bucket, {})
                    for window in windows:
                        vals = per_window.get(window.label, [])
                        if vals:
                            run_means[(imported_param_count, window.label, bucket)].extend(vals)

                # Use full fixed as fallback default baseline for groups that lack fixed runs.
                default_windows = imported.get("default", {})
                full_fixed_by_window = {k: list(v) for k, v in default_windows.items() if v}

    # Backfill default baseline from full fixed where per-group fixed runs are absent.
    if "default" in full_import_buckets and full_fixed_by_window:
        for param_count in list(param_counts_set):
            for window in windows:
                key = (param_count, window.label, "default")
                if run_means[key]:
                    continue
                fallback_vals = full_fixed_by_window.get(window.label, [])
                if fallback_vals:
                    run_means[key].extend(fallback_vals)

    if not param_counts_set:
        raise SystemExit("Error: no usable result directories were found.")

    metric_name = args.metric or (metric_counter.most_common(1)[0][0] if metric_counter else "objective")
    goal = canonical_goal(args.goal) if args.goal else (goal_counter.most_common(1)[0][0] if goal_counter else "minimize")

    param_counts = sorted(param_counts_set)
    output_dir = Path(args.output_dir).resolve()
    table_output = Path(args.table_output).resolve() if args.table_output else None
    csv_output = Path(args.csv_output).resolve() if args.csv_output else None
    plot_output = Path(args.plot_output).resolve() if args.plot_output else None

    table_path, csv_path, plot_path = build_outputs(
        experiment=args.experiment,
        windows=windows,
        param_counts=param_counts,
        run_means=run_means,
        metric_name=metric_name,
        goal=goal,
        bar_stat=args.bar_stat,
        output_dir=output_dir,
        table_output=table_output,
        csv_output=csv_output,
        plot_output=plot_output,
    )

    print(f"Processed {len(results_dirs)} result directories.")
    print(f"Scanned history files: {files_seen}")
    print(f"Used history files: {files_used + full_files_used} (base={files_used}, full-config={full_files_used})")
    print(f"Metric: {metric_name}")
    print(f"Goal: {goal}")
    print(f"Bar statistic: {args.bar_stat}")
    if full_results_dir is not None:
        print(f"Full-config source: {full_results_dir}")
        print(f"Full import buckets: {','.join(sorted(full_import_buckets))}")
    print(f"Windows (inclusive): {', '.join(w.label for w in windows)}")
    print(f"Table: {table_path}")
    print(f"CSV:   {csv_path}")
    print(f"Plot:  {plot_path}")


if __name__ == "__main__":
    main()
