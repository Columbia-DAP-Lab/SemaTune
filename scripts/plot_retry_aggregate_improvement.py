#!/usr/bin/env python3
"""Plot aggregate retry-phase improvement over Fixed for selected tuners."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.transforms import Bbox, ScaledTranslation

from generate_full_performance_table import (
    benchmark_label,
    detect_metric_goal_from_any_tuner,
    detect_metric_goal_from_fixed,
    history_run_completed_successfully,
    improvement_pct,
    iter_history_files,
    load_json,
    objective_label,
    phase_mean_for_run,
    parse_custom_columns,
    resolve_fixed_dirs,
    resolve_tuner_dir,
    resolve_workload_dirs,
)


DEFAULT_COLUMNS = (
    "Tuxbot App Metrics Dual Loop:llm_dual_app_metrics_final_actor,"
    "MLOS + Tuxbot:mlos_trimming_aggressive|mlos_trimming,"
    "MLOS:mlos_50_tuning_only|mlos"
)

DISPLAY_LABEL_MAP = {
    "Tuxbot App Metrics Dual Loop": "TuxBot",
    "MLOS": "MLOS",
    "MLOS + Tuxbot": "TuxBot-trim",
    "Bayesian": "Bayes",
    "DQN": "DQN",
    "Q-Learning": "Q-Learning",
    "Tuxbot App Only Dual": "TuxBot App",
    "Tuxbot Indirect All Dual": "TuxBot Indirect",
    "Tuxbot Indirect Dump Dual": "TuxBot Indirect System",
    "Tuxbot IPC Dual": "TuxBot IPC",
    "MLOS App Metrics": "MLOS",
    "MLOS IPC": "MLOS IPC",
    "MLOS Cache Misses": "MLOS Cache",
    "TuxBot Trim App": "TuxBot-trim",
    "TuxBot Trim IPC": "TuxBot-trim IPC",
    "TuxBot Trim Cache": "TuxBot-trim Cache",
}

TODO_PLACEHOLDER_METHODS = {"TuxBot Trim IPC", "TuxBot Trim Cache"}
RIGHT_TRIM_PTS = 3.0
BENCHMARK_SAME_THRESHOLD_PCT = 5.0
LATEX_POSITIVE_COLOR = "green!60!black"
LATEX_NEGATIVE_COLOR = "red!70!black"
LATEX_NEUTRAL_COLOR = "yellow!50!black"

METHOD_COLORS = {
    "Tuxbot App Metrics Dual Loop": "#0072B2",
    "MLOS + Tuxbot": "#009E73",
    "MLOS": "#E69F00",
    "Bayesian": "#CC79A7",
    "DQN": "#D55E00",
    "Q-Learning": "#F0E442",
    "Tuxbot App Only Dual": "#0072B2",
    "Tuxbot Indirect All Dual": "#0072B2",
    "Tuxbot Indirect Dump Dual": "#0072B2",
    "Tuxbot IPC Dual": "#0072B2",
    "MLOS App Metrics": "#E69F00",
    "MLOS IPC": "#E69F00",
    "MLOS Cache Misses": "#E69F00",
    "TuxBot Trim App": "#009E73",
    "TuxBot Trim IPC": "#009E73",
    "TuxBot Trim Cache": "#009E73",
}

NO_CATASTROPHIC_EXTRA_EXCLUDED: set[str] = set()

LATEX_METHOD_LABEL_MAP = {
    "TuxBot": "TuxBot",
    "TuxBot-trim": "TuxBot-Trm",
    "MLOS": "MLOS",
    "Bayes": "Bayesian",
    "DQN": "DQN",
    "Q-Learning": "Q-Learning",
}

LATEX_BENCHMARK_LABEL_OVERRIDES = {
    "dcperf_spark_tput": "Spark",
    "mutilate_high": "Mutilate",
    "tpcc_hi_p99": "TPC-C",
}


def _variant_suffix(label: str) -> str:
    for suffix in (" no Catastrophic", " no Xapian"):
        if label.endswith(suffix):
            return suffix
    return ""


def _base_method_label(label: str) -> str:
    suffix = _variant_suffix(label)
    return label[:-len(suffix)] if suffix else label


def _excluded_workloads_for_label(label: str) -> set[str]:
    suffix = _variant_suffix(label)
    if suffix == " no Xapian":
        return {"xapian_hi_p99"}
    if suffix == " no Catastrophic":
        return {"xapian_hi_p99", "mutilate_high"} | NO_CATASTROPHIC_EXTRA_EXCLUDED
    return set()


def _display_label(label: str) -> str:
    base = _base_method_label(label)
    pretty = DISPLAY_LABEL_MAP.get(base, base)
    suffix = _variant_suffix(label)
    if suffix == " no Xapian":
        return f"{pretty}\n(no Xapian)"
    if suffix == " no Catastrophic":
        return f"{pretty}\n(no Catastrophic)"
    return pretty


def _summary_label(label: str) -> str:
    base = _base_method_label(label)
    pretty = DISPLAY_LABEL_MAP.get(base, base)
    suffix = _variant_suffix(label)
    if suffix == " no Xapian":
        return f"{pretty} no Xapian"
    if suffix == " no Catastrophic":
        return f"{pretty} no Catastrophic"
    return pretty


def _compute_25pct_axis_bounds(row_map: Dict[Tuple[str, str], Dict[str, object]], labels: Sequence[str]) -> Tuple[float, float]:
    values: List[float] = [0.0]
    for label in labels:
        for phase in ("tuning", "stable"):
            row = row_map.get((label, phase))
            if row is None or row.get("aggregate_pct") is None:
                continue
            pct = float(row["aggregate_pct"])
            err = 0.0 if row.get("aggregate_pct_err_low") is None else float(row["aggregate_pct_err_low"])
            values.extend([pct - err, pct + err])
    lo = min(values)
    hi = max(values)
    tick_lo = 25.0 * math.floor((lo - 5.0) / 25.0)
    tick_hi = 25.0 * math.ceil((hi + 5.0) / 25.0)
    if tick_hi <= tick_lo:
        tick_hi = tick_lo + 25.0
    return tick_lo, tick_hi

COLOR_CYCLE = [
    "#1f77b4",
    "#e67e22",
    "#2ca58d",
    "#9467bd",
    "#c0392b",
    "#7f8c8d",
    "#8c564b",
    "#17becf",
    "#bcbd22",
]

DEFAULT_CURATED_WORKLOADS = (
    "masstree_hi_p99",
    "sibench_hi_p99",
    "silo_hi_p99",
    "sphinx_tput_max",
    "sysbench_cpu_tput",
    "sysbench_oltp_rw_hi_p99",
    "tpcc_hi_p99",
    "twitter_p99",
    "wikipedia_p99",
    "xapian_hi_p99",
    "ycsb_hi_p99",
)

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Plot aggregate improvement over Fixed for retry experiments."
    )
    p.add_argument(
        "--result-paths",
        nargs="+",
        required=True,
        help="Retry results roots or workload dirs.",
    )
    p.add_argument(
        "--fallback-fixed-paths",
        nargs="*",
        default=[],
        help="Fixed baseline roots, typically *_new.",
    )
    p.add_argument(
        "--fallback-tuner-paths",
        nargs="*",
        default=[],
        help="Additional result roots to search for tuner dirs missing from --result-paths (e.g. *_new for bayesian/dqn/qlearning).",
    )
    p.add_argument(
        "--custom-columns",
        default=DEFAULT_COLUMNS,
        help="Comma-separated LABEL:DIRNAME tuner columns to aggregate.",
    )
    p.add_argument(
        "--workloads",
        default=",".join(DEFAULT_CURATED_WORKLOADS),
        help=(
            "Optional comma-separated workload names to include, in output order. "
            "Defaults to the curated 11-workload evaluation set."
        ),
    )
    p.add_argument(
        "--aggregate-exclude-workloads",
        default="",
        help=(
            "Optional comma-separated workload names to exclude only from the "
            "aggregate geomean/plot/CSV summary. These workloads still remain "
            "available to per-benchmark tables."
        ),
    )
    p.add_argument(
        "--tuning-window",
        default="1-30",
        help="Inclusive tuning window START-END.",
    )
    p.add_argument(
        "--stable-window",
        default="31-50",
        help="Inclusive stable window START-END.",
    )
    p.add_argument(
        "--aggregate-stat",
        default="geomean",
        choices=("geomedian", "geomean", "trimmed-geomean"),
        help="Cross-workload aggregate over multiplicative improvement factors.",
    )
    p.add_argument(
        "--subset",
        default="common",
        choices=("common", "union"),
        help="Use workloads common to all methods per phase, or each method's union of available workloads.",
    )
    p.add_argument(
        "--trim-fraction",
        type=float,
        default=0.10,
        help="Trim fraction for trimmed-geomean (default: 0.10).",
    )
    p.add_argument(
        "--plot-output",
        required=True,
        help="Output figure path (.pdf or .png).",
    )
    p.add_argument(
        "--csv-output",
        default="",
        help="Optional CSV summary output path.",
    )
    p.add_argument(
        "--latex-table-output",
        default="",
        help="Optional LaTeX per-benchmark table output path (.tex).",
    )
    p.add_argument(
        "--latex-table-caption",
        default="",
        help="Optional caption override for --latex-table-output.",
    )
    p.add_argument(
        "--latex-table-label",
        default="",
        help="Optional label override for --latex-table-output.",
    )
    p.add_argument(
        "--error-bars",
        action="store_true",
        help="Add +/-1σ error bars across aggregate reruns.",
    )
    p.add_argument(
        "--ymin",
        type=float,
        default=-60.0,
        help="Optional lower y-axis bound (default: -60).",
    )
    p.add_argument(
        "--ymax",
        type=float,
        default=110.0,
        help="Optional upper y-axis bound (default: 110).",
    )
    p.add_argument(
        "--auto-y",
        action="store_true",
        help="Auto-scale the y-axis from the plotted data for this run only.",
    )
    p.add_argument(
        "--auto-y-min",
        type=float,
        default=None,
        help="Optional lower clamp to apply when --auto-y is enabled.",
    )
    p.add_argument(
        "--auto-y-max",
        type=float,
        default=None,
        help="Optional upper clamp to apply when --auto-y is enabled.",
    )
    p.add_argument(
        "--base-font-size",
        type=int,
        default=16,
        help="Base font size for axes, legend, and tick labels (default: 16).",
    )
    p.add_argument(
        "--x-label-map",
        default="",
        help="Optional comma-separated METHOD:SHORTLABEL map for x tick labels.",
    )
    p.add_argument(
        "--x-group-map",
        default="",
        help="Optional comma-separated METHOD:GROUPLABEL map for a second x-axis label row.",
    )
    p.add_argument(
        "--x-shift-map",
        default="",
        help="Optional comma-separated METHOD:PTSHIFT map for first-row x label offsets in points.",
    )
    p.add_argument(
        "--x-group-shift-map",
        default="",
        help="Optional comma-separated GROUPLABEL:PTSHIFT map for second-row group label offsets in points.",
    )
    p.add_argument(
        "--x-group-y-shift-pts",
        type=float,
        default=0.0,
        help="Optional extra vertical shift for second-row group labels in points; positive moves them farther below the x tick labels.",
    )
    p.add_argument(
        "--x-font-delta-map",
        default="",
        help="Optional comma-separated METHOD:PTDELTA map for first-row x label font-size adjustments in points.",
    )
    p.add_argument(
        "--same-threshold-pct",
        type=float,
        default=BENCHMARK_SAME_THRESHOLD_PCT,
        help=(
            "Benchmarks with mean improvement within +/- this percentage are counted "
            "as 'same' in the I/S/D summary columns."
        ),
    )
    p.add_argument(
        "--right-trim-pts",
        type=float,
        default=RIGHT_TRIM_PTS,
        help="Trim this many points from the right edge of the saved tight bbox (default: 3).",
    )
    p.add_argument(
        "--no-catastrophic-extra-exclude",
        default="",
        help=(
            "Optional comma-separated workload names to exclude only from "
            "labels ending in ' no Catastrophic'."
        ),
    )
    return p.parse_args()


def parse_window(spec: str) -> Tuple[int, int]:
    lo, hi = [int(x.strip()) for x in spec.split("-", 1)]
    return lo, hi


def parse_label_map(spec: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not spec:
        return out
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise SystemExit(f"Invalid label-map item (missing ':'): {item}")
        key, value = item.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def parse_float_map(spec: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not spec:
        return out
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise SystemExit(f"Invalid float-map item (missing ':'): {item}")
        key, value = item.split(":", 1)
        try:
            out[key.strip()] = float(value.strip())
        except ValueError as exc:
            raise SystemExit(f"Invalid float value in map item: {item}") from exc
    return out


def improvement_factor(default: Optional[float], candidate: Optional[float], goal: str) -> Optional[float]:
    if default is None or candidate is None:
        return None
    if default <= 0 or candidate <= 0:
        return None
    if goal == "maximize":
        return candidate / default
    return default / candidate


def aggregate_factors(factors: Sequence[float], method: str, trim_fraction: float) -> Optional[float]:
    vals = [float(v) for v in factors if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not vals:
        return None
    logs = np.log(np.array(vals, dtype=float))
    if method == "geomean":
        agg_log = float(np.mean(logs))
    elif method == "geomedian":
        agg_log = float(np.median(logs))
    elif method == "trimmed-geomean":
        if not 0.0 <= trim_fraction < 0.5:
            raise SystemExit("--trim-fraction must be in [0, 0.5).")
        logs_sorted = np.sort(logs)
        trim = int(len(logs_sorted) * trim_fraction)
        if trim > 0 and len(logs_sorted) > 2 * trim:
            logs_sorted = logs_sorted[trim:-trim]
        agg_log = float(np.mean(logs_sorted))
    else:
        raise ValueError(f"Unsupported aggregate method: {method}")
    return math.exp(agg_log)


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


def _build_fallback_tuner_map(paths: Sequence[str]) -> Dict[str, Path]:
    """Map workload name -> fallback workload dir for tuner look-ups."""
    out: Dict[str, Path] = {}
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        if p.is_dir():
            for d in sorted(x for x in p.iterdir() if x.is_dir()):
                out.setdefault(d.name, d)
    return out


def resolve_fixed_dir_for_workload(
    workload_dir: Path,
    fallback_fixed_map: Dict[str, Path],
) -> Optional[Path]:
    local_fixed = workload_dir / "fixed"
    if local_fixed.is_dir():
        return local_fixed
    return fallback_fixed_map.get(workload_dir.name)


def resolve_tuner_dir_for_workload(
    workload_dir: Path,
    fallback_workload_dir: Optional[Path],
    dir_spec: str,
) -> Optional[Path]:
    resolved = resolve_tuner_dir(workload_dir, dir_spec)
    if resolved is not None:
        return resolved
    if fallback_workload_dir is not None:
        return resolve_tuner_dir(fallback_workload_dir, dir_spec)
    return None


def detect_metric_goal_for_workload(
    workload_dir: Path,
    fixed_dir: Path,
    fallback_workload_dir: Optional[Path],
) -> Tuple[Optional[str], Optional[str]]:
    metric_name, goal = detect_metric_goal_from_fixed(fixed_dir, workload_dir.name)
    if metric_name is not None:
        return metric_name, goal
    metric_name, goal = detect_metric_goal_from_any_tuner(workload_dir, workload_dir.name)
    if metric_name is not None:
        return metric_name, goal
    if fallback_workload_dir is not None:
        metric_name, goal = detect_metric_goal_from_any_tuner(fallback_workload_dir, workload_dir.name)
    return metric_name, goal


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
            values.append(value)
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
        factor = pct_to_factor(pct)
        if pct is not None and factor is not None:
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
        aggregate_pct = factor_to_pct(aggregate_factor)
        if aggregate_pct is not None:
            aggregate_series.append(aggregate_pct)
    return aggregate_series


def summarize_series(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    numeric = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not numeric:
        return None, None
    mean_value = statistics.fmean(numeric)
    error_value = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
    return mean_value, error_value


def summarize_benchmark_outcomes(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    workloads: Sequence[str],
    label: str,
    phase: str,
    same_threshold_pct: float = BENCHMARK_SAME_THRESHOLD_PCT,
) -> Tuple[int, int, int]:
    improve = 0
    same = 0
    deteriorate = 0
    for workload in workloads:
        series = workload_phase_series.get(workload, {}).get(label, {}).get(phase, [])
        pct, _err = summarize_series(series)
        if pct is None:
            continue
        if pct > same_threshold_pct:
            improve += 1
        elif pct < -same_threshold_pct:
            deteriorate += 1
        else:
            same += 1
    return improve, same, deteriorate


def collect_workload_phase_series(
    workload_dirs: Sequence[Path],
    fallback_fixed_map: Dict[str, Path],
    fallback_tuner_map: Dict[str, Path],
    columns: Sequence[Tuple[str, str]],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    series_map: Dict[str, Dict[str, Dict[str, List[float]]]] = {}

    for workload_dir in workload_dirs:
        fixed_dir = resolve_fixed_dir_for_workload(workload_dir, fallback_fixed_map)
        if fixed_dir is None or not fixed_dir.is_dir():
            continue
        fallback_workload_dir = fallback_tuner_map.get(workload_dir.name)
        metric_name, goal = detect_metric_goal_for_workload(workload_dir, fixed_dir, fallback_workload_dir)
        if metric_name is None or goal is None:
            continue

        by_method: Dict[str, Dict[str, List[float]]] = {}
        for label, dir_spec in columns:
            tuner_dir = resolve_tuner_dir_for_workload(workload_dir, fallback_workload_dir, dir_spec)
            if tuner_dir is None:
                continue

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

            phase_series: Dict[str, List[float]] = {}
            if tuning_series:
                phase_series["tuning"] = tuning_series
            if stable_series:
                phase_series["stable"] = stable_series
            if phase_series:
                by_method[label] = phase_series

        if by_method:
            series_map[workload_dir.name] = by_method

    return series_map


def build_summary_rows(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    columns: Sequence[Tuple[str, str]],
    aggregate_stat: str,
    subset: str,
    trim_fraction: float,
    same_threshold_pct: float,
    aggregate_excluded_workloads: Optional[set[str]] = None,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    labels = [label for label, _ in columns]
    aggregate_excluded_workloads = aggregate_excluded_workloads or set()
    for phase in ("tuning", "stable"):
        per_method_workloads: Dict[str, List[str]] = {}
        if subset == "common":
            common_workloads = [
                workload
                for workload, method_map in workload_phase_series.items()
                if (
                    workload not in aggregate_excluded_workloads
                    and all(phase in method_map.get(label, {}) for label in labels)
                )
            ]
            for label in labels:
                excluded = _excluded_workloads_for_label(label)
                per_method_workloads[label] = [
                    workload for workload in common_workloads if workload not in excluded
                ]
        else:
            for label in labels:
                excluded = _excluded_workloads_for_label(label)
                per_method_workloads[label] = [
                    workload
                    for workload, method_map in workload_phase_series.items()
                    if phase in method_map.get(label, {})
                    and workload not in excluded
                    and workload not in aggregate_excluded_workloads
                ]

        phase_union_workloads = sorted(
            {
                workload
                for workloads in per_method_workloads.values()
                for workload in workloads
            }
        )

        for label in labels:
            workload_series = [
                workload_phase_series[workload][label][phase]
                for workload in per_method_workloads[label]
            ]
            n_improve, n_same, n_deteriorate = summarize_benchmark_outcomes(
                workload_phase_series=workload_phase_series,
                workloads=per_method_workloads[label],
                label=label,
                phase=phase,
                same_threshold_pct=same_threshold_pct,
            )
            aggregate_series = aggregate_improvement_series(
                workload_series,
                aggregate_stat,
                trim_fraction,
            )
            agg_pct, err = summarize_series(aggregate_series)
            agg_factor = pct_to_factor(agg_pct)
            rows.append(
                {
                    "phase": phase,
                    "method": label,
                    "n_workloads": len(per_method_workloads[label]),
                    "run_count": len(aggregate_series),
                    "aggregate_factor": agg_factor,
                    "aggregate_pct": agg_pct,
                    "aggregate_pct_err_low": err,
                    "aggregate_pct_err_high": err,
                    "workloads": ",".join(sorted(per_method_workloads[label])),
                    "phase_union_workloads": ",".join(phase_union_workloads),
                    "n_improve": n_improve,
                    "n_same": n_same,
                    "n_deteriorate": n_deteriorate,
                }
            )
    return rows


def filter_columns_present_in_series(
    columns: Sequence[Tuple[str, str]],
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
) -> List[Tuple[str, str]]:
    available_labels = {
        label
        for method_map in workload_phase_series.values()
        for label, phase_map in method_map.items()
        if phase_map
    }
    return [column for column in columns if column[0] in available_labels]


def filter_columns_with_data(
    columns: Sequence[Tuple[str, str]],
    summary_rows: Sequence[Dict[str, object]],
) -> Tuple[List[Tuple[str, str]], List[Dict[str, object]]]:
    labels_with_data = {
        str(row["method"])
        for row in summary_rows
        if (
            row.get("aggregate_pct") is not None
            or int(row.get("n_workloads") or 0) > 0
            or int(row.get("run_count") or 0) > 0
        )
    }
    filtered_columns = [column for column in columns if column[0] in labels_with_data]
    filtered_rows = [
        row for row in summary_rows if str(row["method"]) in labels_with_data
    ]
    return filtered_columns, filtered_rows


def plot_summary(
    summary_rows: Sequence[Dict[str, object]],
    columns: Sequence[Tuple[str, str]],
    aggregate_stat: str,
    subset: str,
    output_path: Path,
    error_bars: bool,
    ymin: float,
    ymax: float,
    auto_y: bool,
    auto_y_min: Optional[float],
    auto_y_max: Optional[float],
    base_font_size: int,
    x_label_map: Optional[Dict[str, str]] = None,
    x_group_map: Optional[Dict[str, str]] = None,
    x_shift_map: Optional[Dict[str, float]] = None,
    x_group_shift_map: Optional[Dict[str, float]] = None,
    x_group_y_shift_pts: float = 0.0,
    x_font_delta_map: Optional[Dict[str, float]] = None,
    right_trim_pts: float = RIGHT_TRIM_PTS,
    footer_text: str = "",
) -> None:
    labels = [label for label, _ in columns]
    base_labels: List[str] = []
    seen_bases: set[str] = set()
    for label in labels:
        base = _base_method_label(label)
        if base not in seen_bases:
            seen_bases.add(base)
            base_labels.append(base)
    row_map = {
        (str(row["method"]), str(row["phase"])): row
        for row in summary_rows
    }
    colors = {
        base: METHOD_COLORS.get(base, COLOR_CYCLE[idx % len(COLOR_CYCLE)])
        for idx, base in enumerate(base_labels)
    }

    x = np.arange(len(base_labels), dtype=float)
    x_by_base = {base: x[idx] for idx, base in enumerate(base_labels)}
    width = 0.34

    # if len(base_labels) <= 3:
    #     fig_width, fig_height = 6.6, 3.2
    # elif len(base_labels) <= 6:
    #     fig_width, fig_height = 7.2, 3.6
    # else:
    #     fig_width, fig_height = 8.2, 3.68
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.axhline(0.0, color="#666666", linewidth=1.0, linestyle="--", alpha=0.8, zorder=1)

    FS = base_font_size
    y_label_fontsize = FS + 2
    y_tick_fontsize = FS + 1
    legend_fontsize = FS + 2
    legend_handle_size = 0.9
    legend_shift_right_pts = 5.0
    lowered_x_labels = {"App", "System"}
    lowered_x_label_shift_pts = 10.0

    def _plot_method_bars(label: str) -> None:
        base = _base_method_label(label)
        tuning_row = row_map.get((label, "tuning"))
        stable_row = row_map.get((label, "stable"))
        tuning_val = np.nan if tuning_row is None or tuning_row["aggregate_pct"] is None else float(tuning_row["aggregate_pct"])
        stable_val = np.nan if stable_row is None or stable_row["aggregate_pct"] is None else float(stable_row["aggregate_pct"])
        dashed_variant = bool(_variant_suffix(label))
        tuning_yerr = None
        stable_yerr = None
        if error_bars and not dashed_variant:
            tuning_err = 0.0 if tuning_row is None or tuning_row["aggregate_pct_err_low"] is None else float(tuning_row["aggregate_pct_err_low"])
            stable_err = 0.0 if stable_row is None or stable_row["aggregate_pct_err_low"] is None else float(stable_row["aggregate_pct_err_low"])
            tuning_yerr = np.array([[tuning_err], [tuning_err]], dtype=float)
            stable_yerr = np.array([[stable_err], [stable_err]], dtype=float)

        xpos = x_by_base[base]
        facecolor = "none" if dashed_variant else colors[base]
        edgecolor = colors[base] if dashed_variant else "black"
        if dashed_variant and base in {"Q-Learning", "MLOS IPC", "MLOS Cache Misses"}:
            edgecolor = "black"
        linewidth = 1.2 if dashed_variant else 0.6
        linestyle = "--" if dashed_variant else "-"
        error_color = edgecolor if dashed_variant else "#333333"
        bar_zorder = 5 if dashed_variant else 4

        ax.bar(
            xpos - width / 2,
            [tuning_val],
            width=width,
            color=facecolor,
            hatch=None if dashed_variant else "////",
            edgecolor=edgecolor,
            linewidth=linewidth,
            linestyle=linestyle,
            yerr=tuning_yerr,
            capsize=3 if tuning_yerr is not None else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": error_color} if tuning_yerr is not None else None,
            zorder=bar_zorder,
        )
        ax.bar(
            xpos + width / 2,
            [stable_val],
            width=width,
            color=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            linestyle=linestyle,
            yerr=stable_yerr,
            capsize=3 if stable_yerr is not None else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": error_color} if stable_yerr is not None else None,
            zorder=bar_zorder,
        )

    for label in labels:
        if _variant_suffix(label):
            _plot_method_bars(label)
    for label in labels:
        if not _variant_suffix(label):
            _plot_method_bars(label)

    x_label_map = x_label_map or {}
    x_group_map = x_group_map or {}
    x_shift_map = x_shift_map or {}
    x_group_shift_map = x_group_shift_map or {}
    x_font_delta_map = x_font_delta_map or {}
    emphasized_x_label_deltas = {
        "Tuxbot App Metrics Dual Loop": 1.0,
        "MLOS + Tuxbot": 1.0,
        "MLOS": 1.0,
    }
    display_labels = [x_label_map.get(base, _display_label(base)) for base in base_labels]
    ax.set_xticks(x, display_labels, fontsize=FS + 1, rotation=0, ha="center")
    for base, tick in zip(base_labels, ax.get_xticklabels()):
        label = tick.get_text()
        x_offset_pts = 0.0
        y_offset_pts = lowered_x_label_shift_pts if label in lowered_x_labels else 0.0
        tick.set_fontsize(
            FS + 1 + emphasized_x_label_deltas.get(base, 0.0) + x_font_delta_map.get(base, 0.0)
        )
        if label == "TuxBot" and not x_group_map:
            x_offset_pts = -14.0
        elif label == "  MLOS" and not x_group_map:
            x_offset_pts = 14.0
        x_offset_pts += x_shift_map.get(base, 0.0)
        if x_offset_pts or y_offset_pts:
            tick.set_transform(
                tick.get_transform()
                + ScaledTranslation(x_offset_pts / 72.0, -y_offset_pts / 72.0, fig.dpi_scale_trans)
            )

    if x_group_map:
        groups: List[Tuple[int, int, str]] = []
        current_group: Optional[str] = None
        start_idx = 0
        for idx, base in enumerate(base_labels):
            group = x_group_map.get(base, "")
            if current_group is None:
                current_group = group
                start_idx = idx
                continue
            if group != current_group:
                groups.append((start_idx, idx - 1, current_group))
                current_group = group
                start_idx = idx
        if current_group is not None:
            groups.append((start_idx, len(base_labels) - 1, current_group))

        for idx, (start, end, group_label) in enumerate(groups):
            if not group_label:
                continue
            center = float(np.mean(x[start : end + 1]))
            ax.text(
                center,
                -0.16,
                group_label,
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=FS + 1,
                fontweight="medium",
                clip_on=False,
            ).set_transform(
                ax.get_xaxis_transform()
                + ScaledTranslation(
                    x_group_shift_map.get(group_label, 0.0) / 72.0,
                    -x_group_y_shift_pts / 72.0,
                    fig.dpi_scale_trans,
                )
            )
    ax.set_ylabel("Improvement %", fontsize=y_label_fontsize)
    if auto_y:
        tick_lo, tick_hi = _compute_25pct_axis_bounds(row_map, labels)
        if auto_y_min is not None:
            tick_lo = max(tick_lo, float(auto_y_min))
        if auto_y_max is not None:
            tick_hi = min(tick_hi, float(auto_y_max))
        if tick_hi <= tick_lo:
            tick_hi = tick_lo + 25.0
    else:
        tick_lo, tick_hi = ymin, ymax
    if not auto_y and abs(tick_lo + 60.0) < 1e-9 and abs(tick_hi - 100.0) < 1e-9:
        ax.set_yticks(np.arange(-60.0, 100.0 + 0.1, 20.0))
    else:
        tick_start = 25.0 * math.floor(tick_lo / 25.0)
        ax.set_yticks(np.arange(tick_start, tick_hi + 0.1, 25.0))
    ax.set_ylim(tick_lo, tick_hi)
    ax.tick_params(axis="y", labelsize=y_tick_fontsize)
    ax.grid(axis="y", alpha=0.3, linewidth=0.6)
    ax.set_axisbelow(True)
    legend_y = 0.87 if tick_hi <= 100.0 else 0.90
    legend_variant_label = None
    if any(label.endswith(" no Catastrophic") for label in labels):
        legend_variant_label = "No Catastrophic"
    elif any(label.endswith(" no Xapian") for label in labels):
        legend_variant_label = "No Xapian"

    legend_handles = [
        Patch(facecolor="white", edgecolor="#444444", hatch="////", label="Tuning"),
        Patch(facecolor="white", edgecolor="#444444", label="Stable"),
    ]
    if legend_variant_label is not None:
        legend_handles.append(
            Patch(facecolor="white", edgecolor="#444444", linestyle="--", linewidth=1.6, label=legend_variant_label)
        )

    fig.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(0.985, legend_y),
        bbox_transform=fig.transFigure + ScaledTranslation(legend_shift_right_pts / 72.0, 0.0, fig.dpi_scale_trans),
        ncol=len(legend_handles),
        frameon=False,
        fontsize=legend_fontsize,
        handlelength=legend_handle_size,
        handleheight=legend_handle_size,
        columnspacing=1.1,
        handletextpad=0.5,
        borderaxespad=0.2,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.margins(x=0.04, y=0.16)
    bottom_rect = 0.08 if x_group_map else 0.0
    if footer_text:
        bottom_rect = max(bottom_rect, 0.16 if x_group_map else 0.10)
        fig.text(
            0.5,
            0.012,
            footer_text,
            ha="center",
            va="bottom",
            fontsize=max(8, FS - 6),
            fontstyle="italic",
        )
    fig.tight_layout(pad=0.35, rect=(0.0, bottom_rect, 1.0, 0.88))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    tight_bbox = fig.get_tightbbox(fig.canvas.get_renderer())
    trimmed_bbox = Bbox.from_extents(
        tight_bbox.x0,
        tight_bbox.y0,
        tight_bbox.x1 - right_trim_pts / 72.0,
        tight_bbox.y1,
    )
    fig.savefig(output_path, bbox_inches=trimmed_bbox, pad_inches=0.02)
    plt.close(fig)


def write_csv(rows: Sequence[Dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "phase",
                "method",
                "n_workloads",
                "run_count",
                "aggregate_factor",
                "aggregate_pct",
                "aggregate_pct_err_low",
                "aggregate_pct_err_high",
                "workloads",
                "phase_union_workloads",
                "n_improve",
                "n_same",
                "n_deteriorate",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _format_summary_block(
    row: Optional[Dict[str, object]],
    method_name: str,
) -> Tuple[str, str, str, str]:
    is_todo_placeholder = method_name in TODO_PLACEHOLDER_METHODS
    if row is None:
        missing = "TODO" if is_todo_placeholder else "XXX"
        return missing, missing, missing, missing

    factor = row.get("aggregate_factor")
    pct = row.get("aggregate_pct")
    n_workloads_text = str(int(row.get("n_workloads") or 0))
    factor_text = (
        "TODO"
        if factor is None and is_todo_placeholder
        else ("XXX" if factor is None else f"{float(factor):.4f}")
    )
    pct_text = (
        "TODO"
        if pct is None and is_todo_placeholder
        else ("XXX" if pct is None else f"{float(pct):+.2f}%")
    )
    outcome_text = (
        f"{int(row.get('n_improve') or 0)}/"
        f"{int(row.get('n_same') or 0)}/"
        f"{int(row.get('n_deteriorate') or 0)}"
    )
    return n_workloads_text, factor_text, pct_text, outcome_text


def _benchmark_table_method_names(columns: Sequence[Tuple[str, str]]) -> List[str]:
    return [
        method_name
        for method_name, _dirname in columns
        if _variant_suffix(method_name) != " no Catastrophic"
    ]


def _format_benchmark_cell(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    workload: str,
    method_name: str,
    phase: str,
) -> str:
    if workload in _excluded_workloads_for_label(method_name):
        return "N/A"

    is_todo_placeholder = method_name in TODO_PLACEHOLDER_METHODS
    series = workload_phase_series.get(workload, {}).get(method_name, {}).get(phase, [])
    mean_pct, std_pct = summarize_series(series)
    if mean_pct is None or std_pct is None:
        return "TODO" if is_todo_placeholder else "XXX"
    return f"{mean_pct:+.2f}+/-{std_pct:.2f}"


def _print_benchmark_phase_table(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    columns: Sequence[Tuple[str, str]],
    phase: str,
) -> None:
    phase_title = phase.capitalize()
    method_names = _benchmark_table_method_names(columns)
    if not method_names:
        return
    method_labels = [_summary_label(method_name) for method_name in method_names]
    workloads = [
        workload
        for workload, method_map in workload_phase_series.items()
        if any(
            phase in method_map.get(method_name, {})
            or workload in _excluded_workloads_for_label(method_name)
            for method_name in method_names
        )
    ]
    if not workloads:
        return

    row_cells = [
        [
            _format_benchmark_cell(
                workload_phase_series=workload_phase_series,
                workload=workload,
                method_name=method_name,
                phase=phase,
            )
            for method_name in method_names
        ]
        for workload in workloads
    ]

    benchmark_width = max(len("Benchmark"), *(len(workload) for workload in workloads)) + 2
    column_widths = [
        max(
            len(method_label),
            *(len(cells[col_idx]) for cells in row_cells),
        )
        for col_idx, method_label in enumerate(method_labels)
    ]

    print(f"\nPer-benchmark {phase_title} (% mean+/-stdev across paired reruns):")
    header_line = (
        f"{'Benchmark':<{benchmark_width}} "
        + " ".join(
            f"{method_label:>{column_widths[idx]}}"
            for idx, method_label in enumerate(method_labels)
        )
    )
    print(header_line)
    print("-" * len(header_line))
    for workload, cells in zip(workloads, row_cells):
        print(
            f"{workload:<{benchmark_width}} "
            + " ".join(
                f"{cell:>{column_widths[idx]}}"
                for idx, cell in enumerate(cells)
            )
        )


def _latex_escape(text: str) -> str:
    return (
        text.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
        .replace("#", r"\#")
    )


def _latex_benchmark_label(workload: str) -> str:
    if workload in LATEX_BENCHMARK_LABEL_OVERRIDES:
        return LATEX_BENCHMARK_LABEL_OVERRIDES[workload]
    label = benchmark_label(workload)
    if label == "TPCC":
        label = "TPC-C"
    return _latex_escape(label)


def _latex_method_label(method_name: str) -> str:
    summary = _summary_label(method_name)
    return LATEX_METHOD_LABEL_MAP.get(summary, _latex_escape(summary))


def _benchmark_table_workloads(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    method_names: Sequence[str],
    subset: str,
) -> List[str]:
    workloads: List[str] = []
    for workload, method_map in workload_phase_series.items():
        if subset == "common":
            include = all(
                workload not in _excluded_workloads_for_label(method_name)
                and "tuning" in method_map.get(method_name, {})
                and "stable" in method_map.get(method_name, {})
                for method_name in method_names
            )
        else:
            include = any(
                workload not in _excluded_workloads_for_label(method_name)
                and (
                    "tuning" in method_map.get(method_name, {})
                    or "stable" in method_map.get(method_name, {})
                )
                for method_name in method_names
            )
        if include:
            workloads.append(workload)
    return workloads


def _benchmark_phase_mean(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    workload: str,
    method_name: str,
    phase: str,
) -> Optional[float]:
    if workload in _excluded_workloads_for_label(method_name):
        return None
    series = workload_phase_series.get(workload, {}).get(method_name, {}).get(phase, [])
    mean_pct, _std_pct = summarize_series(series)
    return mean_pct


def _best_methods_for_benchmark_phase(
    values: Dict[str, Optional[float]],
    goal: str,
) -> set[str]:
    numeric_values = {
        method_name: float(value)
        for method_name, value in values.items()
        if value is not None and math.isfinite(float(value))
    }
    if not numeric_values:
        return set()
    best_value = (
        max(numeric_values.values())
        if goal == "maximize"
        else min(numeric_values.values())
    )
    return {
        method_name
        for method_name, value in numeric_values.items()
        if math.isclose(value, best_value, rel_tol=1e-9, abs_tol=1e-9)
    }


def _format_latex_pct_value(value: Optional[float], bold: bool) -> str:
    if value is None:
        return "XXX"
    display_value = 0.0 if abs(float(value)) < 0.05 else float(value)
    if abs(display_value) <= BENCHMARK_SAME_THRESHOLD_PCT:
        color = LATEX_NEUTRAL_COLOR
    elif display_value > 0.0:
        color = LATEX_POSITIVE_COLOR
    else:
        color = LATEX_NEGATIVE_COLOR
    token = rf"\textcolor{{{color}}}{{{display_value:.1f}}}"
    if bold:
        return rf"\textbf{{{token}}}"
    return token


def _format_latex_raw_value(value: Optional[float], bold: bool = False) -> str:
    if value is None:
        return "XXX"
    text = f"{float(value):.1f}"
    if text == "-0.0":
        text = "0.0"
    if bold:
        return rf"\textbf{{{text}}}"
    return text


def _default_latex_table_caption(
    subset: str,
    method_count: int,
    workload_count: int,
) -> str:
    subset_text = "common-subset" if subset == "common" else "union-subset"
    tail = (
        f"Benchmarks with complete entries across all {method_count} methods: {workload_count}."
        if subset == "common"
        else f"Benchmarks shown across the displayed methods: {workload_count}."
    )
    return (
        f"Per-benchmark improvement over Fixed for the {subset_text} aggregate comparison. "
        r"The Goal column reports whether each benchmark is latency minimization or throughput maximization. "
        r"Default reports the raw phase metric under Fixed; each tuner reports raw phase metric and relative improvement in separate subcolumns. "
        r"Green denotes improvement over Default, yellow denotes changes within \pm5\%, and red denotes degradation. "
        r"Bold marks the best raw value per phase within each benchmark. "
        + tail
    )


def _default_latex_table_label(output_path: Path) -> str:
    stem = "".join(ch if ch.isalnum() else "_" for ch in output_path.stem).strip("_")
    if not stem:
        stem = "retry_aggregate_by_benchmark"
    return f"tab:{stem}"


def write_latex_benchmark_table(
    workload_dirs: Sequence[Path],
    fallback_fixed_map: Dict[str, Path],
    fallback_tuner_map: Dict[str, Path],
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    columns: Sequence[Tuple[str, str]],
    tuning_window: Tuple[int, int],
    stable_window: Tuple[int, int],
    subset: str,
    output_path: Path,
    caption: str = "",
    label: str = "",
) -> None:
    method_names = _benchmark_table_method_names(columns)
    if not method_names:
        raise SystemExit("No non-'no Catastrophic' methods available for LaTeX table output.")

    workloads = _benchmark_table_workloads(
        workload_phase_series=workload_phase_series,
        method_names=method_names,
        subset=subset,
    )
    if not workloads:
        raise SystemExit("No workloads available for LaTeX per-benchmark table output.")

    workload_dir_map = {workload_dir.name: workload_dir for workload_dir in workload_dirs}
    dir_spec_by_method = {method_name: dir_spec for method_name, dir_spec in columns}
    header_labels = [_latex_method_label(method_name) for method_name in method_names]
    row_lines: List[str] = []
    for workload in workloads:
        workload_dir = workload_dir_map.get(workload)
        if workload_dir is None:
            continue
        fixed_dir = resolve_fixed_dir_for_workload(workload_dir, fallback_fixed_map)
        if fixed_dir is None or not fixed_dir.is_dir():
            continue
        fallback_workload_dir = fallback_tuner_map.get(workload)
        metric_name, goal = detect_metric_goal_for_workload(
            workload_dir,
            fixed_dir,
            fallback_workload_dir,
        )
        if metric_name is None or goal is None:
            continue

        default_tuning_mean, _ = summarize_series(
            collect_phase_run_means(fixed_dir, metric_name, tuning_window)
        )
        default_stable_mean, _ = summarize_series(
            collect_phase_run_means(fixed_dir, metric_name, stable_window)
        )
        default_by_phase = {
            "tuning": default_tuning_mean,
            "stable": default_stable_mean,
        }

        raw_by_method_phase: Dict[str, Dict[str, Optional[float]]] = {}
        pct_by_method_phase: Dict[str, Dict[str, Optional[float]]] = {}
        for method_name in method_names:
            raw_phase_map: Dict[str, Optional[float]] = {}
            pct_phase_map: Dict[str, Optional[float]] = {}
            if workload in _excluded_workloads_for_label(method_name):
                raw_phase_map["tuning"] = None
                raw_phase_map["stable"] = None
                pct_phase_map["tuning"] = None
                pct_phase_map["stable"] = None
                raw_by_method_phase[method_name] = raw_phase_map
                pct_by_method_phase[method_name] = pct_phase_map
                continue

            tuner_dir = resolve_tuner_dir_for_workload(
                workload_dir,
                fallback_workload_dir,
                dir_spec_by_method[method_name],
            )
            if tuner_dir is None:
                raw_phase_map["tuning"] = None
                raw_phase_map["stable"] = None
            else:
                raw_phase_map["tuning"], _ = summarize_series(
                    collect_phase_run_means(tuner_dir, metric_name, tuning_window)
                )
                raw_phase_map["stable"], _ = summarize_series(
                    collect_phase_run_means(tuner_dir, metric_name, stable_window)
                )
            pct_phase_map["tuning"] = improvement_pct(
                default_by_phase["tuning"],
                raw_phase_map["tuning"],
                goal,
            )
            pct_phase_map["stable"] = improvement_pct(
                default_by_phase["stable"],
                raw_phase_map["stable"],
                goal,
            )
            raw_by_method_phase[method_name] = raw_phase_map
            pct_by_method_phase[method_name] = pct_phase_map

        tuning_best_methods = _best_methods_for_benchmark_phase(
            {
                method_name: raw_by_method_phase[method_name]["tuning"]
                for method_name in method_names
            },
            goal=goal,
        )
        stable_best_methods = _best_methods_for_benchmark_phase(
            {
                method_name: raw_by_method_phase[method_name]["stable"]
                for method_name in method_names
            },
            goal=goal,
        )

        cells: List[str] = [
            objective_label(workload, metric_name, goal),
            _format_latex_raw_value(default_tuning_mean),
            _format_latex_raw_value(default_stable_mean),
        ]
        for method_name in method_names:
            cells.append(
                _format_latex_raw_value(
                    raw_by_method_phase[method_name]["tuning"],
                    bold=method_name in tuning_best_methods
                    and raw_by_method_phase[method_name]["tuning"] is not None,
                )
            )
            cells.append(
                _format_latex_pct_value(
                    pct_by_method_phase[method_name]["tuning"],
                    bold=method_name in tuning_best_methods
                    and raw_by_method_phase[method_name]["tuning"] is not None,
                )
            )
            cells.append(
                _format_latex_raw_value(
                    raw_by_method_phase[method_name]["stable"],
                    bold=method_name in stable_best_methods
                    and raw_by_method_phase[method_name]["stable"] is not None,
                )
            )
            cells.append(
                _format_latex_pct_value(
                    pct_by_method_phase[method_name]["stable"],
                    bold=method_name in stable_best_methods
                    and raw_by_method_phase[method_name]["stable"] is not None,
                )
            )
        row_lines.append(
            f"{_latex_benchmark_label(workload)} & " + " & ".join(cells) + r" \\"
        )

    block = "\n".join(
        [
            r"\begin{table*}[t]",
            r"\centering",
            r"\scriptsize",
            r"\setlength{\tabcolsep}{3.5pt}",
            r"\resizebox{\textwidth}{!}{%",
            rf"\begin{{tabular}}{{llcc{'cccc' * len(method_names)}}}",
            r"\toprule",
            "Benchmark & Goal & "
            + r"\multicolumn{2}{c}{Default} & "
            + " & ".join(
                rf"\multicolumn{{4}}{{c}}{{{header_label}}}"
                for header_label in header_labels
            )
            + r" \\",
            r"\cmidrule(lr){3-4}"
            + "".join(
                rf"\cmidrule(lr){{{5 + (4 * idx)}-{8 + (4 * idx)}}}"
                for idx in range(len(method_names))
            ),
            " & & "
            + "Tun. & Sta. & "
            + " & ".join(
                subcolumn
                for _method_name in method_names
                for subcolumn in ("Tun.", r"\%", "Sta.", r"\%")
            )
            + r" \\",
            r"\midrule",
            *row_lines,
            r"\bottomrule",
            r"\end{tabular}%",
            r"}",
            rf"\caption{{{caption or _default_latex_table_caption(subset, len(method_names), len(workloads))}}}",
            rf"\label{{{label or _default_latex_table_label(output_path)}}}",
            r"\end{table*}",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(block + "\n")


def print_cli_summary(
    summary_rows: Sequence[Dict[str, object]],
    columns: Sequence[Tuple[str, str]],
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
) -> None:
    row_map = {
        (str(row["method"]), str(row["phase"])): row
        for row in summary_rows
    }
    method_labels = [_summary_label(method_name) for method_name, _dirname in columns]
    method_width = max(len("Method"), *(len(label) for label in method_labels)) + 2
    block_width = 38
    header_line = (
        f"{'Method':<{method_width}} "
        f"{'Tuning':^{block_width}} "
        f"{'Stable':^{block_width}}"
    )
    subheader_line = (
        f"{'':<{method_width}} "
        f"{'n':>4} {'Factor':>10} {'Improvement':>12} {'I/S/D':>9} "
        f"{'n':>4} {'Factor':>10} {'Improvement':>12} {'I/S/D':>9}"
    )

    print("\nSummary:")
    print(header_line)
    print(subheader_line)
    print("-" * len(subheader_line))

    for method_name, _dirname in columns:
        tuning_block = _format_summary_block(
            row_map.get((method_name, "tuning")),
            method_name,
        )
        stable_block = _format_summary_block(
            row_map.get((method_name, "stable")),
            method_name,
        )
        print(
            f"{_summary_label(method_name):<{method_width}} "
            f"{tuning_block[0]:>4} {tuning_block[1]:>10} {tuning_block[2]:>12} {tuning_block[3]:>9} "
            f"{stable_block[0]:>4} {stable_block[1]:>10} {stable_block[2]:>12} {stable_block[3]:>9}"
        )

    _print_benchmark_phase_table(
        workload_phase_series=workload_phase_series,
        columns=columns,
        phase="tuning",
    )
    _print_benchmark_phase_table(
        workload_phase_series=workload_phase_series,
        columns=columns,
        phase="stable",
    )


def main() -> None:
    args = parse_args()
    global NO_CATASTROPHIC_EXTRA_EXCLUDED
    NO_CATASTROPHIC_EXTRA_EXCLUDED = {
        item.strip() for item in args.no_catastrophic_extra_exclude.split(",") if item.strip()
    }
    aggregate_excluded_workloads = {
        item.strip() for item in args.aggregate_exclude_workloads.split(",") if item.strip()
    }
    columns = parse_custom_columns(args.custom_columns)
    tuning_window = parse_window(args.tuning_window)
    stable_window = parse_window(args.stable_window)
    workload_dirs = resolve_workload_dirs(args.result_paths)
    if not workload_dirs:
        raise SystemExit("No workload dirs resolved from --result-paths.")

    if args.workloads:
        requested = [w.strip() for w in args.workloads.split(",") if w.strip()]
        workload_map = {wd.name: wd for wd in workload_dirs}
        workload_dirs = [workload_map[name] for name in requested if name in workload_map]
        if not workload_dirs:
            raise SystemExit("No workload dirs remained after applying --workloads.")

    fallback_fixed_map = resolve_fixed_dirs(args.fallback_fixed_paths)
    fallback_tuner_map = _build_fallback_tuner_map(args.fallback_tuner_paths)
    workload_phase_series = collect_workload_phase_series(
        workload_dirs=workload_dirs,
        fallback_fixed_map=fallback_fixed_map,
        fallback_tuner_map=fallback_tuner_map,
        columns=columns,
        tuning_window=tuning_window,
        stable_window=stable_window,
    )
    columns = filter_columns_present_in_series(columns, workload_phase_series)
    if not columns:
        raise SystemExit("No requested methods were found in the available workload series.")
    summary_rows = build_summary_rows(
        workload_phase_series=workload_phase_series,
        columns=columns,
        aggregate_stat=args.aggregate_stat,
        subset=args.subset,
        trim_fraction=args.trim_fraction,
        same_threshold_pct=args.same_threshold_pct,
        aggregate_excluded_workloads=aggregate_excluded_workloads,
    )
    columns, summary_rows = filter_columns_with_data(columns, summary_rows)
    if not columns:
        raise SystemExit("No methods with data remained after filtering missing methods.")

    plot_summary(
        summary_rows=summary_rows,
        columns=columns,
        aggregate_stat=args.aggregate_stat,
        subset=args.subset,
        output_path=Path(args.plot_output),
        error_bars=args.error_bars,
        ymin=args.ymin,
        ymax=args.ymax,
        auto_y=args.auto_y,
        auto_y_min=args.auto_y_min,
        auto_y_max=args.auto_y_max,
        base_font_size=args.base_font_size,
        x_label_map=parse_label_map(args.x_label_map),
        x_group_map=parse_label_map(args.x_group_map),
        x_shift_map=parse_float_map(args.x_shift_map),
        x_group_shift_map=parse_float_map(args.x_group_shift_map),
        x_group_y_shift_pts=args.x_group_y_shift_pts,
        x_font_delta_map=parse_float_map(args.x_font_delta_map),
        right_trim_pts=args.right_trim_pts,
    )

    if args.csv_output:
        write_csv(summary_rows, Path(args.csv_output))

    if args.latex_table_output:
        write_latex_benchmark_table(
            workload_dirs=workload_dirs,
            fallback_fixed_map=fallback_fixed_map,
            fallback_tuner_map=fallback_tuner_map,
            workload_phase_series=workload_phase_series,
            columns=columns,
            tuning_window=tuning_window,
            stable_window=stable_window,
            subset=args.subset,
            output_path=Path(args.latex_table_output),
            caption=args.latex_table_caption,
            label=args.latex_table_label,
        )

    print(f"Wrote plot: {Path(args.plot_output).resolve()}")
    if args.latex_table_output:
        print(f"Wrote LaTeX table: {Path(args.latex_table_output).resolve()}")
    print_cli_summary(summary_rows, columns, workload_phase_series)

    print("\nIncluded benchmarks:")
    phase_names = ("tuning", "stable")
    for phase in phase_names:
        phase_rows = [row for row in summary_rows if row["phase"] == phase]
        phase_union = sorted(
            {
                bench
                for row in phase_rows
                for bench in str(row["workloads"]).split(",")
                if bench
            }
        )
        print(f"{phase}: {', '.join(phase_union) if phase_union else 'NONE'}")
        for row in phase_rows:
            benches = [bench for bench in str(row["workloads"]).split(",") if bench]
            method_name = str(row["method"])
            is_todo_placeholder = method_name in TODO_PLACEHOLDER_METHODS
            print(
                f"  - {_summary_label(method_name)}: "
                f"{', '.join(benches) if benches else ('TODO' if is_todo_placeholder else 'NONE')}"
            )


if __name__ == "__main__":
    main()
