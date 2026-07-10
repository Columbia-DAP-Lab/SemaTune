#!/usr/bin/env python3
"""
Pre-convergence stability plot/table for Fixed vs MLOS vs Tuxbot.

This summarizes a selected window range (default 0-60) to support claims like:
- "does not mess up before convergence"
- "converges sub-optimally but remains safer than alternatives"

Metrics per tuner (aggregated across runs):
- mean metric in window
- std of run means
- mean within-run window std
- percent windows worse than fixed reference
- p10 and p90 of bad-window rate across runs
- worst degradation vs fixed reference
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


@dataclass
class WindowSpec:
    start: int
    end: int


@dataclass
class RunStats:
    values: List[float]
    run_mean: float
    run_std: float
    pct_worse_than_fixed: Optional[float]
    worst_degradation_pct: Optional[float]


@dataclass
class TunerSummary:
    label: str
    runs: int
    points: int
    mean_metric: Optional[float]
    median_run_mean: Optional[float]
    std_run_mean: Optional[float]
    mean_within_run_std: Optional[float]
    mean_pct_worse: Optional[float]
    p10_pct_worse: Optional[float]
    p90_pct_worse: Optional[float]
    mean_worst_deg_pct: Optional[float]
    vs_fixed_mean_pct: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot pre-convergence stability evidence for Fixed vs MLOS vs Tuxbot."
    )
    parser.add_argument("experiment", help="Experiment name (e.g., tpcc_hi_p99, silo_hi_p99, sysbench_cpu_tput).")
    parser.add_argument(
        "--full-results-dir",
        default=None,
        help="Full results root (default: ./all_results/results_config_full_param_<experiment>).",
    )
    parser.add_argument("--fixed-tuner", default="fixed", help="Fixed tuner directory name.")
    parser.add_argument("--mlos-tuner", default="mlos", help="MLOS tuner directory name.")
    parser.add_argument(
        "--tuxbot-tuner",
        default="llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3",
        help="Tuxbot tuner directory name.",
    )
    parser.add_argument("--start", type=int, default=0, help="Window start iteration (inclusive). Default: 0")
    parser.add_argument("--end", type=int, default=60, help="Window end iteration (inclusive). Default: 60")
    parser.add_argument(
        "--metric",
        default=None,
        help="Optional metric override (default: from config.optimization_metric).",
    )
    parser.add_argument(
        "--goal",
        choices=("minimize", "maximize"),
        default=None,
        help="Optional goal override (default: from config.optimization_goal).",
    )
    parser.add_argument(
        "--converged-only",
        action="store_true",
        help="Use only converged runs (history reaches expected iterations).",
    )
    parser.add_argument("--output-dir", default="plots", help="Output directory (default: plots).")
    parser.add_argument("--plot-output", default=None, help="Optional explicit PNG path.")
    parser.add_argument("--table-output", default=None, help="Optional explicit markdown table path.")
    parser.add_argument("--csv-output", default=None, help="Optional explicit CSV path.")
    return parser.parse_args()


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


def fmt_float(x: Optional[float], digits: int = 3) -> str:
    return "NA" if x is None else f"{x:.{digits}f}"


def fmt_pct(x: Optional[float]) -> str:
    return "NA" if x is None else f"{x:+.2f}%"


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
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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


def detect_metric_and_goal(
    history_data: Dict[str, Any], forced_metric: Optional[str], forced_goal: Optional[str]
) -> Tuple[Optional[str], str]:
    config = history_data.get("config", {}) or {}
    metric = forced_metric or config.get("optimization_metric")
    metric_name = str(metric).strip() if metric else None
    goal = canonical_goal(forced_goal or config.get("optimization_goal"))
    return metric_name, goal


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


def iter_history_files(tuner_dir: Path) -> Iterable[Path]:
    for pattern in HISTORY_PATTERNS:
        for fp in sorted(tuner_dir.glob(pattern)):
            if fp.is_file():
                yield fp


def select_window_values(
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


def worst_degradation_pct(goal: str, fixed_ref: Optional[float], values: Sequence[float]) -> Optional[float]:
    if fixed_ref is None or fixed_ref == 0 or not values:
        return None
    if goal == "maximize":
        worst = min(values)
        return ((fixed_ref - worst) / abs(fixed_ref)) * 100.0
    worst = max(values)
    return ((worst - fixed_ref) / abs(fixed_ref)) * 100.0


def pct_worse(goal: str, fixed_ref: Optional[float], values: Sequence[float]) -> Optional[float]:
    if fixed_ref is None or not values:
        return None
    if goal == "maximize":
        bad = sum(1 for v in values if v < fixed_ref)
    else:
        bad = sum(1 for v in values if v > fixed_ref)
    return (bad / len(values)) * 100.0


def summarize_tuner(
    label: str,
    tuner_dir: Path,
    window: WindowSpec,
    fixed_ref: Optional[float],
    forced_metric: Optional[str],
    forced_goal: Optional[str],
    converged_only: bool,
    converged_expected: Optional[int],
) -> Tuple[TunerSummary, Optional[str], Optional[str], int, int]:
    run_stats: List[RunStats] = []
    files_seen = 0
    files_nonconverged = 0
    metric_name: Optional[str] = None
    goal_name: Optional[str] = None

    if not tuner_dir.is_dir():
        return (
            TunerSummary(label, 0, 0, None, None, None, None, None, None, None, None, None),
            metric_name,
            goal_name,
            files_seen,
            files_nonconverged,
        )

    for fp in iter_history_files(tuner_dir):
        files_seen += 1
        data = load_json(fp)
        if data is None:
            continue
        if converged_only and not is_converged(data, fallback_expected=converged_expected):
            files_nonconverged += 1
            continue
        m_name, g_name = detect_metric_and_goal(data, forced_metric, forced_goal)
        metric_name = metric_name or m_name
        goal_name = goal_name or g_name
        vals = select_window_values(data, m_name, window)
        if not vals:
            continue
        run_stats.append(
            RunStats(
                values=list(vals),
                run_mean=statistics.fmean(vals),
                run_std=statistics.stdev(vals) if len(vals) > 1 else 0.0,
                pct_worse_than_fixed=pct_worse(g_name, fixed_ref, vals),
                worst_degradation_pct=worst_degradation_pct(g_name, fixed_ref, vals),
            )
        )

    if not run_stats:
        return (
            TunerSummary(label, 0, 0, None, None, None, None, None, None, None, None, None),
            metric_name,
            goal_name,
            files_seen,
            files_nonconverged,
        )

    run_means = [r.run_mean for r in run_stats]
    run_stds = [r.run_std for r in run_stats]
    pct_bad = [r.pct_worse_than_fixed for r in run_stats if r.pct_worse_than_fixed is not None]
    worst_deg = [r.worst_degradation_pct for r in run_stats if r.worst_degradation_pct is not None]
    points = sum(len(r.values) for r in run_stats)
    goal_final = goal_name or "minimize"

    summary = TunerSummary(
        label=label,
        runs=len(run_stats),
        points=points,
        mean_metric=statistics.fmean(run_means),
        median_run_mean=statistics.median(run_means),
        std_run_mean=statistics.stdev(run_means) if len(run_means) > 1 else 0.0,
        mean_within_run_std=statistics.fmean(run_stds),
        mean_pct_worse=statistics.fmean(pct_bad) if pct_bad else None,
        p10_pct_worse=percentile(pct_bad, 0.10) if pct_bad else None,
        p90_pct_worse=percentile(pct_bad, 0.90) if pct_bad else None,
        mean_worst_deg_pct=statistics.fmean(worst_deg) if worst_deg else None,
        vs_fixed_mean_pct=improvement_pct(fixed_ref, statistics.fmean(run_means), goal_final),
    )
    return summary, metric_name, goal_name, files_seen, files_nonconverged


def draw_metric_subplot(
    ax: Any,
    tuners: Sequence[TunerSummary],
    value_getter: Any,
    ylabel: str,
    title: str,
) -> None:
    labels = [t.label for t in tuners]
    vals = [value_getter(t) for t in tuners]
    x = np.arange(len(labels), dtype=float)
    colors = ["#7F8C8D", "#16A085", "#E74C3C"]
    draw_vals = [0.0 if v is None else float(v) for v in vals]
    bars = ax.bar(x, draw_vals, color=colors[: len(labels)], edgecolor="black", linewidth=0.8)
    for i, (b, raw) in enumerate(zip(bars, vals)):
        text = "NA" if raw is None else f"{raw:.2f}"
        ax.text(
            b.get_x() + b.get_width() / 2.0,
            b.get_height(),
            text,
            ha="center",
            va="bottom",
            fontsize=8,
            rotation=0,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("Error: --end must be >= --start.")

    repo_root = Path.cwd().resolve()
    full_results_dir = (
        Path(args.full_results_dir).resolve()
        if args.full_results_dir
        else (repo_root / "all_results" / f"results_config_full_param_{args.experiment}").resolve()
    )
    if not full_results_dir.is_dir():
        raise SystemExit(f"Error: full results dir not found: {full_results_dir}")

    workload_dir = full_results_dir / args.experiment
    if not workload_dir.is_dir():
        raise SystemExit(f"Error: workload dir not found: {workload_dir}")

    fixed_dir = workload_dir / args.fixed_tuner
    mlos_dir = workload_dir / args.mlos_tuner
    tux_dir = workload_dir / args.tuxbot_tuner

    window = WindowSpec(start=args.start, end=args.end)
    converged_expected = args.end if args.end > 0 else None

    # First pass fixed to get reference line.
    fixed_summary, metric_name, goal_name, fixed_seen, fixed_nonconv = summarize_tuner(
        label="Fixed",
        tuner_dir=fixed_dir,
        window=window,
        fixed_ref=None,
        forced_metric=args.metric,
        forced_goal=args.goal,
        converged_only=args.converged_only,
        converged_expected=converged_expected,
    )
    metric_name = args.metric or metric_name or "objective"
    goal_name = canonical_goal(args.goal or goal_name)
    fixed_ref = fixed_summary.mean_metric

    # Recompute fixed with fixed_ref for derived metrics.
    fixed_summary, _, _, _, _ = summarize_tuner(
        label="Fixed",
        tuner_dir=fixed_dir,
        window=window,
        fixed_ref=fixed_ref,
        forced_metric=metric_name,
        forced_goal=goal_name,
        converged_only=args.converged_only,
        converged_expected=converged_expected,
    )
    mlos_summary, _, _, mlos_seen, mlos_nonconv = summarize_tuner(
        label="MLOS",
        tuner_dir=mlos_dir,
        window=window,
        fixed_ref=fixed_ref,
        forced_metric=metric_name,
        forced_goal=goal_name,
        converged_only=args.converged_only,
        converged_expected=converged_expected,
    )
    tux_summary, _, _, tux_seen, tux_nonconv = summarize_tuner(
        label="Tuxbot",
        tuner_dir=tux_dir,
        window=window,
        fixed_ref=fixed_ref,
        forced_metric=metric_name,
        forced_goal=goal_name,
        converged_only=args.converged_only,
        converged_expected=converged_expected,
    )

    summaries = [fixed_summary, mlos_summary, tux_summary]

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.start}_{args.end}"
    base = f"{args.experiment}_preconvergence_stability_{suffix}"
    table_path = Path(args.table_output).resolve() if args.table_output else (output_dir / f"{base}.md")
    csv_path = Path(args.csv_output).resolve() if args.csv_output else (output_dir / f"{base}.csv")
    plot_path = Path(args.plot_output).resolve() if args.plot_output else (output_dir / f"{base}.png")

    headers = [
        "Tuner",
        "Runs",
        "Points",
        f"Mean {metric_name} ({args.start}-{args.end})",
        "Median run mean",
        "Std(run mean)",
        "Mean within-run std",
        "% windows worse than Fixed",
        "P10 bad-window rate",
        "P90 bad-window rate",
        "Worst degradation vs Fixed (%)",
        "vs Fixed (mean)",
    ]
    rows = []
    for s in summaries:
        rows.append(
            [
                s.label,
                str(s.runs),
                str(s.points),
                fmt_float(s.mean_metric),
                fmt_float(s.median_run_mean),
                fmt_float(s.std_run_mean),
                fmt_float(s.mean_within_run_std),
                fmt_pct(s.mean_pct_worse),
                fmt_pct(s.p10_pct_worse),
                fmt_pct(s.p90_pct_worse),
                fmt_pct(s.mean_worst_deg_pct),
                fmt_pct(s.vs_fixed_mean_pct),
            ]
        )

    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)

    md_lines = [
        f"# Pre-Convergence Stability ({args.experiment})",
        "",
        f"- Window: `{args.start}-{args.end}` (inclusive)",
        f"- Metric: `{metric_name}`",
        f"- Goal: `{goal_name}`",
        f"- Converged-only: `{'on' if args.converged_only else 'off'}`",
        f"- Fixed reference: `{fmt_float(fixed_ref)}`",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        md_lines.append("| " + " | ".join(row) + " |")
    md_lines.append("")
    table_path.write_text("\n".join(md_lines))

    fig, axes = plt.subplots(1, 5, figsize=(22.5, 4.8))
    draw_metric_subplot(
        axes[0],
        summaries,
        value_getter=lambda s: s.mean_pct_worse,
        ylabel="% windows worse than fixed",
        title="Bad-Window Rate (Lower Better)",
    )
    draw_metric_subplot(
        axes[1],
        summaries,
        value_getter=lambda s: s.p10_pct_worse,
        ylabel="P10 bad-window rate (%)",
        title="P10 Bad-Window Rate",
    )
    draw_metric_subplot(
        axes[2],
        summaries,
        value_getter=lambda s: s.p90_pct_worse,
        ylabel="P90 bad-window rate (%)",
        title="P90 Bad-Window Rate",
    )
    draw_metric_subplot(
        axes[3],
        summaries,
        value_getter=lambda s: s.mean_worst_deg_pct,
        ylabel="Worst degradation vs fixed (%)",
        title="Worst Excursion (Lower Better)",
    )
    draw_metric_subplot(
        axes[4],
        summaries,
        value_getter=lambda s: s.mean_within_run_std,
        ylabel=f"Std of {metric_name} within run",
        title="Within-Run Variability (Lower Better)",
    )
    fig.suptitle(f"{args.experiment}: pre-convergence stability ({args.start}-{args.end})", fontsize=12.5)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(plot_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"Experiment: {args.experiment}")
    print(f"Window: {args.start}-{args.end}")
    print(f"Metric: {metric_name}")
    print(f"Goal: {goal_name}")
    print(f"Converged-only: {'on' if args.converged_only else 'off'}")
    print(f"Fixed files seen/nonconverged: {fixed_seen}/{fixed_nonconv}")
    print(f"MLOS files seen/nonconverged: {mlos_seen}/{mlos_nonconv}")
    print(f"Tuxbot files seen/nonconverged: {tux_seen}/{tux_nonconv}")
    print(f"Fixed reference: {fmt_float(fixed_ref)}")
    print(f"Table: {table_path}")
    print(f"CSV:   {csv_path}")
    print(f"Plot:  {plot_path}")


if __name__ == "__main__":
    main()
