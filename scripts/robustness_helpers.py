#!/usr/bin/env python3
"""Shared archived-result aggregation helpers for robustness plots."""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from generate_full_performance_table import (
    history_run_completed_successfully,
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


def _aggregate_percent_geomean(values: Sequence[Optional[float]]) -> Optional[float]:
    factors = [
        1.0 + (float(value) / 100.0)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    if not factors or any(factor <= 0 for factor in factors):
        return None
    return (math.exp(statistics.fmean(math.log(factor) for factor in factors)) - 1.0) * 100.0


def _resolve_roots(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
) -> tuple[Dict[str, Path], Dict[str, Path]]:
    workload_dirs = {directory.name: directory for directory in resolve_workload_dirs(result_paths)}
    fixed_map = resolve_fixed_dirs(fallback_fixed_paths)
    for name, workload_dir in workload_dirs.items():
        fixed_dir = workload_dir / "fixed"
        if fixed_dir.is_dir():
            fixed_map.setdefault(name, fixed_dir)
    return workload_dirs, fixed_map


def _resolve_method_dir(
    workload_dir: Path,
    workload: str,
    label: str,
    default_dirspec: str,
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Optional[Path]:
    dirspec = method_workload_overrides.get(label, {}).get(workload, default_dirspec)
    return resolve_tuner_dir(workload_dir, dirspec)


def _phase_run_means(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
) -> List[float]:
    means: List[float] = []
    spec = WindowSpec(*window)
    for history_file in iter_history_files(tuner_dir):
        data = load_json(history_file)
        if data is None or not history_run_completed_successfully(data):
            continue
        values = select_window_values(data, metric_name, spec)
        if values:
            means.append(statistics.fmean(values))
    return means


def _fixed_reference(
    fixed_dir: Path,
    window: WindowSpec,
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    values: List[float] = []
    metric_name: Optional[str] = None
    goal_name: Optional[str] = None
    for history_file in iter_history_files(fixed_dir):
        data = load_json(history_file)
        if data is None or not history_run_completed_successfully(data):
            continue
        run_metric, run_goal = detect_metric_and_goal(data)
        metric_name = metric_name or run_metric
        goal_name = goal_name or canonical_goal(run_goal)
        values.extend(select_window_values(data, metric_name, window))
    return (statistics.fmean(values) if values else None), metric_name, goal_name


def _window_run_values(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
) -> List[List[float]]:
    runs: List[List[float]] = []
    spec = WindowSpec(*window)
    for history_file in iter_history_files(tuner_dir):
        data = load_json(history_file)
        if data is None or not history_run_completed_successfully(data):
            continue
        values = select_window_values(data, metric_name, spec)
        if values:
            runs.append(values)
    return runs


def _paired_robustness_series(
    fixed_dir: Path,
    tuner_dir: Path,
    metric_name: Optional[str],
    window: Tuple[int, int],
    goal: str,
) -> Dict[str, List[float]]:
    fixed_references = _phase_run_means(fixed_dir, metric_name, window)
    tuner_runs = _window_run_values(tuner_dir, metric_name, window)
    paired_count = min(len(fixed_references), len(tuner_runs))
    poor_rates: List[float] = []
    variability: List[float] = []
    for index in range(paired_count):
        fixed_reference = fixed_references[index]
        values = tuner_runs[index]
        poor_rate = pct_worse(goal, fixed_reference, values)
        if poor_rate is not None:
            poor_rates.append(poor_rate)
        if fixed_reference != 0:
            run_std = statistics.stdev(values) if len(values) > 1 else 0.0
            variability.append((run_std / abs(fixed_reference)) * 100.0)
    return {"pct_worse": poor_rates, "variability": variability}


def collect_aggregate_robustness(
    result_paths: Sequence[str],
    fallback_fixed_paths: Sequence[str],
    experiments: Sequence[str],
    tuning_window: Tuple[int, int],
    methods: Sequence[Tuple[str, str]],
    method_workload_overrides: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, Optional[float]]]:
    workload_dirs, fixed_map = _resolve_roots(result_paths, fallback_fixed_paths)
    window = WindowSpec(*tuning_window)
    by_method: Dict[str, Dict[str, List[List[float]]]] = {
        label: {"pct_worse": [], "variability": []} for label, _ in methods
    }
    used_workloads: List[str] = []

    for experiment in experiments:
        workload_dir = workload_dirs.get(experiment)
        fixed_dir = fixed_map.get(experiment)
        if workload_dir is None or fixed_dir is None or not fixed_dir.is_dir():
            continue
        fixed_reference, metric_name, goal_name = _fixed_reference(fixed_dir, window)
        if fixed_reference is None or goal_name is None:
            continue

        local: Dict[str, Dict[str, List[float]]] = {}
        for label, dirname in methods:
            tuner_dir = _resolve_method_dir(
                workload_dir,
                experiment,
                label,
                dirname,
                method_workload_overrides,
            )
            if tuner_dir is None:
                local = {}
                break
            series = _paired_robustness_series(
                fixed_dir,
                tuner_dir,
                metric_name,
                tuning_window,
                goal_name,
            )
            if not series["pct_worse"] or not series["variability"]:
                local = {}
                break
            local[label] = series

        if not local:
            continue
        used_workloads.append(experiment)
        for label, series in local.items():
            by_method[label]["pct_worse"].append(series["pct_worse"])
            by_method[label]["variability"].append(series["variability"])

    aggregates: Dict[str, Dict[str, Optional[float]]] = {}
    for label, metric_series in by_method.items():
        poor_workloads = metric_series["pct_worse"]
        variability_workloads = metric_series["variability"]

        aggregate_poor: List[float] = []
        if poor_workloads:
            for index in range(min(len(series) for series in poor_workloads)):
                value = _aggregate_percent_geomean([series[index] for series in poor_workloads])
                if value is not None:
                    aggregate_poor.append(value)

        aggregate_variability: List[float] = []
        if variability_workloads:
            for index in range(min(len(series) for series in variability_workloads)):
                value = _aggregate_percent_geomean(
                    [series[index] for series in variability_workloads]
                )
                if value is not None:
                    aggregate_variability.append(value)

        poor_error = (
            statistics.stdev(aggregate_poor)
            if len(aggregate_poor) > 1
            else (0.0 if aggregate_poor else None)
        )
        variability_error = (
            statistics.stdev(aggregate_variability)
            if len(aggregate_variability) > 1
            else (0.0 if aggregate_variability else None)
        )
        aggregates[label] = {
            "p50_bad_window_rate_pct": percentile(aggregate_poor, 0.50) if aggregate_poor else None,
            "p50_bad_window_rate_error_pct": poor_error,
            "p10_bad_window_rate_pct": percentile(aggregate_poor, 0.10) if aggregate_poor else None,
            "p10_bad_window_rate_error_pct": poor_error,
            "variability_pct_of_fixed": (
                statistics.fmean(aggregate_variability) if aggregate_variability else None
            ),
            "variability_pct_of_fixed_error_pct": variability_error,
            "n_workloads": float(len(poor_workloads)),
            "run_count": float(len(aggregate_poor)),
        }
    aggregates["_workloads"] = {"names": ",".join(used_workloads)}
    return aggregates
