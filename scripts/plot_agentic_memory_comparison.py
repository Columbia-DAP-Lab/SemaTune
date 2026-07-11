#!/usr/bin/env python3
"""Plot a two-method agentic comparison figure with Figure-9-style layout.

Outputs:
1. Main two-panel figure
   (a) Geomean improvement over Fixed during tuning and stable phases.
   (b) Aggregate tuning-phase robustness using P50 bad-window rate, P10
       bad-window rate, and variability.
2. Optional compact aggregate robustness figure across all available workloads
   with both vanilla and memory runs.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

from generate_full_performance_table import (
    WorkloadStats,
    detect_metric_goal_from_any_tuner,
    detect_metric_goal_from_fixed,
    history_run_completed_successfully,
    improvement_pct,
    resolve_fixed_dirs,
    resolve_tuner_dir,
    resolve_workload_dirs,
)
from plot_preconvergence_multiworkload import (
    WindowSpec,
    canonical_goal,
    detect_metric_and_goal,
    iter_history_files,
    load_json,
    pct_worse,
    percentile,
    select_window_values,
)


DEFAULT_EXPERIMENTS = [
    "tpcc_hi_p99",
    "silo_hi_p99",
    "sysbench_oltp_rw_hi_p99",
    "dcperf_spark_tput",
]

DEFAULT_METHODS = [
    ("TuxBot", "llm_dual_app_metrics_final_actor"),
    ("TuxBot+Memory", "llm_dual_app_metrics_memory_final_actor"),
]
DEFAULT_METHOD_COLORS = {
    "TuxBot": "#1f77b4",
    "TuxBot+Memory": "#2ca58d",
}
PHASE_COLORS = {
    "tuning": "#235FA4",
    "stable": "#8EC5F4",
}
ROBUSTNESS_METRICS = [
    ("p50_bad_window_rate_pct", "P50"),
    ("p10_bad_window_rate_pct", "P10"),
    ("variability_pct_of_fixed", "Variability"),
]
ROBUSTNESS_ERROR_KEYS = {
    "p50_bad_window_rate_pct": "p50_bad_window_rate_error_pct",
    "p10_bad_window_rate_pct": "p10_bad_window_rate_error_pct",
    "variability_pct_of_fixed": "variability_pct_of_fixed_error_pct",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare two tuner families with Figure-9-style plots.")
    p.add_argument(
        "--result-paths",
        nargs="*",
        default=[],
        help="Common results roots containing both vanilla and memory tuner dirs.",
    )
    p.add_argument(
        "--vanilla-result-paths",
        nargs="*",
        default=[],
        help="Backward-compatible alias for vanilla result roots.",
    )
    p.add_argument(
        "--memory-result-paths",
        nargs="*",
        default=[],
        help="Backward-compatible alias for memory result roots.",
    )
    p.add_argument("--fallback-fixed-paths", nargs="*", default=[])
    p.add_argument("--experiments", nargs="+", default=list(DEFAULT_EXPERIMENTS))
    p.add_argument(
        "--all-experiments",
        nargs="*",
        default=[],
        help="Optional explicit workload list for aggregate robustness. Defaults to all workloads with both runs.",
    )
    p.add_argument("--tuning-window", default="1-30")
    p.add_argument("--stable-window", default="31-50")
    p.add_argument("--plot-output", required=True)
    p.add_argument("--csv-output", default="")
    p.add_argument(
        "--method-a-label",
        default=DEFAULT_METHODS[0][0],
        help=f"Display label for the first method (default: {DEFAULT_METHODS[0][0]}).",
    )
    p.add_argument(
        "--method-a-dir",
        default=DEFAULT_METHODS[0][1],
        help=f"Result subdirectory for the first method (default: {DEFAULT_METHODS[0][1]}).",
    )
    p.add_argument(
        "--method-a-color",
        default=DEFAULT_METHOD_COLORS[DEFAULT_METHODS[0][0]],
        help="Bar color for the first method.",
    )
    p.add_argument(
        "--method-a-workload-overrides",
        default="",
        help="Optional comma-separated WORKLOAD:DIRSPEC overrides for method A.",
    )
    p.add_argument(
        "--method-b-label",
        default=DEFAULT_METHODS[1][0],
        help=f"Display label for the second method (default: {DEFAULT_METHODS[1][0]}).",
    )
    p.add_argument(
        "--method-b-dir",
        default=DEFAULT_METHODS[1][1],
        help=f"Result subdirectory for the second method (default: {DEFAULT_METHODS[1][1]}).",
    )
    p.add_argument(
        "--method-b-color",
        default=DEFAULT_METHOD_COLORS[DEFAULT_METHODS[1][0]],
        help="Bar color for the second method.",
    )
    p.add_argument(
        "--method-b-workload-overrides",
        default="",
        help="Optional comma-separated WORKLOAD:DIRSPEC overrides for method B.",
    )
    p.add_argument(
        "--aggregate-robustness-plot-output",
        default="",
        help="Optional compact all-workload robustness plot output path.",
    )
    p.add_argument(
        "--aggregate-robustness-csv-output",
        default="",
        help="Optional compact all-workload robustness CSV output path.",
    )
    p.add_argument(
        "--main-robustness-ymax",
        type=float,
        default=None,
        help="Optional y-axis max for the right panel in the main figure.",
    )
    p.add_argument(
        "--main-robustness-yticks",
        nargs="*",
        type=float,
        default=[],
        help="Optional explicit y-ticks for the right panel in the main figure.",
    )
    p.add_argument(
        "--aggregate-robustness-ymax",
        type=float,
        default=None,
        help="Optional y-axis max for the compact aggregate robustness plot.",
    )
    p.add_argument(
        "--aggregate-robustness-yticks",
        nargs="*",
        type=float,
        default=[],
        help="Optional explicit y-ticks for the compact aggregate robustness plot.",
    )
    return p.parse_args()


def parse_window(spec: str) -> Tuple[int, int]:
    lo, hi = spec.split("-", 1)
    return int(lo), int(hi)


def merge_unique_paths(*groups: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for group in groups:
        for raw in group:
            if not raw:
                continue
            p = str(Path(raw).resolve())
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


def benchmark_label(workload: str) -> str:
    mapping = {
        "dcperf_spark_tput": "Spark",
        "masstree_hi_p99": "Masstree",
        "mutilate_high": "Memcached-High",
        "mutilate_low": "Memcached-Low",
        "sibench_hi_p99": "Sibench",
        "silo_hi_p99": "Silo",
        "sphinx_tput_max": "Sphinx",
        "sysbench_cpu_tput": "Sys-CPU",
        "sysbench_oltp_rw_hi_p99": "Sys-OLTP-RW",
        "tpcc_hi_p99": "TPC-C",
        "twitter_p99": "Twitter",
        "wikipedia_p99": "Wikipedia",
        "xapian_hi_p99": "Xapian",
        "ycsb_hi_p99": "YCSB",
    }
    return mapping.get(workload, workload)


def method_display_label(label: str) -> str:
    if label == "Indirect":
        return "No Mem"
    if label == "Indirect+Memory":
        return "Mem"
    if label == "TuxBot":
        return "No Mem"
    if label == "TuxBot+Memory":
        return "Mem"
    if label.endswith("+Memory"):
        return label.replace("+Memory", "+Mem")
    return label


def parse_workload_overrides(spec: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise SystemExit(f"Override entry must be WORKLOAD:DIRSPEC, got: {chunk!r}")
        workload, dirspec = chunk.split(":", 1)
        workload = workload.strip()
        dirspec = dirspec.strip()
        if not workload or not dirspec:
            raise SystemExit(f"Override entry must be WORKLOAD:DIRSPEC, got: {chunk!r}")
        out[workload] = dirspec
    return out


def build_methods(args: argparse.Namespace) -> Tuple[List[Tuple[str, str]], Dict[str, str], Dict[str, Dict[str, str]]]:
    methods = [
        (args.method_a_label, args.method_a_dir),
        (args.method_b_label, args.method_b_dir),
    ]
    labels = [label for label, _ in methods]
    if len(set(labels)) != len(labels):
        raise SystemExit("Method labels must be unique.")
    colors = {
        args.method_a_label: args.method_a_color,
        args.method_b_label: args.method_b_color,
    }
    overrides = {
        args.method_a_label: parse_workload_overrides(args.method_a_workload_overrides),
        args.method_b_label: parse_workload_overrides(args.method_b_workload_overrides),
    }
    return methods, colors, overrides


def resolve_method_tuner_dir(
    workload_dir: Path,
    workload: str,
    label: str,
    default_dirspec: str,
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Optional[Path]:
    overrides = method_workload_overrides.get(label, {})
    dirspec = overrides.get(workload, default_dirspec)
    return resolve_tuner_dir(workload_dir, dirspec)


def aggregate_factor_geomean(factors: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not vals:
        return None
    return float(np.exp(np.mean(np.log(np.array(vals, dtype=float)))))


def pct_to_factor(pct: Optional[float]) -> Optional[float]:
    if pct is None:
        return None
    factor = 1.0 + (float(pct) / 100.0)
    if not math.isfinite(factor) or factor <= 0:
        return None
    return factor


def factor_to_pct(factor: Optional[float]) -> Optional[float]:
    if factor is None:
        return None
    return (float(factor) - 1.0) * 100.0


def aggregate_percent_geomean(values: Sequence[Optional[float]]) -> Optional[float]:
    factors = [1.0 + (float(v) / 100.0) for v in values if v is not None and math.isfinite(float(v))]
    if not factors:
        return None
    if any(f <= 0 for f in factors):
        return None
    return (float(np.exp(np.mean(np.log(np.array(factors, dtype=float))))) - 1.0) * 100.0


def ensure_fixed_map(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
) -> tuple[Dict[str, Path], Dict[str, Path]]:
    workload_dirs = {wd.name: wd for wd in resolve_workload_dirs(result_paths)}
    fixed_map = resolve_fixed_dirs(fallback_fixed_paths)
    for name, wd in workload_dirs.items():
        fixed_dir = wd / "fixed"
        if fixed_dir.is_dir():
            fixed_map.setdefault(name, fixed_dir)
    return workload_dirs, fixed_map


def collect_workload_stats_map(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    experiments: Sequence[str],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Dict[str, WorkloadStats]:
    workload_dirs, fixed_map = ensure_fixed_map(result_paths, fallback_fixed_paths)
    out: Dict[str, WorkloadStats] = {}

    for exp in experiments:
        wd = workload_dirs.get(exp)
        fixed_dir = fixed_map.get(exp)
        if wd is None or fixed_dir is None or not fixed_dir.is_dir():
            continue

        metric_name, goal = detect_metric_goal_from_fixed(fixed_dir, exp)
        if metric_name is None:
            metric_name, goal = detect_metric_goal_from_any_tuner(wd, exp)
        if metric_name is None:
            continue

        by_tuner: Dict[str, Dict[str, Optional[float]]] = {}
        for label, dirname in methods:
            tuner_dir = resolve_method_tuner_dir(wd, exp, label, dirname, method_workload_overrides)
            if tuner_dir is None:
                by_tuner[label] = {"tuning": None, "stable": None}
                continue
            by_tuner[label] = {
                "tuning": collect_phase_mean(tuner_dir, metric_name, tuning_window),
                "stable": collect_phase_mean(tuner_dir, metric_name, stable_window),
            }

        default_tuning = collect_phase_mean(fixed_dir, metric_name, tuning_window)
        default_stable = collect_phase_mean(fixed_dir, metric_name, stable_window)
        if default_tuning is None or default_stable is None:
            continue
        if any(by_tuner[label]["tuning"] is None for label, _ in methods):
            continue
        if any(by_tuner[label]["stable"] is None for label, _ in methods):
            continue

        out[exp] = WorkloadStats(
            workload=exp,
            benchmark_label=benchmark_label(exp),
            obj_label="",
            goal=goal,
            default_tuning=default_tuning,
            default_stable=default_stable,
            by_tuner=by_tuner,
        )

    return out


def aggregate_run_means(values: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    if all(v > 0 for v in vals):
        return statistics.geometric_mean(vals)
    return statistics.fmean(vals)


def collect_phase_mean(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
) -> Optional[float]:
    per_run_means: List[float] = []
    spec = WindowSpec(*window)
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        vals = select_window_values(data, metric_name, spec)
        if not vals:
            continue
        per_run_means.append(statistics.fmean(vals))
    return aggregate_run_means(per_run_means)


def collect_phase_run_means(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
) -> List[float]:
    per_run_means: List[float] = []
    spec = WindowSpec(*window)
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        vals = select_window_values(data, metric_name, spec)
        if not vals:
            continue
        per_run_means.append(statistics.fmean(vals))
    return per_run_means


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
            improvements.append(pct)
    return improvements


def summarize_series(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    numeric = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not numeric:
        return None, None
    mean_value = statistics.fmean(numeric)
    error_value = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
    return mean_value, error_value


def collect_aggregate_improvement_series(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    experiments: Sequence[str],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, List[float]]]:
    workload_dirs, fixed_map = ensure_fixed_map(result_paths, fallback_fixed_paths)
    per_method_workload_series: Dict[str, Dict[str, List[List[float]]]] = {
        label: {"tuning": [], "stable": []} for label, _ in methods
    }

    for exp in experiments:
        wd = workload_dirs.get(exp)
        fixed_dir = fixed_map.get(exp)
        if wd is None or fixed_dir is None or not fixed_dir.is_dir():
            continue

        metric_name, goal = detect_metric_goal_from_fixed(fixed_dir, exp)
        if metric_name is None:
            metric_name, goal = detect_metric_goal_from_any_tuner(wd, exp)
        if metric_name is None:
            continue

        workload_series: Dict[str, Dict[str, List[float]]] = {}
        for label, dirname in methods:
            tuner_dir = resolve_method_tuner_dir(wd, exp, label, dirname, method_workload_overrides)
            if tuner_dir is None:
                workload_series = {}
                break

            tuning_series = paired_improvement_series(
                fixed_dir=fixed_dir,
                tuner_dir=tuner_dir,
                metric_name=metric_name,
                window=tuning_window,
                goal=goal,
            )
            stable_series = paired_improvement_series(
                fixed_dir=fixed_dir,
                tuner_dir=tuner_dir,
                metric_name=metric_name,
                window=stable_window,
                goal=goal,
            )
            if not tuning_series or not stable_series:
                workload_series = {}
                break

            workload_series[label] = {
                "tuning": tuning_series,
                "stable": stable_series,
            }

        if not workload_series:
            continue

        for label, _ in methods:
            per_method_workload_series[label]["tuning"].append(workload_series[label]["tuning"])
            per_method_workload_series[label]["stable"].append(workload_series[label]["stable"])

    aggregate_series: Dict[str, Dict[str, List[float]]] = {}
    for label, _ in methods:
        aggregate_series[label] = {}
        for phase in ("tuning", "stable"):
            workload_series = per_method_workload_series[label][phase]
            if not workload_series:
                aggregate_series[label][phase] = []
                continue

            paired_count = min(len(series) for series in workload_series)
            phase_series: List[float] = []
            for idx in range(paired_count):
                factors = [pct_to_factor(series[idx]) for series in workload_series]
                aggregate_factor = aggregate_factor_geomean(factors)
                aggregate_pct = factor_to_pct(aggregate_factor)
                if aggregate_pct is not None:
                    phase_series.append(aggregate_pct)
            aggregate_series[label][phase] = phase_series

    return aggregate_series


def fixed_reference_for_window(
    fixed_dir: Path,
    window: WindowSpec,
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    fixed_vals_all: List[float] = []
    metric_name: Optional[str] = None
    goal_name: Optional[str] = None
    for fp in iter_history_files(fixed_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        run_metric, run_goal = detect_metric_and_goal(data)
        if metric_name is None:
            metric_name = run_metric
        if goal_name is None:
            goal_name = canonical_goal(run_goal)
        vals = select_window_values(data, metric_name, window)
        fixed_vals_all.extend(vals)
    fixed_ref = statistics.fmean(fixed_vals_all) if fixed_vals_all else None
    return fixed_ref, metric_name, goal_name


def collect_run_robustness(
    tuner_dir: Path,
    window: WindowSpec,
    fixed_ref: Optional[float],
    forced_metric: Optional[str],
    forced_goal: Optional[str],
) -> List[Dict[str, Optional[float]]]:
    rows: List[Dict[str, Optional[float]]] = []
    if not tuner_dir.is_dir():
        return rows

    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        metric_name, goal_name = detect_metric_and_goal(data)
        metric_name = forced_metric or metric_name
        goal_name = canonical_goal(forced_goal or goal_name)
        vals = select_window_values(data, metric_name, window)
        if not vals:
            continue
        run_std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        variability = None
        if fixed_ref is not None and fixed_ref != 0:
            variability = (run_std / abs(fixed_ref)) * 100.0
        rows.append(
            {
                "pct_worse": pct_worse(goal_name, fixed_ref, vals),
                "variability": variability,
            }
        )
    return rows


def summarize_run_robustness(
    run_rows: Sequence[Dict[str, Optional[float]]],
) -> Dict[str, Optional[float]]:
    pct_bad = [float(r["pct_worse"]) for r in run_rows if r.get("pct_worse") is not None]
    variability = [float(r["variability"]) for r in run_rows if r.get("variability") is not None]
    return {
        "p50_bad_window_rate_pct": percentile(pct_bad, 0.50) if pct_bad else None,
        "p10_bad_window_rate_pct": percentile(pct_bad, 0.10) if pct_bad else None,
        "variability_pct_of_fixed": statistics.fmean(variability) if variability else None,
        "runs": float(len(run_rows)),
    }


def collect_window_run_values(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
) -> List[List[float]]:
    per_run_values: List[List[float]] = []
    spec = WindowSpec(*window)
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        vals = select_window_values(data, metric_name, spec)
        if vals:
            per_run_values.append(vals)
    return per_run_values


def paired_run_robustness_series(
    fixed_dir: Path,
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
    goal: str,
) -> Dict[str, List[float]]:
    fixed_run_refs = collect_phase_run_means(fixed_dir, metric_name, window)
    tuner_run_values = collect_window_run_values(tuner_dir, metric_name, window)
    paired_count = min(len(fixed_run_refs), len(tuner_run_values))
    if paired_count <= 0:
        return {"pct_worse": [], "variability": []}

    pct_worse_series: List[float] = []
    variability_series: List[float] = []
    for idx in range(paired_count):
        fixed_ref = fixed_run_refs[idx]
        vals = tuner_run_values[idx]
        pct = pct_worse(goal, fixed_ref, vals)
        if pct is not None:
            pct_worse_series.append(pct)
        if fixed_ref is not None and fixed_ref != 0:
            run_std = statistics.stdev(vals) if len(vals) > 1 else 0.0
            variability_series.append((run_std / abs(fixed_ref)) * 100.0)

    return {
        "pct_worse": pct_worse_series,
        "variability": variability_series,
    }


def collect_subset_variability(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    experiments: Sequence[str],
    tuning_window: Tuple[int, int],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, Optional[float]]]:
    workload_dirs, fixed_map = ensure_fixed_map(result_paths, fallback_fixed_paths)
    out: Dict[str, Dict[str, Optional[float]]] = {}
    window = WindowSpec(*tuning_window)

    for exp in experiments:
        wd = workload_dirs.get(exp)
        fixed_dir = fixed_map.get(exp)
        if wd is None or fixed_dir is None or not fixed_dir.is_dir():
            continue
        fixed_ref, metric_name, goal_name = fixed_reference_for_window(fixed_dir, window)
        if fixed_ref is None:
            continue

        row: Dict[str, Optional[float]] = {}
        for label, dirname in methods:
            tuner_dir = resolve_method_tuner_dir(wd, exp, label, dirname, method_workload_overrides)
            if tuner_dir is None:
                row[label] = None
                continue
            summary = summarize_run_robustness(
                collect_run_robustness(
                    tuner_dir=tuner_dir,
                    window=window,
                    fixed_ref=fixed_ref,
                    forced_metric=metric_name,
                    forced_goal=goal_name,
                )
            )
            row[label] = summary["variability_pct_of_fixed"]
        if row:
            out[exp] = row

    return out


def discover_all_memory_workloads(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    explicit: Sequence[str],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> List[str]:
    if explicit:
        return list(explicit)

    workload_dirs, fixed_map = ensure_fixed_map(result_paths, fallback_fixed_paths)
    out: List[str] = []
    for name, wd in sorted(workload_dirs.items()):
        if name not in fixed_map:
            continue
        if any(
            resolve_method_tuner_dir(wd, name, label, dirname, method_workload_overrides) is None
            for label, dirname in methods
        ):
            continue
        out.append(name)
    return out


def collect_aggregate_robustness(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    experiments: Sequence[str],
    tuning_window: Tuple[int, int],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, Optional[float]]]:
    workload_dirs, fixed_map = ensure_fixed_map(result_paths, fallback_fixed_paths)
    window = WindowSpec(*tuning_window)
    per_method_workload_series: Dict[str, Dict[str, List[List[float]]]] = {
        label: {"pct_worse": [], "variability": []} for label, _ in methods
    }
    used_workloads: List[str] = []

    for exp in experiments:
        wd = workload_dirs.get(exp)
        fixed_dir = fixed_map.get(exp)
        if wd is None or fixed_dir is None or not fixed_dir.is_dir():
            continue
        fixed_ref, metric_name, goal_name = fixed_reference_for_window(fixed_dir, window)
        if fixed_ref is None:
            continue

        local_series: Dict[str, Dict[str, List[float]]] = {}
        for label, dirname in methods:
            tuner_dir = resolve_method_tuner_dir(wd, exp, label, dirname, method_workload_overrides)
            if tuner_dir is None:
                local_series = {}
                break
            series = paired_run_robustness_series(
                fixed_dir=fixed_dir,
                tuner_dir=tuner_dir,
                metric_name=metric_name,
                window=tuning_window,
                goal=goal_name,
            )
            if not series["pct_worse"] or not series["variability"]:
                local_series = {}
                break
            local_series[label] = series

        if not local_series:
            continue

        used_workloads.append(exp)
        for label in local_series:
            per_method_workload_series[label]["pct_worse"].append(local_series[label]["pct_worse"])
            per_method_workload_series[label]["variability"].append(local_series[label]["variability"])

    aggregates: Dict[str, Dict[str, Optional[float]]] = {}
    for label, per_metric_series in per_method_workload_series.items():
        pct_worse_workload_series = per_metric_series["pct_worse"]
        variability_workload_series = per_metric_series["variability"]

        aggregate_pct_worse_series: List[float] = []
        if pct_worse_workload_series:
            paired_count = min(len(series) for series in pct_worse_workload_series)
            for idx in range(paired_count):
                aggregate_pct = aggregate_percent_geomean([series[idx] for series in pct_worse_workload_series])
                if aggregate_pct is not None:
                    aggregate_pct_worse_series.append(aggregate_pct)

        aggregate_variability_series: List[float] = []
        if variability_workload_series:
            paired_count = min(len(series) for series in variability_workload_series)
            for idx in range(paired_count):
                aggregate_var = aggregate_percent_geomean([series[idx] for series in variability_workload_series])
                if aggregate_var is not None:
                    aggregate_variability_series.append(aggregate_var)

        pct_worse_error = statistics.stdev(aggregate_pct_worse_series) if len(aggregate_pct_worse_series) > 1 else (0.0 if aggregate_pct_worse_series else None)
        variability_error = statistics.stdev(aggregate_variability_series) if len(aggregate_variability_series) > 1 else (0.0 if aggregate_variability_series else None)
        aggregates[label] = {
            "p50_bad_window_rate_pct": percentile(aggregate_pct_worse_series, 0.50) if aggregate_pct_worse_series else None,
            "p50_bad_window_rate_error_pct": pct_worse_error,
            "p10_bad_window_rate_pct": percentile(aggregate_pct_worse_series, 0.10) if aggregate_pct_worse_series else None,
            "p10_bad_window_rate_error_pct": pct_worse_error,
            "variability_pct_of_fixed": statistics.fmean(aggregate_variability_series) if aggregate_variability_series else None,
            "variability_pct_of_fixed_error_pct": variability_error,
            "n_workloads": float(len(pct_worse_workload_series)),
            "run_count": float(len(aggregate_pct_worse_series)),
        }
    aggregates["_workloads"] = {"names": ",".join(used_workloads)}
    return aggregates


def plot_main_figure(
    workload_stats: Dict[str, WorkloadStats],
    variability_by_workload: Dict[str, Dict[str, Optional[float]]],
    aggregates: Dict[str, Dict[str, Optional[float]]],
    aggregate_improvement_series: Dict[str, Dict[str, List[float]]],
    output_path: Path,
    methods: Sequence[Tuple[str, str]],
    method_colors: Dict[str, str],
    robustness_ymax: Optional[float] = None,
    robustness_yticks: Sequence[float] = (),
) -> Dict[str, Dict[str, Optional[float]]]:
    common_workloads = list(workload_stats.keys())

    aggregate_rows: Dict[str, Dict[str, Optional[float]]] = {}
    for label, _ in methods:
        tuning_mean, tuning_error = summarize_series(aggregate_improvement_series[label]["tuning"])
        stable_mean, stable_error = summarize_series(aggregate_improvement_series[label]["stable"])
        aggregate_rows[label] = {
            "tuning_pct": tuning_mean,
            "tuning_error_pct": tuning_error,
            "stable_pct": stable_mean,
            "stable_error_pct": stable_error,
        }

    fs = 8.2
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(3.35, 1.32),
        gridspec_kw={"width_ratios": [0.778, 1.214]},
    )
    fig.subplots_adjust(left=0.12, right=0.98, top=0.97, bottom=0.16, wspace=0.28)

    method_labels = [label for label, _ in methods]
    method_tick_labels = [method_display_label(label) for label in method_labels]
    x = np.arange(len(method_labels), dtype=float)
    width = 0.34
    tuning_vals = [aggregate_rows[label]["tuning_pct"] or 0.0 for label in method_labels]
    stable_vals = [aggregate_rows[label]["stable_pct"] or 0.0 for label in method_labels]
    tuning_errs = [aggregate_rows[label]["tuning_error_pct"] or 0.0 for label in method_labels]
    stable_errs = [aggregate_rows[label]["stable_error_pct"] or 0.0 for label in method_labels]
    for idx, label in enumerate(method_labels):
        color = method_colors[label]
        ax1.bar(
            x[idx] - width / 2,
            tuning_vals[idx],
            width=width,
            yerr=tuning_errs[idx],
            color=color,
            hatch="////",
            edgecolor="#444444",
            linewidth=0.7,
            capsize=2.2,
            error_kw={"elinewidth": 0.8, "ecolor": "#444444", "capthick": 0.8},
        )
        ax1.bar(
            x[idx] + width / 2,
            stable_vals[idx],
            width=width,
            yerr=stable_errs[idx],
            color=color,
            edgecolor="#444444",
            linewidth=0.7,
            capsize=2.2,
            error_kw={"elinewidth": 0.8, "ecolor": "#444444", "capthick": 0.8},
        )
    ax1.axhline(0.0, color="#555555", linewidth=0.8)
    ax1.set_xticks(x)
    ax1.set_xticklabels(method_tick_labels, fontsize=fs - 1.2)
    ax1.set_ylabel("Improvement (%)", fontsize=fs, labelpad=1)
    ax1.tick_params(axis="y", labelsize=fs - 1, pad=1)
    ax1.grid(axis="y", alpha=0.28)
    ax1.set_axisbelow(True)

    metric_keys = [m[0] for m in ROBUSTNESS_METRICS]
    metric_labels = [m[1] for m in ROBUSTNESS_METRICS]
    x2 = np.arange(len(metric_keys), dtype=float)
    legend_offsets = np.linspace(-width / 2, width / 2, num=len(methods)) if len(methods) > 1 else np.array([0.0])
    for idx, (label, _) in enumerate(methods):
        vals = [aggregates.get(label, {}).get(metric_key) or 0.0 for metric_key in metric_keys]
        errs = [
            aggregates.get(label, {}).get(ROBUSTNESS_ERROR_KEYS[metric_key]) or 0.0
            for metric_key in metric_keys
        ]
        offset = legend_offsets[idx]
        ax2.bar(
            x2 + offset,
            vals,
            width=width,
            yerr=errs,
            color=method_colors[label],
            hatch="////",
            edgecolor="#444444",
            linewidth=0.7,
            capsize=2.2,
            error_kw={"elinewidth": 0.8, "ecolor": "#444444", "capthick": 0.8},
        )
    ax2.set_xticks(x2)
    ax2.set_xticklabels(metric_labels, fontsize=fs - 1)
    ax2.set_ylabel("Aggregate (%)", fontsize=fs, labelpad=1)
    ax2.tick_params(axis="y", labelsize=fs - 1, pad=1)
    ax2.grid(axis="y", alpha=0.28)
    ax2.set_axisbelow(True)
    if robustness_ymax is not None:
        ax2.set_ylim(0.0, robustness_ymax)
    if robustness_yticks:
        ax2.set_yticks(list(robustness_yticks))

    legend_handles = [
        Patch(facecolor=method_colors[label], edgecolor="#444444", label=method_display_label(label))
        for label, _ in methods
    ]
    legend_handles.extend(
        [
            Patch(facecolor="white", edgecolor="#444444", hatch="////", label="Tuning"),
            Patch(facecolor="white", edgecolor="#444444", label="Stable"),
        ]
    )
    ax2.legend(
        handles=legend_handles,
        frameon=False,
        fontsize=fs - 1.0,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.03),
        ncol=2,
        handlelength=1.4,
        columnspacing=0.65,
        borderaxespad=0.1,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return aggregate_rows


def plot_aggregate_robustness(
    aggregates: Dict[str, Dict[str, Optional[float]]],
    output_path: Path,
    methods: Sequence[Tuple[str, str]],
    method_colors: Dict[str, str],
    ymax: Optional[float] = None,
    yticks: Sequence[float] = (),
) -> None:
    fs = 14
    metric_keys = [m[0] for m in ROBUSTNESS_METRICS]
    metric_labels = [m[1] for m in ROBUSTNESS_METRICS]
    x = np.arange(len(metric_keys), dtype=float)
    width = 0.34

    fig, ax = plt.subplots(1, 1, figsize=(4.9, 1.8), constrained_layout=True)
    offsets = np.linspace(-width / 2, width / 2, num=len(methods)) if len(methods) > 1 else np.array([0.0])
    for idx, (label, _) in enumerate(methods):
        vals = [aggregates.get(label, {}).get(metric_key) or 0.0 for metric_key in metric_keys]
        errs = [
            aggregates.get(label, {}).get(ROBUSTNESS_ERROR_KEYS[metric_key]) or 0.0
            for metric_key in metric_keys
        ]
        offset = offsets[idx]
        ax.bar(
            x + offset,
            vals,
            width=width,
            yerr=errs,
            color=method_colors[label],
            label=method_display_label(label),
            capsize=2.8,
            error_kw={"elinewidth": 0.9, "ecolor": "#444444", "capthick": 0.9},
        )

    ax.set_xticks(x)
    ax.set_xticklabels(metric_labels, fontsize=fs - 2)
    ax.set_ylabel("Aggregate (%)", fontsize=fs - 1)
    ax.tick_params(axis="y", labelsize=fs - 3)
    ax.grid(axis="y", alpha=0.28)
    ax.set_axisbelow(True)
    if ymax is not None:
        ax.set_ylim(0.0, ymax)
    if yticks:
        ax.set_yticks(list(yticks))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def write_main_csv(
    output_path: Path,
    workload_stats: Dict[str, WorkloadStats],
    variability_by_workload: Dict[str, Dict[str, Optional[float]]],
    aggregate_rows: Dict[str, Dict[str, Optional[float]]],
    methods: Sequence[Tuple[str, str]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "row_type",
                "workload",
                "benchmark_label",
                "goal",
                "method",
                "tuning_pct",
                "tuning_error_pct",
                "stable_pct",
                "stable_error_pct",
                "tuning_variability_pct_of_fixed",
            ]
        )
        for label, _ in methods:
            writer.writerow(
                [
                    "aggregate",
                    "",
                    "",
                    "",
                    label,
                    "" if aggregate_rows[label]["tuning_pct"] is None else f"{aggregate_rows[label]['tuning_pct']:.6f}",
                    "" if aggregate_rows[label]["tuning_error_pct"] is None else f"{aggregate_rows[label]['tuning_error_pct']:.6f}",
                    "" if aggregate_rows[label]["stable_pct"] is None else f"{aggregate_rows[label]['stable_pct']:.6f}",
                    "" if aggregate_rows[label]["stable_error_pct"] is None else f"{aggregate_rows[label]['stable_error_pct']:.6f}",
                    "",
                ]
            )
        for workload, stats in sorted(workload_stats.items()):
            for label, _ in methods:
                writer.writerow(
                    [
                        "workload",
                        workload,
                        benchmark_label(workload),
                        stats.goal,
                        label,
                        "" if stats.by_tuner[label]["tuning"] is None else f"{improvement_pct(stats.default_tuning, stats.by_tuner[label]['tuning'], stats.goal):.6f}",
                        "",
                        "" if stats.by_tuner[label]["stable"] is None else f"{improvement_pct(stats.default_stable, stats.by_tuner[label]['stable'], stats.goal):.6f}",
                        "",
                        "" if variability_by_workload.get(workload, {}).get(label) is None else f"{variability_by_workload[workload][label]:.6f}",
                    ]
                )


def write_aggregate_csv(
    output_path: Path,
    aggregates: Dict[str, Dict[str, Optional[float]]],
    methods: Sequence[Tuple[str, str]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "method",
                "n_workloads",
                "run_count",
                "p50_bad_window_rate_pct",
                "p50_bad_window_rate_error_pct",
                "p10_bad_window_rate_pct",
                "p10_bad_window_rate_error_pct",
                "variability_pct_of_fixed",
                "variability_pct_of_fixed_error_pct",
                "workloads",
            ]
        )
        workloads = aggregates.get("_workloads", {}).get("names", "")
        for label, _ in methods:
            row = aggregates.get(label, {})
            writer.writerow(
                [
                    label,
                    "" if row.get("n_workloads") is None else f"{row['n_workloads']:.0f}",
                    "" if row.get("run_count") is None else f"{row['run_count']:.0f}",
                    "" if row.get("p50_bad_window_rate_pct") is None else f"{row['p50_bad_window_rate_pct']:.6f}",
                    "" if row.get("p50_bad_window_rate_error_pct") is None else f"{row['p50_bad_window_rate_error_pct']:.6f}",
                    "" if row.get("p10_bad_window_rate_pct") is None else f"{row['p10_bad_window_rate_pct']:.6f}",
                    "" if row.get("p10_bad_window_rate_error_pct") is None else f"{row['p10_bad_window_rate_error_pct']:.6f}",
                    "" if row.get("variability_pct_of_fixed") is None else f"{row['variability_pct_of_fixed']:.6f}",
                    "" if row.get("variability_pct_of_fixed_error_pct") is None else f"{row['variability_pct_of_fixed_error_pct']:.6f}",
                    workloads,
                ]
            )


def main() -> None:
    args = parse_args()
    tuning_window = parse_window(args.tuning_window)
    stable_window = parse_window(args.stable_window)
    methods, method_colors, method_workload_overrides = build_methods(args)
    result_paths = merge_unique_paths(args.result_paths, args.vanilla_result_paths, args.memory_result_paths)
    if not result_paths:
        raise SystemExit("Provide --result-paths or the legacy --vanilla-result-paths/--memory-result-paths.")

    workload_stats = collect_workload_stats_map(
        result_paths=result_paths,
        fallback_fixed_paths=args.fallback_fixed_paths,
        experiments=args.experiments,
        tuning_window=tuning_window,
        stable_window=stable_window,
        methods=methods,
        method_workload_overrides=method_workload_overrides,
    )
    if not workload_stats:
        raise SystemExit("No common method-pair workload stats found for the requested subset.")

    variability_by_workload = collect_subset_variability(
        result_paths=result_paths,
        fallback_fixed_paths=args.fallback_fixed_paths,
        experiments=list(workload_stats.keys()),
        tuning_window=tuning_window,
        methods=methods,
        method_workload_overrides=method_workload_overrides,
    )

    all_experiments = discover_all_memory_workloads(
        result_paths=result_paths,
        fallback_fixed_paths=args.fallback_fixed_paths,
        explicit=args.all_experiments,
        methods=methods,
        method_workload_overrides=method_workload_overrides,
    )
    aggregates = collect_aggregate_robustness(
        result_paths=result_paths,
        fallback_fixed_paths=args.fallback_fixed_paths,
        experiments=all_experiments,
        tuning_window=tuning_window,
        methods=methods,
        method_workload_overrides=method_workload_overrides,
    )
    aggregate_improvement_series = collect_aggregate_improvement_series(
        result_paths=result_paths,
        fallback_fixed_paths=args.fallback_fixed_paths,
        experiments=list(workload_stats.keys()),
        tuning_window=tuning_window,
        stable_window=stable_window,
        methods=methods,
        method_workload_overrides=method_workload_overrides,
    )

    aggregate_rows = plot_main_figure(
        workload_stats=workload_stats,
        variability_by_workload=variability_by_workload,
        aggregates=aggregates,
        aggregate_improvement_series=aggregate_improvement_series,
        output_path=Path(args.plot_output),
        methods=methods,
        method_colors=method_colors,
        robustness_ymax=args.main_robustness_ymax,
        robustness_yticks=args.main_robustness_yticks,
    )
    print(f"Wrote plot: {Path(args.plot_output).resolve()}")
    print("Selected subset:", ", ".join(benchmark_label(w) for w in sorted(workload_stats.keys())))
    for label, row in aggregate_rows.items():
        tuning_text = (
            "N/A"
            if row["tuning_pct"] is None
            else f"{row['tuning_pct']:+.2f}% +/- {float(row['tuning_error_pct'] or 0.0):.2f}"
        )
        stable_text = (
            "N/A"
            if row["stable_pct"] is None
            else f"{row['stable_pct']:+.2f}% +/- {float(row['stable_error_pct'] or 0.0):.2f}"
        )
        print(f"  {label}: tuning={tuning_text}, stable={stable_text}")

    if args.csv_output:
        write_main_csv(
            output_path=Path(args.csv_output),
            workload_stats=workload_stats,
            variability_by_workload=variability_by_workload,
            aggregate_rows=aggregate_rows,
            methods=methods,
        )
        print(f"Wrote CSV: {Path(args.csv_output).resolve()}")

    if args.aggregate_robustness_plot_output or args.aggregate_robustness_csv_output:
        if args.aggregate_robustness_plot_output:
            plot_aggregate_robustness(
                aggregates=aggregates,
                output_path=Path(args.aggregate_robustness_plot_output),
                methods=methods,
                method_colors=method_colors,
                ymax=args.aggregate_robustness_ymax,
                yticks=args.aggregate_robustness_yticks,
            )
            print(
                "Wrote aggregate robustness plot:",
                Path(args.aggregate_robustness_plot_output).resolve(),
            )
        if args.aggregate_robustness_csv_output:
            write_aggregate_csv(
                output_path=Path(args.aggregate_robustness_csv_output),
                aggregates=aggregates,
                methods=methods,
            )
            print(
                "Wrote aggregate robustness CSV:",
                Path(args.aggregate_robustness_csv_output).resolve(),
            )
        print("Aggregate robustness workloads:", aggregates.get("_workloads", {}).get("names", ""))
        for label, _ in methods:
            row = aggregates.get(label, {})
            p50 = (
                "N/A"
                if row.get("p50_bad_window_rate_pct") is None
                else f"{row['p50_bad_window_rate_pct']:.2f}% +/- {float(row.get('p50_bad_window_rate_error_pct') or 0.0):.2f}"
            )
            p10 = (
                "N/A"
                if row.get("p10_bad_window_rate_pct") is None
                else f"{row['p10_bad_window_rate_pct']:.2f}% +/- {float(row.get('p10_bad_window_rate_error_pct') or 0.0):.2f}"
            )
            var = (
                "N/A"
                if row.get("variability_pct_of_fixed") is None
                else f"{row['variability_pct_of_fixed']:.2f}% +/- {float(row.get('variability_pct_of_fixed_error_pct') or 0.0):.2f}"
            )
            print(f"  {label}: P50={p50}, P10={p10}, Var={var}")


if __name__ == "__main__":
    main()
