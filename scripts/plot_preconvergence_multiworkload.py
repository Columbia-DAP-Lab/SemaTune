#!/usr/bin/env python3
"""
Multi-workload pre-convergence robustness plot for MLOS vs Tuxbot.

Outputs one compact figure with three vertically stacked panels:
1) Mean poor-measurement rate (%)
2) P10 poor-measurement rate (%)
3) Variability normalized to fixed reference (%)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np

HISTORY_PATTERNS: Tuple[str, ...] = ("optimization_history_*.json", "dual_loop_*.json")
DEFAULT_COLORS: Tuple[str, ...] = ("#E67E22", "#1f77b4", "#2ca02c", "#9467bd", "#7f7f7f", "#17becf")


@dataclass
class WindowSpec:
    start: int
    end: int


@dataclass
class RunStats:
    run_mean: float
    run_std: float
    pct_worse_than_fixed: Optional[float]
    variability_pct_of_fixed: Optional[float]


@dataclass
class TunerSummary:
    runs: int
    points: int
    mean_pct_worse: Optional[float]
    p10_pct_worse: Optional[float]
    variability_pct_of_fixed: Optional[float]
    mean_pct_worse_std: Optional[float]
    p10_pct_worse_std: Optional[float]
    variability_pct_of_fixed_std: Optional[float]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot multi-workload pre-convergence robustness for MLOS vs Tuxbot.")
    p.add_argument(
        "--experiments",
        nargs="+",
        default=["dcperf_spark_tput", "tpcc_hi_p99", "silo_hi_p99", "sysbench_cpu_tput"],
        help="Experiment names (workloads).",
    )
    p.add_argument("--start", type=int, default=0, help="Window start (inclusive).")
    p.add_argument("--end", type=int, default=60, help="Window end (inclusive).")
    p.add_argument(
        "--results-root",
        default="/mydata/os-param-tuning/all_results",
        help="Root that contains results_config_full_param_<experiment> directories.",
    )
    p.add_argument(
        "--results-pattern",
        default="results_config_full_param_{experiment}",
        help=(
            "Glob-style directory pattern under --results-root for workload results. "
            "Use {experiment} as placeholder, e.g. "
            "'results_config_full_param_{experiment}_*_retry'."
        ),
    )
    p.add_argument(
        "--fixed-pattern",
        default=None,
        help=(
            "Optional glob-style directory pattern under --results-root for the fixed baseline. "
            "If unset, uses --results-pattern. Example: "
            "'results_config_full_param_{experiment}_new'."
        ),
    )
    p.add_argument("--fixed-tuner", default="fixed", help="Fixed tuner directory name.")
    p.add_argument("--mlos-tuner", default="mlos", help="MLOS tuner directory name.")
    p.add_argument(
        "--tuxbot-tuner",
        default="llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3",
        help="Tuxbot tuner directory name.",
    )
    p.add_argument(
        "--series",
        default="",
        help=(
            "Optional comma-separated LABEL:DIR[:COLOR] entries. "
            'Example: "Tux-Bot App:llm_dual_app_metrics_final_actor:#1f77b4,'
            'Tux-Bot Sys:llm_dual_indirect_all_mode3_final_actor:#9467bd,'
            'MLOS:mlos_50_tuning_only:#E67E22". '
            "When set, overrides --mlos-tuner/--tuxbot-tuner."
        ),
    )
    p.add_argument("--converged-only", action="store_true", help="Use converged runs only.")
    p.add_argument(
        "--error-bars",
        action="store_true",
        help="Add run-level error bars to the robustness bars.",
    )
    p.add_argument("--output-dir", default="/tmp", help="Output directory.")
    p.add_argument("--plot-output", default=None, help="Optional explicit PNG output path.")
    p.add_argument("--csv-output", default=None, help="Optional explicit CSV output path.")
    return p.parse_args()


def parse_series_arg(spec: str, legacy_mlos_dir: str, legacy_tuxbot_dir: str) -> List[Dict[str, str]]:
    if not spec.strip():
        return [
            {"label": "MLOS", "dir": legacy_mlos_dir, "color": DEFAULT_COLORS[0]},
            {"label": "Tuxbot", "dir": legacy_tuxbot_dir, "color": DEFAULT_COLORS[1]},
        ]

    out: List[Dict[str, str]] = []
    for idx, chunk in enumerate(spec.split(",")):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) not in (2, 3):
            raise SystemExit(
                "--series entries must be LABEL:DIR or LABEL:DIR:COLOR; "
                f"got {chunk!r}"
            )
        label, dirname = parts[0], parts[1]
        color = parts[2] if len(parts) == 3 and parts[2] else DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]
        if not label or not dirname:
            raise SystemExit(
                "--series entries must be LABEL:DIR or LABEL:DIR:COLOR; "
                f"got {chunk!r}"
            )
        out.append({"label": label, "dir": dirname, "color": color})
    if not out:
        raise SystemExit("--series did not contain any valid entries.")
    return out


def canonical_goal(raw_goal: Optional[str]) -> str:
    g = (raw_goal or "").strip().lower()
    if g in {"maximize", "max", "higher_is_better"}:
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


def to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    arr = sorted(float(v) for v in values)
    if len(arr) == 1:
        return arr[0]
    pos = (len(arr) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return arr[lo]
    frac = pos - lo
    return arr[lo] + (arr[hi] - arr[lo]) * frac


def bootstrap_percentile_std(values: Sequence[float], q: float, samples: int = 400) -> Optional[float]:
    arr = np.array([float(v) for v in values if v is not None and math.isfinite(float(v))], dtype=float)
    if arr.size < 2:
        return None
    rng = np.random.default_rng(0)
    boot = np.empty(samples, dtype=float)
    for idx in range(samples):
        sample = rng.choice(arr, size=arr.size, replace=True)
        boot[idx] = float(np.quantile(sample, q))
    return float(np.std(boot, ddof=1)) if boot.size > 1 else None


def load_json(fp: Path) -> Optional[Dict[str, Any]]:
    try:
        with fp.open("r") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def iter_history_files(tuner_dir: Path) -> Iterable[Path]:
    for pattern in HISTORY_PATTERNS:
        for fp in sorted(tuner_dir.glob(pattern)):
            if fp.is_file():
                yield fp


def pick_iteration(entry: Dict[str, Any], fallback_pos: int) -> int:
    for key in ("iteration", "window_number", "index"):
        if key in entry:
            try:
                return int(entry[key])
            except Exception:
                pass
    return fallback_pos


def extract_entry_value(entry: Dict[str, Any], metric_name: Optional[str]) -> Optional[float]:
    metrics = entry.get("metrics", {}) or {}
    sys_metrics = entry.get("system_metrics", {}) or {}
    candidates: List[Any] = [entry.get("raw_metric_value")]
    if metric_name:
        candidates.append(metrics.get(metric_name))
        candidates.append(sys_metrics.get(metric_name))
    candidates.append(entry.get("reward"))
    for c in candidates:
        parsed = to_float(c)
        if parsed is not None:
            return parsed
    return None


def detect_metric_and_goal(history_data: Dict[str, Any]) -> Tuple[Optional[str], str]:
    config = history_data.get("config", {}) or {}
    metric = config.get("optimization_metric")
    metric_name = str(metric).strip() if metric else None
    goal = canonical_goal(config.get("optimization_goal"))
    return metric_name, goal


def detect_dir_metric_and_goal(
    tuner_dir: Path,
    converged_only: bool,
    converged_expected: Optional[int],
) -> Tuple[Optional[str], Optional[str]]:
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        if converged_only and not is_converged(data, fallback_expected=converged_expected):
            continue
        metric_name, goal_name = detect_metric_and_goal(data)
        return metric_name, goal_name
    return None, None


def expected_total_windows(history_data: Dict[str, Any], fallback_expected: Optional[int]) -> Optional[int]:
    config = history_data.get("config", {}) or {}
    max_iter = to_int(config.get("max_iterations"))
    post = to_int(config.get("post_tuning_windows"))
    if max_iter is None:
        return fallback_expected
    if post is None:
        post = 0
    return max_iter + post


def is_converged(history_data: Dict[str, Any], fallback_expected: Optional[int]) -> bool:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return False
    expected = expected_total_windows(history_data, fallback_expected=fallback_expected)
    if expected is None or expected <= 0:
        return False
    rows = [e for e in history if isinstance(e, dict)]
    if len(rows) < expected:
        return False
    max_iter = 0
    for pos, e in enumerate(rows, start=1):
        max_iter = max(max_iter, pick_iteration(e, pos))
    return max_iter >= expected


def select_window_values(history_data: Dict[str, Any], metric_name: Optional[str], window: WindowSpec) -> List[float]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return []
    values: List[float] = []
    for pos, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        it = pick_iteration(entry, pos)
        if it < window.start or it > window.end:
            continue
        v = extract_entry_value(entry, metric_name)
        if v is not None:
            values.append(v)
    return values


def collect_window_run_values(
    tuner_dir: Path,
    metric_name: Optional[str],
    window: WindowSpec,
    converged_only: bool,
    converged_expected: Optional[int],
) -> List[List[float]]:
    per_run_values: List[List[float]] = []
    if not tuner_dir.is_dir():
        return per_run_values
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        if converged_only and not is_converged(data, fallback_expected=converged_expected):
            continue
        vals = select_window_values(data, metric_name, window)
        if vals:
            per_run_values.append(vals)
    return per_run_values


def pct_worse(goal: str, fixed_ref: Optional[float], values: Sequence[float]) -> Optional[float]:
    if fixed_ref is None or not values:
        return None
    if goal == "maximize":
        bad = sum(1 for v in values if v < fixed_ref)
    else:
        bad = sum(1 for v in values if v > fixed_ref)
    return (bad / len(values)) * 100.0


def summarize_tuner(
    tuner_dir: Path,
    window: WindowSpec,
    fixed_run_refs: Sequence[float],
    forced_metric: Optional[str],
    forced_goal: Optional[str],
    converged_only: bool,
    converged_expected: Optional[int],
) -> Tuple[TunerSummary, Optional[str], Optional[str]]:
    run_values: List[List[float]] = []
    metric_name: Optional[str] = None
    goal_name: Optional[str] = None

    if not tuner_dir.is_dir():
        return TunerSummary(0, 0, None, None, None, None, None, None), None, None

    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None:
            continue
        if converged_only and not is_converged(data, fallback_expected=converged_expected):
            continue
        m_name, g_name = detect_metric_and_goal(data)
        m_name = forced_metric or m_name
        g_name = canonical_goal(forced_goal or g_name)
        metric_name = metric_name or m_name
        goal_name = goal_name or g_name
        vals = select_window_values(data, m_name, window)
        if not vals:
            continue
        run_values.append(vals)

    paired_count = min(len(run_values), len(fixed_run_refs))
    if paired_count <= 0:
        return TunerSummary(0, 0, None, None, None, None, None, None), metric_name, goal_name

    run_stats: List[RunStats] = []
    for idx in range(paired_count):
        vals = run_values[idx]
        fixed_ref = float(fixed_run_refs[idx])
        run_std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        run_stats.append(
            RunStats(
                run_mean=statistics.fmean(vals),
                run_std=run_std,
                pct_worse_than_fixed=pct_worse(goal_name or "minimize", fixed_ref, vals),
                variability_pct_of_fixed=(run_std / abs(fixed_ref) * 100.0)
                if fixed_ref != 0
                else None,
            )
        )

    pct_bad = [r.pct_worse_than_fixed for r in run_stats if r.pct_worse_than_fixed is not None]
    run_var_pct = [r.variability_pct_of_fixed for r in run_stats if r.variability_pct_of_fixed is not None]
    points = sum(len(vals) for vals in run_values[:paired_count])

    variability_pct = statistics.fmean(run_var_pct) if run_var_pct else None
    pct_bad_std = statistics.stdev(pct_bad) if len(pct_bad) > 1 else (0.0 if pct_bad else None)

    return (
        TunerSummary(
            runs=len(run_stats),
            points=points,
            mean_pct_worse=statistics.fmean(pct_bad) if pct_bad else None,
            p10_pct_worse=percentile(pct_bad, 0.10) if pct_bad else None,
            variability_pct_of_fixed=variability_pct,
            mean_pct_worse_std=pct_bad_std,
            p10_pct_worse_std=pct_bad_std,
            variability_pct_of_fixed_std=statistics.stdev(run_var_pct) if len(run_var_pct) > 1 else (0.0 if run_var_pct else None),
        ),
        metric_name,
        goal_name,
    )


def workload_label(exp: str) -> str:
    mapping = {
        "dcperf_spark_tput": "Spark",
        "masstree_hi_p99": "Masstree",
        "otmetrics_p99": "OTMetrics",
        "sibench_hi_p99": "Sibench",
        "tpcc_hi_p99": "TPCC",
        "silo_hi_p99": "Silo",
        "sphinx_tput_max": "Sphinx",
        "sysbench_cpu_tput": "Sys-CPU",
        "sysbench_oltp_rw_hi_p99": "Sys-OLTP-RW",
        "twitter_p99": "Twitter",
        "wikipedia_p99": "Wikipedia",
        "xapian_hi_p99": "Xapian",
        "ycsb_hi_p99": "YCSB",
    }
    return mapping.get(exp, exp)


def resolve_workload_dir(root: Path, pattern: str, exp: str) -> Optional[Path]:
    rendered = pattern.format(experiment=exp)
    matches = sorted(root.glob(rendered))
    dirs = [m for m in matches if m.is_dir()]
    if not dirs:
        # Fallback for roots whose top-level name differs from the workload leaf
        # (e.g., twitter_hi_p99_new contains child twitter_p99).
        broader_rendered = rendered.replace(exp, "*")
        while "**" in broader_rendered:
            broader_rendered = broader_rendered.replace("**", "*")
        broader_matches = sorted(root.glob(broader_rendered))
        for full_dir in [m for m in broader_matches if m.is_dir()]:
            workload_dir = full_dir / exp
            if workload_dir.is_dir():
                return workload_dir
    if not dirs:
        return None
    # Prefer the lexicographically last match for timestamped experiment roots.
    full_dir = dirs[-1]
    workload_dir = full_dir / exp
    if workload_dir.is_dir():
        return workload_dir
    # Fallback: first child directory if the workload leaf differs unexpectedly.
    child_dirs = sorted([d for d in full_dir.iterdir() if d.is_dir()])
    return child_dirs[0] if child_dirs else None


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("Error: --end must be >= --start.")

    root = Path(args.results_root).resolve()
    window = WindowSpec(args.start, args.end)
    converged_expected = args.end if args.end > 0 else None
    series_cfg = parse_series_arg(args.series, args.mlos_tuner, args.tuxbot_tuner)

    records: List[Dict[str, Any]] = []

    for exp in args.experiments:
        workload_dir = resolve_workload_dir(root, args.results_pattern, exp)
        if workload_dir is None or not workload_dir.is_dir():
            continue

        fixed_workload_dir = None
        if args.fixed_pattern:
            fixed_workload_dir = resolve_workload_dir(root, args.fixed_pattern, exp)
        if fixed_workload_dir is None and (workload_dir / args.fixed_tuner).is_dir():
            fixed_workload_dir = workload_dir
        if fixed_workload_dir is None or not fixed_workload_dir.is_dir():
            continue

        fixed_dir = fixed_workload_dir / args.fixed_tuner
        metric_name, goal_name = detect_dir_metric_and_goal(
            tuner_dir=fixed_dir,
            converged_only=args.converged_only,
            converged_expected=converged_expected,
        )
        if metric_name is None:
            continue

        fixed_run_values = collect_window_run_values(
            tuner_dir=fixed_dir,
            metric_name=metric_name,
            window=window,
            converged_only=args.converged_only,
            converged_expected=converged_expected,
        )
        fixed_run_refs = [statistics.fmean(vals) for vals in fixed_run_values if vals]
        if not fixed_run_refs:
            continue

        for spec in series_cfg:
            summary, _, _ = summarize_tuner(
                tuner_dir=workload_dir / spec["dir"],
                window=window,
                fixed_run_refs=fixed_run_refs,
                forced_metric=metric_name,
                forced_goal=goal_name,
                converged_only=args.converged_only,
                converged_expected=converged_expected,
            )
            if summary.runs == 0:
                continue
            records.append(
                {
                    "experiment": exp,
                    "workload": workload_label(exp),
                    "metric": metric_name,
                    "goal": goal_name,
                    "tuner": spec["label"],
                    "color": spec["color"],
                    "runs": summary.runs,
                    "points": summary.points,
                    "bad_window_rate_pct": summary.mean_pct_worse,
                    "bad_window_rate_pct_std": summary.mean_pct_worse_std,
                    "p10_bad_window_rate_pct": summary.p10_pct_worse,
                    "p10_bad_window_rate_pct_std": summary.p10_pct_worse_std,
                    "variability_pct_of_fixed": summary.variability_pct_of_fixed,
                    "variability_pct_of_fixed_std": summary.variability_pct_of_fixed_std,
                }
            )

    if not records:
        raise SystemExit("Error: no records computed. Check inputs and results directories.")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.start}_{args.end}"
    base = f"preconvergence_multiworkload_mlos_tuxbot_{suffix}"
    plot_path = Path(args.plot_output).resolve() if args.plot_output else output_dir / f"{base}.png"
    csv_path = Path(args.csv_output).resolve() if args.csv_output else output_dir / f"{base}.csv"

    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "experiment",
                "workload",
                "metric",
                "goal",
                "tuner",
                "runs",
                "points",
                "bad_window_rate_pct",
                "bad_window_rate_pct_std",
                "p10_bad_window_rate_pct",
                "p10_bad_window_rate_pct_std",
                "variability_pct_of_fixed",
                "variability_pct_of_fixed_std",
            ]
        )
        for r in records:
            w.writerow(
                [
                    r["experiment"],
                    r["workload"],
                    r["metric"],
                    r["goal"],
                    r["tuner"],
                    r["runs"],
                    r["points"],
                    "" if r["bad_window_rate_pct"] is None else f"{r['bad_window_rate_pct']:.4f}",
                    "" if r["bad_window_rate_pct_std"] is None else f"{r['bad_window_rate_pct_std']:.4f}",
                    "" if r["p10_bad_window_rate_pct"] is None else f"{r['p10_bad_window_rate_pct']:.4f}",
                    "" if r["p10_bad_window_rate_pct_std"] is None else f"{r['p10_bad_window_rate_pct_std']:.4f}",
                    "" if r["variability_pct_of_fixed"] is None else f"{r['variability_pct_of_fixed']:.4f}",
                    "" if r["variability_pct_of_fixed_std"] is None else f"{r['variability_pct_of_fixed_std']:.4f}",
                ]
            )

    workloads = []
    for r in records:
        if r["workload"] not in workloads:
            workloads.append(r["workload"])

    by_wt: Dict[Tuple[str, str], Dict[str, Any]] = {(r["workload"], r["tuner"]): r for r in records}
    metrics = [
        ("bad_window_rate_pct", "bad_window_rate_pct_std", "P50 Poor-Measurement Rate (%)"),
        ("p10_bad_window_rate_pct", "p10_bad_window_rate_pct_std", "P10 Poor-Measurement Rate (%)"),
        ("variability_pct_of_fixed", "variability_pct_of_fixed_std", "Variability (% of Fixed)"),
    ]

    tuners = []
    color_by_tuner: Dict[str, str] = {}
    for spec in series_cfg:
        tuners.append(spec["label"])
        color_by_tuner[spec["label"]] = spec["color"]
    for r in records:
        if r["tuner"] not in tuners:
            tuners.append(r["tuner"])
        color_by_tuner[r["tuner"]] = r.get("color") or color_by_tuner.get(r["tuner"], DEFAULT_COLORS[len(color_by_tuner) % len(DEFAULT_COLORS)])

    FS = 7
    x = np.arange(len(workloads))
    width = min(0.80 / max(len(tuners), 1), 0.28)
    fig_width = 3.30 if len(tuners) <= 2 else min(7.2, 3.30 + 0.55 * (len(tuners) - 2))
    fig, axes = plt.subplots(3, 1, figsize=(fig_width, 2.38), sharex=True, constrained_layout=False)

    for metric_idx, (ax, (metric_key, metric_err_key, title)) in enumerate(zip(axes, metrics)):
        center_shift = (len(tuners) - 1) / 2.0
        for idx, tuner in enumerate(tuners):
            offset = (idx - center_shift) * width
            vals: List[float] = []
            raw_vals: List[Optional[float]] = []
            yerrs: List[float] = []
            for wl in workloads:
                rec = by_wt.get((wl, tuner))
                v = None if rec is None else rec.get(metric_key)
                raw_vals.append(v)
                vals.append(0.0 if v is None else float(v))
                e = None if rec is None else rec.get(metric_err_key)
                yerrs.append(0.0 if e is None else float(e))

            bar_yerr = None
            if args.error_bars:
                if metric_key in {"bad_window_rate_pct", "p10_bad_window_rate_pct"}:
                    bar_yerr = np.array(
                        [
                            [min(max(val, 0.0), err) for val, err in zip(vals, yerrs)],
                            yerrs,
                        ],
                        dtype=float,
                    )
                else:
                    bar_yerr = np.array(
                        [
                            yerrs,
                            yerrs,
                        ],
                        dtype=float,
                    )

            bars = ax.bar(
                x + offset,
                vals,
                width=width,
                label=tuner,
                color=color_by_tuner[tuner],
                edgecolor="black",
                linewidth=0.7,
            )
            if args.error_bars and bar_yerr is not None:
                err = ax.errorbar(
                    x + offset,
                    vals,
                    yerr=bar_yerr,
                    fmt="none",
                    ecolor="#333333",
                    elinewidth=0.9,
                    capsize=3,
                    capthick=0.9,
                    zorder=4,
                    clip_on=True,
                )
                _data_line, caplines, barlinecols = err.lines
                for artist in list(caplines) + list(barlinecols):
                    artist.set_clip_on(True)
                    artist.set_clip_path(ax.patch)
            for bar, raw in zip(bars, raw_vals):
                if raw is None:
                    bar.set_facecolor("#dddddd")
                    bar.set_hatch("//")

        ax.set_title(title, fontsize=FS, pad=6)
        ax.set_xticks(x)
        if metric_idx < len(metrics) - 1:
            ax.tick_params(axis="x", which="both", labelbottom=False)
        else:
            ax.set_xticklabels(workloads, rotation=0, ha="center", fontsize=FS)
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)
        ax.set_ylim(bottom=0)
        ax.tick_params(axis="y", labelsize=FS - 1)

    handles, labels = axes[0].get_legend_handles_labels()
    axes[0].legend(
        handles,
        labels,
        loc="upper right",
        ncol=2 if len(tuners) <= 4 else 3,
        frameon=False,
        fontsize=FS,
        borderaxespad=0.15,
        handletextpad=0.35,
        columnspacing=0.8,
    )
    fig.subplots_adjust(left=0.13, right=0.995, top=0.965, bottom=0.115, hspace=0.38)
    fig.savefig(plot_path, dpi=280, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)

    # Tabulated CLI summary
    print()
    hdr = f"{'Workload':<14s} {'Tuner':<24s} {'Runs':>5s} {'Bad%':>8s} {'P10%':>8s} {'Var%':>8s}"
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        bw = f"{r['bad_window_rate_pct']:.1f}" if r['bad_window_rate_pct'] is not None else "N/A"
        p10 = f"{r['p10_bad_window_rate_pct']:.1f}" if r['p10_bad_window_rate_pct'] is not None else "N/A"
        var = f"{r['variability_pct_of_fixed']:.1f}" if r['variability_pct_of_fixed'] is not None else "N/A"
        print(f"{r['workload']:<14s} {r['tuner']:<24s} {r['runs']:>5d} {bw:>8s} {p10:>8s} {var:>8s}")
    print()
    print(f"Experiments: {', '.join(args.experiments)}")
    print(f"Window: {args.start}-{args.end}")
    print(f"Included benchmarks: {workloads}")
    print(f"CSV:   {csv_path}")
    print(f"Plot:  {plot_path}")
    print("Variability formula: mean_r(std_t(metric_{r,t}) / |mean_fixed| * 100)")


if __name__ == "__main__":
    main()
