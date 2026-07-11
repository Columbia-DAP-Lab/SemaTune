#!/usr/bin/env python3
"""Section 5.4: Dual vs Single Loop Tuning and Cost.

Two-panel figure:
  Panel A – grouped bars for tuning & stable geomean improvement vs Fixed.
  Panel B – Pareto scatter: x = total session cost (USD), y = stable geomean improvement.

Methods compared:
  Tuxbot App Dual       (llm_dual_app_metrics_final_actor)
  Single-Loop Reasoning (llm_reasoning_app_metrics_final_actor)
  Single-Loop Flash-Lite(llm_app_metrics_final_actor)
  MLOS                  (mlos_50_tuning_only | mlos)
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.transforms import Bbox
from matplotlib.transforms import ScaledTranslation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_full_performance_table import (
    detect_metric_goal_from_any_tuner,
    detect_metric_goal_from_fixed,
    history_run_completed_successfully,
    improvement_pct,
    iter_history_files,
    load_json,
    phase_mean_for_run,
    resolve_fixed_dirs,
    resolve_tuner_dir,
    resolve_workload_dirs,
)
from cost_helpers import (
    CostSummary,
    collect_tuner_cost,
    efficiency_score,
    load_pricing_map,
    merge_cost_summaries,
)


DEFAULT_METHODS = [
    ("TuxBot", "llm_dual_app_metrics_final_actor"),
    ("TuxBot no Xapian", "llm_dual_app_metrics_final_actor"),
    ("MLOS + TuxBot", "mlos_trimming_aggressive|mlos_trimming"),
    ("MLOS + TuxBot no Xapian", "mlos_trimming_aggressive|mlos_trimming"),
    ("Single-Reasoning", "llm_reasoning_app_metrics_final_actor"),
    ("Single-Reasoning no Xapian", "llm_reasoning_app_metrics_final_actor"),
    ("Single-Instant", "llm_app_metrics_final_actor"),
    ("Single-Instant no Xapian", "llm_app_metrics_final_actor"),
    ("MLOS", "mlos_50_tuning_only|mlos"),
    ("MLOS no Xapian", "mlos_50_tuning_only|mlos"),
]

VARIANT_SUFFIXES = (" no Xapian", " no Catastrophic")

# Okabe-Ito-inspired palette for strong colorblind robustness.
COLOR_BLUE = "#0072B2"
COLOR_ORANGE = "#E69F00"
COLOR_GREEN = "#009E73"
COLOR_VERMILLION = "#D55E00"
COLOR_PURPLE = "#CC79A7"
COLOR_DARK_GRAY = "#4D4D4D"
BOTTOM_TRIM_PTS = 9.0

COLORS = {
    "TuxBot": COLOR_BLUE,
    "Single-Reasoning": COLOR_VERMILLION,
    "Single-Instant": COLOR_GREEN,
    "MLOS": COLOR_ORANGE,
    "MLOS + TuxBot": COLOR_PURPLE,
}
MARKERS = {
    "TuxBot": "D",
    "Single-Reasoning": "s",
    "Single-Instant": "^",
    "MLOS": "H",
    "MLOS + TuxBot": "P",
}
MLOS_TRIM_COST_RATIO = 0.36
MLOS_TRIM_DEFAULT_COST_PER_RUN = 0.404976 * MLOS_TRIM_COST_RATIO
PREDEFINED_COST_PER_RUN = {
    "TuxBot": 0.20,
    "Single-Reasoning": 0.42,
}
CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config" / "full_param"


def _base_method_label(label: str) -> str:
    suffix = _variant_suffix(label)
    return label[:-len(suffix)] if suffix else label


def _variant_suffix(label: str) -> Optional[str]:
    for suffix in VARIANT_SUFFIXES:
        if label.endswith(suffix):
            return suffix
    return None


def _display_label(label: str) -> str:
    base = _base_method_label(label)
    if base == "MLOS + TuxBot":
        return "TuxBot-Trim"
    return base


def _excluded_workloads_for_label(label: str) -> set[str]:
    if label.endswith(" no Xapian"):
        return {"xapian_hi_p99"}
    if label.endswith(" no Catastrophic"):
        return {"xapian_hi_p99", "mutilate_high"}
    return set()


def _collect_unique(values: Sequence[object]) -> List[str]:
    out = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out.append(text)
    return sorted(set(out))


def _format_model_summary(config: dict) -> str:
    actor = config.get("llm_actor_model")
    spec = config.get("llm_speculator_model")
    llm = config.get("llm_model_name")
    trim = config.get("trimming_model_name")

    parts: List[str] = []
    if actor:
        parts.append(f"actor={actor}")
    if spec:
        parts.append(f"speculator={spec}")
    if llm and not actor:
        parts.append(f"model={llm}")
    if trim and trim != llm:
        parts.append(f"trim={trim}")
    if not parts and llm:
        parts.append(f"model={llm}")
    return "; ".join(parts) if parts else "N/A"


def _load_method_metadata(workload_names: Sequence[str], dir_spec: str) -> Dict[str, object]:
    candidates = [item.strip() for item in dir_spec.split("|") if item.strip()]
    matched: List[dict] = []

    for workload in workload_names:
        workload_dir = CONFIG_ROOT / workload
        if not workload_dir.is_dir():
            continue
        for config_path in sorted(workload_dir.glob("*.json")):
            data = load_json(config_path)
            if not isinstance(data, dict):
                continue
            results_dir = str(data.get("results_dir") or "").strip()
            result_leaf = PurePosixPath(results_dir.rstrip("/")).name if results_dir else ""
            if any(config_path.stem.endswith(f"_{cand}") or result_leaf == cand for cand in candidates):
                matched.append(data)
                break

    tuner_type = ", ".join(_collect_unique(cfg.get("tuner_type") for cfg in matched)) or "N/A"
    experiment_profile = ", ".join(_collect_unique(cfg.get("experiment_profile") for cfg in matched)) or "N/A"
    optimization_metric = ", ".join(_collect_unique(cfg.get("optimization_metric") for cfg in matched)) or "N/A"
    optimization_goal = ", ".join(_collect_unique(cfg.get("optimization_goal") for cfg in matched)) or "N/A"
    llm_model_name = ", ".join(_collect_unique(cfg.get("llm_model_name") for cfg in matched)) or "N/A"
    llm_actor_model = ", ".join(_collect_unique(cfg.get("llm_actor_model") for cfg in matched)) or "N/A"
    llm_speculator_model = ", ".join(_collect_unique(cfg.get("llm_speculator_model") for cfg in matched)) or "N/A"
    trimming_model_name = ", ".join(_collect_unique(cfg.get("trimming_model_name") for cfg in matched)) or "N/A"
    model_summary = " | ".join(_collect_unique(_format_model_summary(cfg) for cfg in matched)) or "N/A"

    return {
        "config_matches": len(matched),
        "tuner_type": tuner_type,
        "experiment_profile": experiment_profile,
        "optimization_metric": optimization_metric,
        "optimization_goal": optimization_goal,
        "llm_model_name": llm_model_name,
        "llm_actor_model": llm_actor_model,
        "llm_speculator_model": llm_speculator_model,
        "trimming_model_name": trimming_model_name,
        "model_summary": model_summary,
    }


def _resolve_fixed_dir_for_workload(workload_dir: Path, fallback_fixed_map: Dict[str, Path]) -> Optional[Path]:
    local_fixed = workload_dir / "fixed"
    if local_fixed.is_dir():
        return local_fixed
    return fallback_fixed_map.get(workload_dir.name)


def _compute_25pct_axis_bounds(values: Sequence[float], errors: Sequence[Optional[Tuple[float, float]]]) -> Tuple[float, float]:
    extents: List[float] = [0.0]
    for value, err in zip(values, errors):
        extents.append(float(value))
        if err is not None:
            extents.extend([float(value) - float(err[0]), float(value) + float(err[1])])
    lo = min(extents)
    hi = max(extents)
    tick_lo = 25.0 * math.floor((lo - 5.0) / 25.0)
    tick_hi = 25.0 * math.ceil((hi + 5.0) / 25.0)
    if tick_hi <= tick_lo:
        tick_hi = tick_lo + 25.0
    return tick_lo, tick_hi


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot 5.4: dual vs single loop + cost Pareto.")
    p.add_argument("--result-paths", nargs="+", required=True,
                   help="Retry result roots.")
    p.add_argument("--fallback-fixed-paths", nargs="+", required=True,
                   help="Fixed baseline roots (e.g. *_new).")
    p.add_argument("--custom-columns", default=None,
                   help="Override method spec: 'LABEL:DIRNAME,...'")
    p.add_argument(
        "--workloads",
        default="",
        help="Optional comma-separated workload names to include, in output order.",
    )
    p.add_argument("--tuning-window", default="1-30")
    p.add_argument("--stable-window", default="31-50")
    p.add_argument("--aggregate-stat", choices=("geomean", "geomedian", "trimmed-geomean"),
                   default="geomean")
    p.add_argument("--trim-fraction", type=float, default=0.10)
    p.add_argument("--subset", choices=("common", "union"), default="common")
    p.add_argument("--pricing-map", default=None, help="Override pricing JSON path.")
    p.add_argument(
        "--cost-summary-csv",
        default="",
        help=(
            "Optional CSV with measured per-session costs keyed by method label. "
            "When provided, provider_total_cost_usd or cost_per_run_usd overrides the Pareto x-axis cost."
        ),
    )
    p.add_argument("--plot-output", required=True)
    p.add_argument("--csv-output", default="")
    p.add_argument(
        "--ymin",
        type=float,
        default=None,
        help="Optional lower y-axis bound for the left improvement panel.",
    )
    p.add_argument(
        "--ymax",
        type=float,
        default=None,
        help="Optional upper y-axis bound for the left improvement panel.",
    )
    p.add_argument(
        "--yticks",
        nargs="*",
        type=float,
        default=[],
        help="Optional explicit y-ticks for the left improvement panel.",
    )
    p.add_argument(
        "--error-bars",
        action="store_true",
        help="Add +/-1σ error bars across benchmark-level improvement factors on the left panel.",
    )
    return p.parse_args()


def parse_window(spec: str) -> Tuple[int, int]:
    a, b = spec.split("-")
    return int(a), int(b)


def parse_methods(custom: Optional[str]) -> List[Tuple[str, str]]:
    if custom:
        methods = []
        for item in custom.split(","):
            label, dirs = item.split(":", 1)
            methods.append((label.strip(), dirs.strip()))
        return methods
    return list(DEFAULT_METHODS)


def load_cost_summary_overrides(path_str: str) -> Dict[str, Dict[str, float]]:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.is_file():
        raise SystemExit(f"--cost-summary-csv does not exist: {path}")
    overrides: Dict[str, Dict[str, float]] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("method") or row.get("backend") or "").strip()
            if not label:
                continue
            entry: Dict[str, float] = {}
            for src_key, dst_key in (
                ("provider_total_cost_usd", "total_cost_usd"),
                ("total_cost_usd", "total_cost_usd"),
                ("cost_per_run_usd", "cost_per_run_usd"),
            ):
                raw_value = row.get(src_key)
                if raw_value in (None, ""):
                    continue
                try:
                    entry[dst_key] = float(raw_value)
                except ValueError:
                    continue
            if entry:
                overrides[label] = entry
    return overrides


def resolve_tuner_dirs_multi(workload_dir: Path, dir_spec: str) -> Optional[Path]:
    for candidate in dir_spec.split("|"):
        d = resolve_tuner_dir(workload_dir, candidate.strip())
        if d is not None:
            return d
    return None


def aggregate_factors(factors: Sequence[float], method: str, trim_fraction: float) -> Optional[float]:
    vals = [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not vals:
        return None
    logs = np.log(np.array(vals, dtype=float))
    if method == "geomean":
        agg_log = float(np.mean(logs))
    elif method == "geomedian":
        agg_log = float(np.median(logs))
    else:
        logs_sorted = np.sort(logs)
        trim = int(len(logs_sorted) * trim_fraction)
        if trim > 0 and len(logs_sorted) > 2 * trim:
            logs_sorted = logs_sorted[trim:-trim]
        agg_log = float(np.mean(logs_sorted))
    return math.exp(agg_log)


def pct_to_factor(pct: Optional[float]) -> Optional[float]:
    if pct is None:
        return None
    factor = 1.0 + (float(pct) / 100.0)
    if not math.isfinite(factor) or factor <= 0:
        return None
    return factor


def collect_phase_run_means(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
    ) -> List[float]:
    values: List[float] = []
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        value = phase_mean_for_run(data, metric_name, window)
        if value is not None:
            values.append(float(value))
    return values


def paired_improvement_series(
    fixed_dir: Path,
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
    goal: str,
) -> List[float]:
    fixed_run_means = collect_phase_run_means(fixed_dir, metric_name, window)
    tuner_run_means = collect_phase_run_means(tuner_dir, metric_name, window)
    paired_count = min(len(fixed_run_means), len(tuner_run_means))
    if paired_count <= 0:
        return []
    improvements: List[float] = []
    for idx in range(paired_count):
        pct = improvement_pct(fixed_run_means[idx], tuner_run_means[idx], goal)
        if pct is not None:
            improvements.append(float(pct))
    return improvements


def aggregate_improvement_series(
    workload_series: Sequence[Sequence[float]],
    method: str,
    trim_fraction: float,
) -> List[float]:
    nonempty_series = [list(series) for series in workload_series if series]
    if not nonempty_series:
        return []
    paired_count = min(len(series) for series in nonempty_series)
    if paired_count <= 0:
        return []
    aggregate_series: List[float] = []
    for idx in range(paired_count):
        factors = [pct_to_factor(series[idx]) for series in nonempty_series]
        aggregate_factor = aggregate_factors(
            [factor for factor in factors if factor is not None],
            method,
            trim_fraction,
        )
        if aggregate_factor is None:
            continue
        aggregate_series.append((aggregate_factor - 1.0) * 100.0)
    return aggregate_series


def summarize_series(values: Sequence[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    numeric = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not numeric:
        return None, None, None
    mean_pct = statistics.fmean(numeric)
    err = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
    factors = [pct_to_factor(v) for v in numeric]
    factor_values = [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0]
    mean_factor = statistics.fmean(factor_values) if factor_values else None
    return mean_pct, err, mean_factor


def write_detailed_report(
    path: Path,
    results: Dict[str, Dict[str, object]],
    by_benchmark_rows: Sequence[Dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Cost Analysis Details")
    lines.append("")
    lines.append("## Aggregate Summary")
    lines.append("")
    lines.append("| Method | Models | Objective | Goal | Tuning % | Stable % | Total Cost ($) | $/Run | $/Action | #Runs | #Actions | #BM | Benchmarks |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for label, data in results.items():
        tuning = "N/A" if data["tuning_pct"] is None else f"{float(data['tuning_pct']):+.2f}"
        stable = "N/A" if data["stable_pct"] is None else f"{float(data['stable_pct']):+.2f}"
        lines.append(
            f"| {label} | {data['model_summary']} | {data['optimization_metric']} | {data['optimization_goal']} | "
            f"{tuning} | {stable} | {float(data['total_cost_usd']):.6f} | {float(data['cost_per_run_usd']):.6f} | "
            f"{float(data['avg_cost_per_action']):.6f} | {int(data['run_count'])} | {int(data['action_count'])} | "
            f"{int(data['n_benchmarks'])} | {data['benchmarks']} |"
        )
    lines.append("")
    lines.append("## Method Metadata")
    lines.append("")
    lines.append("| Method | Tuner Type | Experiment Profile | llm_model_name | llm_actor_model | llm_speculator_model | trimming_model_name | Config Matches |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | ---: |")
    for label, data in results.items():
        lines.append(
            f"| {label} | {data['tuner_type']} | {data['experiment_profile']} | "
            f"{data['llm_model_name']} | {data['llm_actor_model']} | {data['llm_speculator_model']} | "
            f"{data['trimming_model_name']} | {int(data['config_matches'])} |"
        )
    lines.append("")
    lines.append("## Per-Benchmark Detail")
    lines.append("")
    lines.append("| Benchmark | Method | Models | Objective | Goal | Tuning % | Stable % | Total Cost ($) | $/Action | Actions | Tuning Runs | Stable Runs |")
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in sorted(by_benchmark_rows, key=lambda r: (str(r["benchmark"]), str(r["method"]))):
        tuning = "N/A" if row["tuning_geomean_pct"] is None else f"{float(row['tuning_geomean_pct']):+.2f}"
        stable = "N/A" if row["stable_geomean_pct"] is None else f"{float(row['stable_geomean_pct']):+.2f}"
        lines.append(
            f"| {row['benchmark']} | {row['method']} | {row['model_summary']} | {row['optimization_metric']} | "
            f"{row['optimization_goal']} | {tuning} | {stable} | {float(row['total_cost_usd']):.6f} | "
            f"{float(row['avg_cost_per_action']):.6f} | {int(row['action_count'])} | "
            f"{int(row['completed_runs_tuning'])} | {int(row['completed_runs_stable'])} |"
        )
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()
    tuning_win = parse_window(args.tuning_window)
    stable_win = parse_window(args.stable_window)
    methods = parse_methods(args.custom_columns)
    pricing = load_pricing_map(Path(args.pricing_map) if args.pricing_map else None)
    cost_overrides = load_cost_summary_overrides(args.cost_summary_csv)

    workload_dirs = resolve_workload_dirs(args.result_paths)
    fixed_map = resolve_fixed_dirs(args.fallback_fixed_paths)

    workload_names = sorted({wd.name for wd in workload_dirs})
    workload_map = {wd.name: wd for wd in workload_dirs}
    if args.workloads:
        requested = [w.strip() for w in args.workloads.split(",") if w.strip()]
        workload_names = [
            w for w in requested
            if w in workload_map and _resolve_fixed_dir_for_workload(workload_map[w], fixed_map) is not None
        ]

    # -- Collect workload/method phase series and workload-level costs --
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    method_workload_costs: Dict[str, Dict[str, Tuple[CostSummary, int]]] = {label: {} for label, _ in methods}

    for wname in workload_names:
        wdir = workload_map.get(wname)
        fdir = _resolve_fixed_dir_for_workload(wdir, fixed_map)
        if wdir is None or fdir is None:
            continue

        metric_name, goal = detect_metric_goal_from_fixed(fdir, wname)
        if metric_name is None:
            metric_name, goal = detect_metric_goal_from_any_tuner(wdir, wname)
        if metric_name is None or goal is None:
            continue

        by_method: Dict[str, Dict[str, List[float]]] = {}
        for label, dir_spec in methods:
            tuner_dir = resolve_tuner_dirs_multi(wdir, dir_spec)
            if tuner_dir is None:
                continue

            tuning_series = paired_improvement_series(
                fixed_dir=fdir,
                tuner_dir=tuner_dir,
                metric_name=metric_name,
                window=tuning_win,
                goal=goal,
            )
            stable_series = paired_improvement_series(
                fixed_dir=fdir,
                tuner_dir=tuner_dir,
                metric_name=metric_name,
                window=stable_win,
                goal=goal,
            )

            phase_series: Dict[str, List[float]] = {}
            if tuning_series:
                phase_series["tuning"] = tuning_series
            if stable_series:
                phase_series["stable"] = stable_series
            if phase_series:
                by_method[label] = phase_series

            wk_cost = collect_tuner_cost(tuner_dir, start=tuning_win[0], end=tuning_win[1], pricing=pricing)
            method_workload_costs[label][wname] = (wk_cost, len(wk_cost.iterations))

        if by_method:
            workload_phase_series[wname] = by_method

    labels = [label for label, _ in methods]
    method_metadata = {
        label: _load_method_metadata(workload_names, dir_spec)
        for label, dir_spec in methods
    }
    phase_workloads: Dict[str, Dict[str, List[str]]] = {"tuning": {}, "stable": {}}
    for phase in ("tuning", "stable"):
        if args.subset == "common":
            common_workloads = [
                workload
                for workload, method_map in workload_phase_series.items()
                if all(phase in method_map.get(label, {}) for label in labels)
            ]
            for label in labels:
                excluded = _excluded_workloads_for_label(label)
                phase_workloads[phase][label] = [
                    workload for workload in common_workloads if workload not in excluded
                ]
        else:
            for label in labels:
                excluded = _excluded_workloads_for_label(label)
                phase_workloads[phase][label] = [
                    workload
                    for workload, method_map in workload_phase_series.items()
                    if phase in method_map.get(label, {})
                    and workload not in excluded
                ]

    # -- Compute per-method aggregates --
    results: Dict[str, Dict[str, object]] = {}
    by_benchmark_rows: List[Dict[str, object]] = []
    for label in labels:
        tuning_workload_series = [
            workload_phase_series[w][label]["tuning"]
            for w in phase_workloads["tuning"][label]
        ]
        stable_workload_series = [
            workload_phase_series[w][label]["stable"]
            for w in phase_workloads["stable"][label]
        ]

        tuning_aggregate_series = aggregate_improvement_series(
            tuning_workload_series,
            args.aggregate_stat,
            args.trim_fraction,
        )
        stable_aggregate_series = aggregate_improvement_series(
            stable_workload_series,
            args.aggregate_stat,
            args.trim_fraction,
        )

        tuning_pct_mean, tuning_pct_sd, tuning_factor_mean = summarize_series(tuning_aggregate_series)
        stable_pct_mean, stable_pct_sd, stable_factor_mean = summarize_series(stable_aggregate_series)

        cost_workloads = sorted(set(phase_workloads["tuning"][label]) | set(phase_workloads["stable"][label]))
        total_cost = CostSummary(source="none")
        action_count = 0
        for wname in cost_workloads:
            wk = method_workload_costs.get(label, {}).get(wname)
            if wk is None:
                continue
            wk_cost, wk_actions = wk
            merge_cost_summaries(total_cost, wk_cost)
            action_count += wk_actions

            tuning_series = workload_phase_series.get(wname, {}).get(label, {}).get("tuning", [])
            stable_series = workload_phase_series.get(wname, {}).get(label, {}).get("stable", [])
            t_pct_mean, t_pct_sd, t_factor_mean = summarize_series(tuning_series)
            s_pct_mean, s_pct_sd, s_factor_mean = summarize_series(stable_series)
            avg_cost_per_action = wk_cost.combined.cost_usd / max(1, wk_actions)
            by_benchmark_rows.append(
                {
                    "benchmark": wname,
                    "method": label,
                    "tuner_type": method_metadata[label]["tuner_type"],
                    "experiment_profile": method_metadata[label]["experiment_profile"],
                    "optimization_metric": method_metadata[label]["optimization_metric"],
                    "optimization_goal": method_metadata[label]["optimization_goal"],
                    "model_summary": method_metadata[label]["model_summary"],
                    "llm_model_name": method_metadata[label]["llm_model_name"],
                    "llm_actor_model": method_metadata[label]["llm_actor_model"],
                    "llm_speculator_model": method_metadata[label]["llm_speculator_model"],
                    "trimming_model_name": method_metadata[label]["trimming_model_name"],
                    "tuning_geomean_pct": t_pct_mean,
                    "tuning_geomean_pct_err_low": t_pct_sd,
                    "tuning_geomean_pct_err_high": t_pct_sd,
                    "stable_geomean_pct": s_pct_mean,
                    "stable_geomean_pct_err_low": s_pct_sd,
                    "stable_geomean_pct_err_high": s_pct_sd,
                    "tuning_geomean_factor": t_factor_mean,
                    "stable_geomean_factor": s_factor_mean,
                    "total_cost_usd": wk_cost.combined.cost_usd,
                    "avg_cost_per_action": avg_cost_per_action,
                    "action_count": wk_actions,
                    "efficiency": efficiency_score(s_factor_mean, wk_cost.combined.cost_usd),
                    "completed_runs_tuning": len(tuning_series),
                    "completed_runs_stable": len(stable_series),
                }
            )

        run_count = len(stable_aggregate_series) if stable_aggregate_series else len(tuning_aggregate_series)
        base_label = _base_method_label(label)
        override_cost = cost_overrides.get(label)
        plotted_cost_per_run = (
            float(override_cost["cost_per_run_usd"])
            if override_cost is not None and "cost_per_run_usd" in override_cost
            else (total_cost.combined.cost_usd / max(1, run_count))
        )
        reported_total_cost = (
            float(override_cost["total_cost_usd"])
            if override_cost is not None and "total_cost_usd" in override_cost
            else total_cost.combined.cost_usd
        )
        avg_cost_per_action = reported_total_cost / max(1, action_count)

        if base_label in PREDEFINED_COST_PER_RUN:
            plotted_cost_per_run = PREDEFINED_COST_PER_RUN[base_label]

        # MLOS trimming cost is estimated as 10 actor calls ~= 36% of Single-Reasoning.
        if base_label == "MLOS + TuxBot":
            reference_cost = cost_overrides.get("Single-Reasoning")
            if reference_cost is not None and "cost_per_run_usd" in reference_cost:
                reference_cost = float(reference_cost["cost_per_run_usd"])
            elif "Single-Reasoning" in results:
                reference_cost = float(results["Single-Reasoning"]["cost_per_run_usd"])
            else:
                reference_cost = MLOS_TRIM_DEFAULT_COST_PER_RUN / MLOS_TRIM_COST_RATIO
            estimated_trim_cost = float(reference_cost) * MLOS_TRIM_COST_RATIO
            plotted_cost_per_run = estimated_trim_cost
            reported_total_cost = estimated_trim_cost
            action_count = 10
            avg_cost_per_action = estimated_trim_cost / 10.0

        # Dashed subset overlays should share the same x-position/cost as the full-set counterpart.
        if _variant_suffix(label) is not None and base_label in results:
            plotted_cost_per_run = float(results[base_label]["cost_per_run_usd"])
            reported_total_cost = float(results[base_label]["total_cost_usd"])
            avg_cost_per_action = float(results[base_label]["avg_cost_per_action"])

        results[label] = {
            **method_metadata[label],
            "tuning_geomean": tuning_factor_mean,
            "tuning_pct": tuning_pct_mean,
            "tuning_pct_err": None if tuning_pct_sd is None else (tuning_pct_sd, tuning_pct_sd),
            "stable_geomean": stable_factor_mean,
            "stable_pct": stable_pct_mean,
            "stable_pct_err": None if stable_pct_sd is None else (stable_pct_sd, stable_pct_sd),
            "total_cost_usd": reported_total_cost,
            "cost_per_run_usd": plotted_cost_per_run,
            "avg_cost_per_action": avg_cost_per_action,
            "action_count": action_count,
            "run_count": run_count,
            "n_benchmarks": len(cost_workloads),
            "benchmarks": ",".join(cost_workloads),
            "efficiency": efficiency_score(
                stable_factor_mean,
                plotted_cost_per_run,
            ),
        }

    # -- Print CLI summary --
    if args.subset == "common":
        common_set = None
        for label, data in results.items():
            bms = set(str(data["benchmarks"]).split(",")) if data["benchmarks"] else set()
            common_set = bms if common_set is None else common_set & bms
        print(f"Common benchmark subset: {sorted(common_set) if common_set else '(empty)'}")
    print()
    hdr = f"{'Method':<30s} {'Tuning %':>10s} {'Stable %':>10s} {'Cost ($)':>10s} {'$/Run':>10s} {'$/Action':>10s} {'#Runs':>6s} {'#Actions':>8s} {'η':>10s} {'#BM':>4s}"
    print(hdr)
    print("-" * len(hdr))
    for label, data in results.items():
        tp = f"{data['tuning_pct']:+.1f}" if data['tuning_pct'] is not None else "N/A"
        sp = f"{data['stable_pct']:+.1f}" if data['stable_pct'] is not None else "N/A"
        cost = f"${data['total_cost_usd']:.4f}"
        per_run = f"${data['cost_per_run_usd']:.6f}"
        avg = f"${data['avg_cost_per_action']:.6f}"
        runs = str(data['run_count'])
        actions = str(data['action_count'])
        eff = f"{data['efficiency']:.2f}" if data['efficiency'] is not None else "N/A"
        print(f"{label:<30s} {tp:>10s} {sp:>10s} {cost:>10s} {per_run:>10s} {avg:>10s} {runs:>6s} {actions:>8s} {eff:>10s} {data['n_benchmarks']:>4d}")
    print()

    # -- Plot --
    FS = 29
    Y_LABEL_FS = FS - 1
    Y_TICK_FS = FS - 1
    COST_X_TICK_FS = FS - 1
    labels = list(results.keys())
    base_labels: List[str] = []
    seen_bases: set[str] = set()
    for label in labels:
        base = _base_method_label(label)
        if base not in seen_bases:
            seen_bases.add(base)
            base_labels.append(base)
    colors = {
        base: COLORS.get(base, COLOR_DARK_GRAY)
        for base in base_labels
    }

    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(11.52, 4.08),
        sharey=True,
        gridspec_kw={"width_ratios": [3, 2]},
    )

    # Panel A: grouped bars
    x = np.arange(len(base_labels), dtype=float)
    x_by_base = {base: x[idx] for idx, base in enumerate(base_labels)}
    width = 0.35

    def _plot_method_bars(label: str) -> None:
        base = _base_method_label(label)
        data = results[label]
        tuning_pct = data["tuning_pct"] or 0.0
        stable_pct = data["stable_pct"] or 0.0
        tuning_yerr = None
        stable_yerr = None
        if args.error_bars:
            tuning_err = 0.0 if data["tuning_pct_err"] is None else float(data["tuning_pct_err"][0])
            stable_err = 0.0 if data["stable_pct_err"] is None else float(data["stable_pct_err"][0])
            tuning_yerr = np.array([[tuning_err], [tuning_err]], dtype=float)
            stable_yerr = np.array([[stable_err], [stable_err]], dtype=float)

        dashed_variant = _variant_suffix(label) is not None
        if dashed_variant:
            tuning_yerr = None
            stable_yerr = None
        xpos = x_by_base[base]
        tuning_facecolor = "none" if dashed_variant else colors[base]
        stable_facecolor = "none" if dashed_variant else colors[base]
        tuning_edgecolor = colors[base] if dashed_variant else "black"
        stable_edgecolor = colors[base] if dashed_variant else "black"
        linewidth = 1.8 if dashed_variant else 0.8
        linestyle = "--" if dashed_variant else "-"
        error_color = tuning_edgecolor if dashed_variant else "black"

        ax1.bar(
            xpos - width / 2,
            [tuning_pct],
            width,
            color=tuning_facecolor,
            edgecolor=tuning_edgecolor,
            linewidth=linewidth,
            alpha=1.0,
            hatch=None if dashed_variant else "//",
            linestyle=linestyle,
            yerr=tuning_yerr,
            capsize=3 if args.error_bars else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": error_color} if args.error_bars else None,
            zorder=3 if dashed_variant else 4,
        )
        ax1.bar(
            xpos + width / 2,
            [stable_pct],
            width,
            color=stable_facecolor,
            edgecolor=stable_edgecolor,
            linewidth=linewidth,
            alpha=1.0,
            linestyle=linestyle,
            yerr=stable_yerr,
            capsize=3 if args.error_bars else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": error_color} if args.error_bars else None,
            zorder=3 if dashed_variant else 4,
        )

    for label in labels:
        if _variant_suffix(label) is not None:
            _plot_method_bars(label)
    for label in labels:
        if _variant_suffix(label) is None:
            _plot_method_bars(label)

    variant_legend_label = "No Xapian"
    if any(label.endswith(" no Catastrophic") for label in labels):
        variant_legend_label = "No Catastrophic"

    ax1.axhline(0, color=COLOR_DARK_GRAY, linestyle="--", linewidth=0.8)
    ax1.set_xticks(x)
    ax1.set_xticklabels([""] * len(base_labels))
    ax1.tick_params(axis="x", length=0)
    ax1.set_ylabel("Improvement %", fontsize=Y_LABEL_FS)
    axis_vals = [float(results[l]["tuning_pct"] or 0.0) for l in labels] + [float(results[l]["stable_pct"] or 0.0) for l in labels]
    axis_errs = [results[l]["tuning_pct_err"] for l in labels] + [results[l]["stable_pct_err"] for l in labels]
    tick_lo, tick_hi = _compute_25pct_axis_bounds(axis_vals, axis_errs)
    if args.ymin is not None:
        tick_lo = float(args.ymin)
    if args.ymax is not None:
        tick_hi = float(args.ymax)
    if args.yticks:
        cost_ticks = [float(tick) for tick in args.yticks]
    else:
        cost_ticks = list(np.arange(tick_lo, tick_hi + 0.1, 25.0))
    ax1.set_yticks(cost_ticks)
    ax1.set_ylim(tick_lo, tick_hi)
    ax1.tick_params(axis="y", labelsize=Y_TICK_FS)
    ax1.grid(axis="y", alpha=0.3, linewidth=0.6)
    ax1.set_axisbelow(True)
    left_marker_offset_transform = ax1.transData + ScaledTranslation(0.0, -9.0 / 72.0, fig.dpi_scale_trans)
    for base in base_labels:
        marker_y = -36.0
        ax1.scatter(
            x_by_base[base],
            marker_y,
            s=380,
            marker=MARKERS.get(base, "o"),
            c=colors[base],
            edgecolors=COLOR_DARK_GRAY,
            linewidths=0.8,
            zorder=6,
            transform=left_marker_offset_transform,
        )
    ax1.legend(
        handles=[
            Patch(facecolor="white", edgecolor="black", hatch="//", label="Tuning"),
            Patch(facecolor="white", edgecolor=COLOR_DARK_GRAY, label="Stable"),
            Patch(facecolor="white", edgecolor=COLOR_DARK_GRAY, linestyle="--", linewidth=1.6, label=variant_legend_label),
        ],
        frameon=False,
        fontsize=FS - 4,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.02),
        bbox_transform=ax1.transAxes + ScaledTranslation(-18.0 / 72.0, -8.0 / 72.0, fig.dpi_scale_trans),
        ncol=3,
        handlelength=1.2,
        handleheight=0.8,
        handletextpad=0.23,
        columnspacing=0.12,
        borderaxespad=0.0,
    )
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    # Panel B: Pareto scatter
    annotation_style = {
        "TuxBot": {"xytext": (18, -2), "ha": "left", "va": "center"},
        "Single-Reasoning": {"xytext": (7, 33), "ha": "right", "va": "bottom"},
        "Single-Instant": {"xytext": (11, 0), "ha": "left", "va": "center"},
        "MLOS": {"xytext": (-8, -10), "ha": "left", "va": "top"},
        "MLOS + TuxBot": {"xytext": (11, 8), "ha": "left", "va": "top"},
    }
    for i, l in enumerate(labels):
        base = _base_method_label(l)
        sp = float(results[l]["stable_pct"] or 0.0)
        c = float(results[l]["cost_per_run_usd"])
        dashed_variant = _variant_suffix(l) is not None
        if dashed_variant:
            ax2.scatter(
                c,
                sp,
                s=440,
                facecolors="none",
                edgecolors=colors[base],
                marker=MARKERS.get(base, "o"),
                linewidths=2.4,
                linestyle="--",
                zorder=5,
            )
            continue
        ax2.scatter(
            c,
            sp,
            s=440,
            c=colors[base],
            marker=MARKERS.get(base, "o"),
            edgecolors=COLOR_DARK_GRAY,
            linewidths=0.8,
            zorder=6,
            label=base,
        )
        style = annotation_style.get(base, {"xytext": (8, 8), "ha": "left", "va": "bottom"})
        ax2.annotate(
            _display_label(base),
            (c, sp),
            textcoords="offset points",
            xytext=style["xytext"],
            fontsize=FS - 6,
            ha=style["ha"],
            va=style["va"],
        )
    ax2.axhline(0, color=COLOR_DARK_GRAY, linestyle="--", linewidth=0.8)
    ax2.set_xlabel("")
    xticks = [0.0, 0.2, 0.4]
    ax2.set_xticks(xticks)
    ax2.set_xticklabels(["$0.0", "$0.2", "$0.4"], fontsize=COST_X_TICK_FS)
    ax2.set_xlim(-0.03, 0.45)
    ax2.set_ylabel("")
    ax2.tick_params(axis="x", labelsize=COST_X_TICK_FS)
    ax2.tick_params(axis="y", labelsize=Y_TICK_FS, labelleft=False, left=True, length=4, width=0.8)
    ax2.grid(axis="both", alpha=0.3, linewidth=0.6)
    ax2.set_axisbelow(True)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    output_path = Path(args.plot_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.5, rect=(0, 0.0, 1, 1))
    fig.subplots_adjust(wspace=0.16)
    fig.canvas.draw()
    tight_bbox = fig.get_tightbbox(fig.canvas.get_renderer())
    trimmed_bbox = Bbox.from_extents(
        tight_bbox.x0,
        tight_bbox.y0 + BOTTOM_TRIM_PTS / 72.0,
        tight_bbox.x1,
        tight_bbox.y1,
    )
    fig.savefig(output_path, bbox_inches=trimmed_bbox, pad_inches=0.0)
    plt.close(fig)
    print(f"Saved plot: {output_path}")

    # -- Optional CSV --
    if args.csv_output:
        csv_path = Path(args.csv_output)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "method", "tuning_geomean_pct", "stable_geomean_pct",
                "tuning_geomean_pct_err_low", "tuning_geomean_pct_err_high",
                "stable_geomean_pct_err_low", "stable_geomean_pct_err_high",
                "tuner_type", "experiment_profile", "optimization_metric", "optimization_goal",
                "model_summary", "llm_model_name", "llm_actor_model", "llm_speculator_model", "trimming_model_name",
                "config_matches",
                "total_cost_usd", "cost_per_run_usd", "avg_cost_per_action", "action_count", "run_count",
                "efficiency", "n_benchmarks", "benchmarks",
            ])
            w.writeheader()
            for label, data in results.items():
                w.writerow({
                    "method": label,
                    "tuning_geomean_pct": data["tuning_pct"],
                    "stable_geomean_pct": data["stable_pct"],
                    "tuning_geomean_pct_err_low": None if data["tuning_pct_err"] is None else data["tuning_pct_err"][0],
                    "tuning_geomean_pct_err_high": None if data["tuning_pct_err"] is None else data["tuning_pct_err"][1],
                    "stable_geomean_pct_err_low": None if data["stable_pct_err"] is None else data["stable_pct_err"][0],
                    "stable_geomean_pct_err_high": None if data["stable_pct_err"] is None else data["stable_pct_err"][1],
                    "tuner_type": data["tuner_type"],
                    "experiment_profile": data["experiment_profile"],
                    "optimization_metric": data["optimization_metric"],
                    "optimization_goal": data["optimization_goal"],
                    "model_summary": data["model_summary"],
                    "llm_model_name": data["llm_model_name"],
                    "llm_actor_model": data["llm_actor_model"],
                    "llm_speculator_model": data["llm_speculator_model"],
                    "trimming_model_name": data["trimming_model_name"],
                    "config_matches": data["config_matches"],
                    "total_cost_usd": data["total_cost_usd"],
                    "cost_per_run_usd": data["cost_per_run_usd"],
                    "avg_cost_per_action": data["avg_cost_per_action"],
                    "action_count": data["action_count"],
                    "run_count": data["run_count"],
                    "efficiency": data["efficiency"],
                    "n_benchmarks": data["n_benchmarks"],
                    "benchmarks": data["benchmarks"],
                })
        print(f"Saved CSV: {csv_path}")

        by_benchmark_csv = csv_path.with_name(f"{csv_path.stem}_by_benchmark{csv_path.suffix}")
        with by_benchmark_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "benchmark",
                "method",
                "tuner_type",
                "experiment_profile",
                "optimization_metric",
                "optimization_goal",
                "model_summary",
                "llm_model_name",
                "llm_actor_model",
                "llm_speculator_model",
                "trimming_model_name",
                "tuning_geomean_pct",
                "tuning_geomean_pct_err_low",
                "tuning_geomean_pct_err_high",
                "stable_geomean_pct",
                "stable_geomean_pct_err_low",
                "stable_geomean_pct_err_high",
                "tuning_geomean_factor",
                "stable_geomean_factor",
                "total_cost_usd",
                "avg_cost_per_action",
                "action_count",
                "efficiency",
                "completed_runs_tuning",
                "completed_runs_stable",
            ])
            w.writeheader()
            for row in sorted(by_benchmark_rows, key=lambda r: (str(r["benchmark"]), str(r["method"]))):
                w.writerow(row)
        print(f"Saved per-benchmark CSV: {by_benchmark_csv}")

        report_path = csv_path.with_name(f"{csv_path.stem}_report.md")
        write_detailed_report(report_path, results, by_benchmark_rows)
        print(f"Saved detailed report: {report_path}")


if __name__ == "__main__":
    main()
