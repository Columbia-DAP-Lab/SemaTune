#!/usr/bin/env python3
"""Section 5.6: Model Backend Tradeoffs.

Keeps the control policy fixed (dual-loop, app-metrics, in-window, 30+20,
final Actor before stable) and varies only the model backend pair.

Two-panel figure identical in layout to 5.4:
  Panel A – grouped bars for tuning & stable geomean improvement vs Fixed.
  Panel B – Pareto scatter: x = total session cost (USD), y = stable geomean.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_full_performance_table import (
    detect_metric_goal_from_any_tuner,
    detect_metric_goal_from_fixed,
    history_run_completed_successfully,
    improvement_pct,
    resolve_fixed_dirs,
    resolve_tuner_dir,
    resolve_workload_dirs,
)
from cost_helpers import (
    CostSummary,
    collect_one_completed_history_cost,
    efficiency_score,
    load_pricing_map,
)
from plot_preconvergence_multiworkload import WindowSpec, iter_history_files, load_json, select_window_values


DEFAULT_METHODS = [
    ("Gemini 2.5 Flash", "llm_dual_app_metrics_final_actor"),
    ("Gemini 3 Flash", "llm_dual_app_metrics_gemini3flash31lite_final_actor"),
    ("Kimi K2", "llm_dual_app_metrics_kimi_final_actor"),
]

COLORS = {
    "Gemini 2.5 Flash": "#0072B2",
    "Gemini 3 Flash": "#2FB47C",
    "Kimi K2": "#F0B44D",
}
MARKERS = {
    "Gemini 2.5 Flash": "D",
    "Gemini 3 Flash": "s",
    "Kimi K2": "^",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot 5.6: model backend cost/performance comparison.")
    p.add_argument("--result-paths", nargs="+", required=True,
                   help="Retry result roots.")
    p.add_argument("--fallback-fixed-paths", nargs="+", required=True,
                   help="Fixed baseline roots (e.g. *_new).")
    p.add_argument("--custom-columns", default=None,
                   help="Override method spec: 'LABEL:DIRNAME,...'")
    p.add_argument("--benchmarks", nargs="+",
                   default=["tpcc_hi_p99", "silo_hi_p99", "sysbench_oltp_rw_hi_p99", "dcperf_spark_online_tput"],
                   help="Restrict to these benchmarks.")
    p.add_argument("--tuning-window", default="1-30")
    p.add_argument("--stable-window", default="31-50")
    p.add_argument("--aggregate-stat", choices=("geomean", "geomedian", "trimmed-geomean"),
                   default="geomean")
    p.add_argument("--trim-fraction", type=float, default=0.10)
    p.add_argument("--pricing-map", default=None)
    p.add_argument(
        "--cost-summary-csv",
        default="",
        help=(
            "Optional CSV with measured per-session costs keyed by backend label. "
            "When provided, provider_total_cost_usd overrides the plot x-axis cost."
        ),
    )
    p.add_argument(
        "--plot-cost-override",
        action="append",
        default=[],
        help="Override one plotted cost as 'LABEL:COST_USD'. Can be repeated.",
    )
    p.add_argument(
        "--assume-speculator-calls",
        type=int,
        default=0,
        help=(
            "For cost estimation only, scale each sampled session to this many "
            "Speculator calls while keeping the observed Actor-side usage."
        ),
    )
    p.add_argument(
        "--method-workload-override",
        action="append",
        default=[],
        help=(
            "Override one method/workload input as "
            "'LABEL:WORKLOAD:PERF_DIR[:COST_DIR]'. "
            "PERF_DIR is used for phase aggregation; COST_DIR defaults to PERF_DIR."
        ),
    )
    p.add_argument("--plot-output", required=True)
    p.add_argument("--csv-output", default="")
    p.add_argument(
        "--xlim",
        nargs=2,
        type=float,
        metavar=("XMIN", "XMAX"),
        default=None,
        help="Set scatter-panel x-axis limits explicitly.",
    )
    p.add_argument(
        "--x-tick-step",
        type=float,
        default=None,
        help="Set scatter-panel x-axis tick spacing explicitly.",
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


def _label_variants(label: str) -> List[str]:
    variants = [label]
    if label == "Gemini 2.5 Flash":
        variants.extend(["Gemini 2.5"])
    return variants


def load_cost_summary_overrides(path_str: str) -> Dict[str, float]:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.is_file():
        raise SystemExit(f"--cost-summary-csv does not exist: {path}")
    overrides: Dict[str, float] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            backend = (row.get("backend") or "").strip()
            raw_cost = row.get("provider_total_cost_usd")
            if not backend or raw_cost in (None, ""):
                continue
            try:
                overrides[backend] = float(raw_cost)
            except ValueError:
                continue
    return overrides


def parse_plot_cost_overrides(raw_overrides: Sequence[str]) -> Dict[str, float]:
    overrides: Dict[str, float] = {}
    for raw in raw_overrides:
        label, sep, cost_raw = raw.rpartition(":")
        if not sep or not label.strip() or not cost_raw.strip():
            raise SystemExit(
                "--plot-cost-override must be LABEL:COST_USD, "
                f"got: {raw!r}"
            )
        try:
            overrides[label.strip()] = float(cost_raw)
        except ValueError as exc:
            raise SystemExit(
                f"Invalid COST_USD in --plot-cost-override: {raw!r}"
            ) from exc
    return overrides


def resolve_tuner_dirs_multi(workload_dirs: Sequence[Path], dir_spec: str) -> Optional[Path]:
    for workload_dir in workload_dirs:
        for candidate in dir_spec.split("|"):
            d = resolve_tuner_dir(workload_dir, candidate.strip())
            if d is not None:
                return d
    return None


def parse_method_workload_overrides(
    raw_overrides: Sequence[str],
) -> Dict[Tuple[str, str], Tuple[Path, Path]]:
    overrides: Dict[Tuple[str, str], Tuple[Path, Path]] = {}
    for raw in raw_overrides:
        parts = [part.strip() for part in raw.split(":", 3)]
        if len(parts) not in (3, 4) or any(not part for part in parts[:3]):
            raise SystemExit(
                "--method-workload-override must be LABEL:WORKLOAD:PERF_DIR[:COST_DIR], "
                f"got: {raw!r}"
            )
        label, workload, perf_dir_raw = parts[:3]
        cost_dir_raw = parts[3] if len(parts) == 4 and parts[3] else perf_dir_raw
        perf_dir = Path(perf_dir_raw).resolve()
        cost_dir = Path(cost_dir_raw).resolve()
        if not perf_dir.is_dir():
            raise SystemExit(f"Override PERF_DIR does not exist or is not a directory: {perf_dir}")
        if not cost_dir.is_dir():
            raise SystemExit(f"Override COST_DIR does not exist or is not a directory: {cost_dir}")
        overrides[(label, workload)] = (perf_dir, cost_dir)
    return overrides


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


def factor_to_pct(factor: Optional[float]) -> Optional[float]:
    if factor is None:
        return None
    return (float(factor) - 1.0) * 100.0


def candidate_factor(default_val, candidate_val, goal):
    pct = improvement_pct(default_val, candidate_val, goal)
    if pct is None:
        return None
    return 1.0 + (pct / 100.0)


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


def aggregate_run_series(
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


def estimate_cost_from_summary(
    summary: CostSummary,
    assume_speculator_calls: int,
) -> Tuple[float, int, int, int]:
    observed_spec_calls = int(summary.speculator.calls)
    observed_actor_side_calls = int(summary.actor.calls + summary.single.calls + summary.trimming.calls)
    if assume_speculator_calls <= 0 or observed_spec_calls <= 0:
        return (
            float(summary.combined.cost_usd),
            int(summary.combined.calls),
            observed_spec_calls,
            observed_actor_side_calls,
        )

    spec_scale = float(assume_speculator_calls) / float(observed_spec_calls)
    estimated_spec_cost = float(summary.speculator.cost_usd) * spec_scale
    estimated_total_cost = (
        float(summary.actor.cost_usd)
        + float(summary.single.cost_usd)
        + float(summary.trimming.cost_usd)
        + estimated_spec_cost
    )
    estimated_action_count = observed_actor_side_calls + int(assume_speculator_calls)
    return estimated_total_cost, estimated_action_count, observed_spec_calls, observed_actor_side_calls


def main() -> None:
    args = parse_args()
    tuning_win = parse_window(args.tuning_window)
    stable_win = parse_window(args.stable_window)
    methods = parse_methods(args.custom_columns)
    pricing = load_pricing_map(Path(args.pricing_map) if args.pricing_map else None)
    overrides = parse_method_workload_overrides(args.method_workload_override)
    cost_overrides = load_cost_summary_overrides(args.cost_summary_csv)
    plot_cost_overrides = parse_plot_cost_overrides(args.plot_cost_override)

    workload_dirs = resolve_workload_dirs(args.result_paths)
    fixed_map = resolve_fixed_dirs(args.fallback_fixed_paths)

    allowed_bm = set(args.benchmarks) if args.benchmarks else None
    workload_map: Dict[str, List[Path]] = {}
    for wd in workload_dirs:
        if allowed_bm and wd.name not in allowed_bm:
            continue
        workload_map.setdefault(wd.name, []).append(wd)
    if overrides:
        for _label, workload in overrides.keys():
            if allowed_bm and workload not in allowed_bm:
                continue
            workload_map.setdefault(workload, [])
    workload_names = sorted(workload_map.keys())

    results: Dict[str, Dict[str, object]] = {}

    for label, dir_spec in methods:
        tuning_workload_series: List[List[float]] = []
        stable_workload_series: List[List[float]] = []
        total_cost_usd = 0.0
        action_count = 0
        cost_sample_runs = 0
        observed_speculator_calls = 0
        observed_actor_side_calls = 0
        included_benchmarks = []

        for wname in workload_names:
            wdirs = workload_map.get(wname, [])
            fdir = fixed_map.get(wname)
            if fdir is None:
                continue

            override = overrides.get((label, wname))
            if override is not None:
                tuner_dir, cost_dir = override
            else:
                tuner_dir = resolve_tuner_dirs_multi(wdirs, dir_spec)
                cost_dir = tuner_dir
            if tuner_dir is None or cost_dir is None:
                continue

            metric_name, goal = detect_metric_goal_from_fixed(fdir, wname)
            if metric_name is None:
                for wdir in wdirs:
                    metric_name, goal = detect_metric_goal_from_any_tuner(wdir, wname)
                    if metric_name is not None:
                        break
            if metric_name is None:
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
            if not tuning_series or not stable_series:
                continue

            tuning_workload_series.append(tuning_series)
            stable_workload_series.append(stable_series)
            included_benchmarks.append(wname)

            wk_cost = collect_one_completed_history_cost(
                cost_dir,
                start=tuning_win[0],
                end=tuning_win[1],
                pricing=pricing,
            )
            estimated_cost, estimated_actions, wk_spec_calls, wk_actor_side_calls = estimate_cost_from_summary(
                wk_cost,
                args.assume_speculator_calls,
            )
            total_cost_usd += estimated_cost
            action_count += estimated_actions
            observed_speculator_calls += wk_spec_calls
            observed_actor_side_calls += wk_actor_side_calls
            if wk_cost.combined.calls > 0 or wk_cost.iterations:
                cost_sample_runs += 1

        tuning_aggregate_series = aggregate_run_series(
            tuning_workload_series,
            args.aggregate_stat,
            args.trim_fraction,
        )
        stable_aggregate_series = aggregate_run_series(
            stable_workload_series,
            args.aggregate_stat,
            args.trim_fraction,
        )
        tuning_pct, tuning_err = summarize_series(tuning_aggregate_series)
        stable_pct, stable_err = summarize_series(stable_aggregate_series)
        stable_factor_for_efficiency = pct_to_factor(stable_pct)

        run_count = len(stable_aggregate_series) if stable_aggregate_series else len(tuning_aggregate_series)
        plotted_total_cost_usd = total_cost_usd
        for candidate_label in _label_variants(label):
            if candidate_label in plot_cost_overrides:
                plotted_total_cost_usd = plot_cost_overrides[candidate_label]
                break
            if candidate_label in cost_overrides:
                plotted_total_cost_usd = cost_overrides[candidate_label]
                break

        results[label] = {
            "tuning_geomean": pct_to_factor(tuning_pct),
            "tuning_pct": tuning_pct,
            "tuning_pct_err": None if tuning_err is None else (tuning_err, tuning_err),
            "stable_geomean": stable_factor_for_efficiency,
            "stable_pct": stable_pct,
            "stable_pct_err": None if stable_err is None else (stable_err, stable_err),
            "total_cost_usd": plotted_total_cost_usd,
            "cost_per_run_usd": plotted_total_cost_usd,
            "avg_cost_per_action": plotted_total_cost_usd / max(1, action_count),
            "action_count": action_count,
            "cost_sample_runs": cost_sample_runs,
            "observed_speculator_calls": observed_speculator_calls,
            "observed_actor_side_calls": observed_actor_side_calls,
            "assumed_speculator_calls_per_session": args.assume_speculator_calls if args.assume_speculator_calls > 0 else "",
            "n_benchmarks": len(included_benchmarks),
            "benchmarks": ",".join(sorted(included_benchmarks)),
            "efficiency": efficiency_score(stable_factor_for_efficiency, plotted_total_cost_usd),
            "run_count": run_count,
            "cost_mode": (
                f"assume_speculator_calls={args.assume_speculator_calls}"
                if args.assume_speculator_calls > 0
                else "observed"
            ),
        }

    # -- Print CLI summary --
    print(f"Benchmarks requested: {args.benchmarks}")
    print()
    hdr = f"{'Method':<30s} {'Tuning %':>10s} {'Stable %':>10s} {'Cost ($)':>10s} {'$/Run':>10s} {'$/Action':>10s} {'#Agg':>6s} {'#Cost':>6s} {'#Actions':>8s} {'η':>10s} {'#BM':>4s}"
    print(hdr)
    print("-" * len(hdr))
    for label, data in results.items():
        tp = f"{data['tuning_pct']:+.1f}" if data['tuning_pct'] is not None else "N/A"
        sp = f"{data['stable_pct']:+.1f}" if data['stable_pct'] is not None else "N/A"
        cost = f"${data['total_cost_usd']:.4f}"
        per_run = f"${data['cost_per_run_usd']:.6f}"
        avg = f"${data['avg_cost_per_action']:.6f}"
        runs = str(data['run_count'])
        cost_runs = str(data['cost_sample_runs'])
        actions = str(data['action_count'])
        eff = f"{data['efficiency']:.2f}" if data['efficiency'] is not None else "N/A"
        print(f"{label:<30s} {tp:>10s} {sp:>10s} {cost:>10s} {per_run:>10s} {avg:>10s} {runs:>6s} {cost_runs:>6s} {actions:>8s} {eff:>10s} {data['n_benchmarks']:>4d}")
    print()

    # -- Plot --
    labels = list(results.keys())
    n = len(labels)
    tuning_pcts = [results[l]["tuning_pct"] or 0.0 for l in labels]
    stable_pcts = [results[l]["stable_pct"] or 0.0 for l in labels]
    costs_vals = [results[l]["total_cost_usd"] for l in labels]
    colors_list = [COLORS.get(l, "#999999") for l in labels]

    FS = 29
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(12, 4.0),
        sharey=True,
        gridspec_kw={"width_ratios": [3, 2]},
    )

    x = np.arange(n, dtype=float)
    width = 0.35
    tuning_yerr = None
    stable_yerr = None
    if args.error_bars:
        tuning_yerr = np.array(
            [
                [0.0 if results[l]["tuning_pct_err"] is None else float(results[l]["tuning_pct_err"][0]) for l in labels],
                [0.0 if results[l]["tuning_pct_err"] is None else float(results[l]["tuning_pct_err"][1]) for l in labels],
            ],
            dtype=float,
        )
        stable_yerr = np.array(
            [
                [0.0 if results[l]["stable_pct_err"] is None else float(results[l]["stable_pct_err"][0]) for l in labels],
                [0.0 if results[l]["stable_pct_err"] is None else float(results[l]["stable_pct_err"][1]) for l in labels],
            ],
            dtype=float,
        )
    ax1.bar(x - width/2, tuning_pcts, width, label="Tuning", color=colors_list,
            edgecolor="black", linewidth=0.8, alpha=0.7, hatch="//", yerr=tuning_yerr,
            capsize=3 if args.error_bars else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": "#333333"} if args.error_bars else None)
    ax1.bar(x + width/2, stable_pcts, width, label="Stable", color=colors_list,
            edgecolor="black", linewidth=0.8, alpha=1.0, yerr=stable_yerr,
            capsize=3 if args.error_bars else 0,
            error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": "#333333"} if args.error_bars else None)
    ax1.axhline(0, color="#666", linestyle="--", linewidth=0.8)
    ax1.set_xticks(x)
    ax1.set_xticklabels([""] * n)
    ax1.tick_params(axis="x", length=0)
    ax1.set_ylabel("Improv. (%)", fontsize=FS)
    ax1.tick_params(axis="y", labelsize=FS)
    ax1.grid(axis="y", alpha=0.3, linewidth=0.6)
    ax1.set_axisbelow(True)
    ax1.legend(
        handles=[
            Patch(facecolor="white", edgecolor="#444444", hatch="//", label="Tuning"),
            Patch(facecolor="white", edgecolor="#444444", label="Stable"),
        ],
        frameon=False,
        fontsize=FS - 5,
        ncol=2,
        loc="upper center",
        bbox_to_anchor=(0.50, -0.005),
        handlelength=1.3,
        handletextpad=0.5,
        columnspacing=1.2,
        borderaxespad=0.1,
    )
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    annotation_style = {
        "Gemini 2.5 Flash": {"xytext": (0, 10), "ha": "center", "va": "bottom"},
        "Gemini 3 Flash": {"xytext": (0, 10), "ha": "center", "va": "bottom"},
        "Kimi K2": {"xytext": (15, -14), "ha": "center", "va": "top"},
    }
    for i, l in enumerate(labels):
        sp = stable_pcts[i]
        c = costs_vals[i]
        ax2.scatter(c, sp, s=360, c=colors_list[i], marker=MARKERS.get(l, "o"),
                    edgecolors="black", linewidths=0.8, zorder=5, label=l)
        style = annotation_style.get(l, {"xytext": (8, 8), "ha": "left", "va": "bottom"})
        ax2.annotate(
            l,
            (c, sp),
            textcoords="offset points",
            xytext=style["xytext"],
            fontsize=FS - 6,
            ha=style["ha"],
            va=style["va"],
        )
    ax2.axhline(0, color="#666", linestyle="--", linewidth=0.8)
    ax2.set_xlabel("")
    if args.xlim is not None:
        xmin, xmax = args.xlim
        if xmax <= xmin:
            raise SystemExit("--xlim requires XMAX > XMIN")
        if args.x_tick_step is not None:
            if args.x_tick_step <= 0:
                raise SystemExit("--x-tick-step must be > 0")
            tick_count = int(round((xmax - xmin) / args.x_tick_step))
            xticks = [xmin + i * args.x_tick_step for i in range(tick_count + 1)]
            if not math.isclose(xticks[-1], xmax):
                xticks.append(xmax)
        else:
            xticks = list(np.linspace(xmin, xmax, 4))
        xlim_upper = xmax
    else:
        max_cost = max(costs_vals) if costs_vals else 0.0
        if max_cost <= 0.12:
            xticks = [0.0, 0.1]
        elif max_cost <= 0.2:
            xticks = [0.0, 0.1, 0.2]
        elif max_cost <= 0.4:
            xticks = [0.0, 0.2, 0.4]
        elif max_cost <= 0.6:
            xticks = [0.0, 0.25, 0.5]
        elif max_cost <= 1.2:
            xticks = [0.0, 0.5, 1.0]
        else:
            xticks = [0.0, 1.0, 2.0]
        xlim_upper = xticks[-1] + 0.05
    ax2.set_xticks(xticks)
    ax2.set_xticklabels([f"${tick:g}" for tick in xticks], fontsize=FS)
    ax2.set_xlim(xticks[0], xlim_upper)
    ax2.set_ylabel("")
    ax2.tick_params(axis="x", labelsize=FS)
    ax2.tick_params(axis="y", labelleft=False, left=True, length=4, width=0.8)
    ax2.grid(axis="both", alpha=0.3, linewidth=0.6)
    ax2.set_axisbelow(True)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    fig.tight_layout(pad=0.5)
    fig.subplots_adjust(wspace=0.16)

    output_path = Path(args.plot_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print(f"Saved plot: {output_path}")

    if args.csv_output:
        csv_path = Path(args.csv_output)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "method", "tuning_geomean_pct", "stable_geomean_pct",
                "tuning_geomean_pct_err_low", "tuning_geomean_pct_err_high",
                "stable_geomean_pct_err_low", "stable_geomean_pct_err_high",
                "total_cost_usd", "cost_per_run_usd", "avg_cost_per_action", "action_count", "cost_sample_runs", "run_count",
                "observed_speculator_calls", "observed_actor_side_calls", "assumed_speculator_calls_per_session", "cost_mode",
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
                    "total_cost_usd": data["total_cost_usd"],
                    "cost_per_run_usd": data["cost_per_run_usd"],
                    "avg_cost_per_action": data["avg_cost_per_action"],
                    "action_count": data["action_count"],
                    "cost_sample_runs": data["cost_sample_runs"],
                    "run_count": data["run_count"],
                    "observed_speculator_calls": data["observed_speculator_calls"],
                    "observed_actor_side_calls": data["observed_actor_side_calls"],
                    "assumed_speculator_calls_per_session": data["assumed_speculator_calls_per_session"],
                    "cost_mode": data["cost_mode"],
                    "efficiency": data["efficiency"],
                    "n_benchmarks": data["n_benchmarks"],
                    "benchmarks": data["benchmarks"],
                })
        print(f"Saved CSV: {csv_path}")


if __name__ == "__main__":
    main()
