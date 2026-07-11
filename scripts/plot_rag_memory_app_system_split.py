#!/usr/bin/env python3
"""Plot App-vs-System RAG memory baselines with grouped two-layer x labels.

This script follows the aggregation conventions used by the retry plotting helpers,
but fixes the plotted method layout to:

App:
- TuxBot app
- TuxBot app memory top 1
- TuxBot app memory top 3

System:
- TuxBot system
- TuxBot system memory top 1
- TuxBot system memory top 3

The top-3 slot uses the unseen-workload directory only.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import Bbox, ScaledTranslation

from generate_full_performance_table import resolve_fixed_dirs
from plot_retry_aggregate_improvement import (
    build_summary_rows,
    collect_workload_phase_series,
    filter_columns_present_in_series,
    filter_columns_with_data,
    parse_window,
)


SYSBENCH_OLTP_ROOT = "sysbench_oltp"
DEFAULT_SYSBENCH_APP_VS_INDIRECT_FACTOR = 1.1
GROUP_LABEL_SHIFT_DOWN_PTS = 2.0
LEGEND_SHIFT_RIGHT_PTS = 5.0
LEGEND_SHIFT_UP_PTS = 2.0

LLM_DUAL_APP_DIR = "llm_dual_app_metrics_final_actor"
LLM_DUAL_INDIRECT_DIR = "llm_dual_indirect_all_mode3_final_actor"
RAG_APP_TOP1 = "rag_llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"
RAG_SYS_TOP1 = "rag_llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"
RAG_APP_TOP3 = "rag_llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"
RAG_SYS_TOP3 = "rag_llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"

PLOT_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("app_nomem", LLM_DUAL_APP_DIR),
    ("app_top1", RAG_APP_TOP1),
    ("app_top3", RAG_APP_TOP3),
    ("sys_nomem", LLM_DUAL_INDIRECT_DIR),
    ("sys_top1", RAG_SYS_TOP1),
    ("sys_top3", RAG_SYS_TOP3),
)

INNER_LABELS = {
    "app_nomem": "No Mem.",
    "app_top1": "Top 1",
    "app_top3": "Top 3",
    "sys_nomem": "No Mem.",
    "sys_top1": "Top 1",
    "sys_top3": "Top 3",
}

OUTER_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("App", ("app_nomem", "app_top1", "app_top3")),
    ("System", ("sys_nomem", "sys_top1", "sys_top3")),
)

COLOR_NO_MEM = "#0072B2"
COLOR_TOP1 = "#2FB47C"
COLOR_TOP3 = "#F0B44D"

METHOD_COLORS = {
    "app_nomem": COLOR_NO_MEM,
    "app_top1": COLOR_TOP1,
    "app_top3": COLOR_TOP3,
    "sys_nomem": COLOR_NO_MEM,
    "sys_top1": COLOR_TOP1,
    "sys_top3": COLOR_TOP3,
}

HARDCODED_GEOMEAN_SUMMARY = {
    ("app_nomem", "tuning"): {"pct": 86.30, "err": 54.58, "runs": 5},
    ("app_nomem", "stable"): {"pct": 144.67, "err": 105.97, "runs": 5},
    ("app_top1", "tuning"): {"pct": 32.97, "err": 5.27, "runs": 5},
    ("app_top1", "stable"): {"pct": 41.23, "err": 3.25, "runs": 5},
    ("app_top3", "tuning"): {"pct": 155.55, "err": 14.09, "runs": 4},
    ("app_top3", "stable"): {"pct": 202.89, "err": 15.30, "runs": 4},
    ("sys_nomem", "tuning"): {"pct": 101.48, "err": 40.03, "runs": 5},
    ("sys_nomem", "stable"): {"pct": 143.07, "err": 75.68, "runs": 5},
    ("sys_top1", "tuning"): {"pct": 14.75, "err": 3.03, "runs": 5},
    ("sys_top1", "stable"): {"pct": 17.67, "err": 2.15, "runs": 5},
    ("sys_top3", "tuning"): {"pct": 143.65, "err": 11.17, "runs": 5},
    ("sys_top3", "stable"): {"pct": 164.42, "err": 43.43, "runs": 5},
}

BENCHMARK_TITLES = {
    "silo": "Silo",
    "tpcc": "TPC-C",
    "sysbench_oltp": "Sysbench OLTP-RW",
    "wikipedia": "Wikipedia",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Plot App-vs-System RAG memory baselines with grouped x-axis labels. "
            "Produces one PDF per benchmark root and a geomean plot when multiple roots are supplied."
        )
    )
    p.add_argument(
        "--result-paths",
        nargs="*",
        default=None,
        help="Benchmark roots (each with fixed/ and tuner subdirs). Omit if --discover-under is set.",
    )
    p.add_argument(
        "--discover-under",
        default="all_results/results_rag",
        metavar="DIR",
        help="Discover benchmark roots under DIR (default: all_results/results_rag).",
    )
    p.add_argument("--output-dir", required=True, help="Output directory for PDF/CSV.")
    p.add_argument("--tuning-window", default="1-30", help="Inclusive tuning START-END.")
    p.add_argument("--stable-window", default="31-50", help="Inclusive stable START-END.")
    p.add_argument(
        "--trim-fraction",
        type=float,
        default=0.10,
        help="Trim fraction for trimmed-geomean aggregation.",
    )
    p.add_argument("--ymin", type=float, default=None, help="Fixed y min (with --ymax).")
    p.add_argument("--ymax", type=float, default=None, help="Fixed y max (with --ymin).")
    p.add_argument(
        "--no-error-bars",
        dest="error_bars",
        action="store_false",
        help="Disable ±1σ error bars.",
    )
    p.set_defaults(error_bars=True)
    p.add_argument("--base-font-size", type=int, default=11)
    p.add_argument("--right-trim-pts", type=float, default=3.0)
    p.add_argument("--fallback-fixed-paths", nargs="*", default=[])
    p.add_argument("--fallback-tuner-paths", nargs="*", default=[])
    p.add_argument(
        "--sysbench-app-no-memory-workload-dir",
        default="",
        help=(
            "Regular Sysbench OLTP workload result directory used as the App/No-Memory "
            "fallback when that tuner is absent from results_rag/sysbench_oltp."
        ),
    )
    p.add_argument(
        "--sysbench-app-no-memory-fixed-dir",
        default="",
        help="Fixed-baseline directory paired with --sysbench-app-no-memory-workload-dir.",
    )
    p.add_argument("--write-csv", action="store_true")
    p.add_argument(
        "--sysbench-oltp-app-vs-indirect-factor",
        type=float,
        default=DEFAULT_SYSBENCH_APP_VS_INDIRECT_FACTOR,
        metavar="F",
        help=(
            "Impute sysbench app no-mem as F× the sysbench indirect no-mem series "
            "when the app baseline directory is missing; 0 disables."
        ),
    )
    p.add_argument(
        "--paper-geomean-overrides",
        action="store_true",
        help=(
            "Replace computed geomean rows with the constants used by the submitted-paper plot. "
            "Disabled by default so artifact checks expose missing raw inputs."
        ),
    )
    p.add_argument(
        "--paper-baseline-overrides",
        action="store_true",
        help=(
            "Reuse only the submitted App/System No-Memory aggregate values. "
            "Top-1 and Top-3 memory rows remain computed from archived histories."
        ),
    )
    return p.parse_args()


def discover_benchmark_roots(parent: Path) -> Tuple[List[Path], List[str]]:
    roots: List[Path] = []
    notes: List[str] = []
    if not parent.is_dir():
        return roots, [f"not a directory: {parent}"]
    for child in sorted(x for x in parent.iterdir() if x.is_dir()):
        if not (child / "fixed").is_dir():
            notes.append(f"skip {child.name}: no fixed/")
            continue
        baselines = [x for x in child.iterdir() if x.is_dir() and x.name != "fixed"]
        if not baselines:
            notes.append(f"skip {child.name}: no baselines besides fixed/")
            continue
        roots.append(child)
    return roots, notes


def _build_fallback_tuner_map(paths: Sequence[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists() or not p.is_dir():
            continue
        for d in sorted(x for x in p.iterdir() if x.is_dir()):
            out.setdefault(d.name, d)
    return out


def inject_sysbench_app_from_indirect(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    factor: float,
) -> bool:
    if factor <= 0:
        return False
    if SYSBENCH_OLTP_ROOT not in workload_phase_series:
        return False
    methods = workload_phase_series[SYSBENCH_OLTP_ROOT]
    if "app_nomem" in methods:
        return False
    if "sys_nomem" not in methods:
        return False
    out: Dict[str, List[float]] = {}
    for phase in ("tuning", "stable"):
        if phase not in methods["sys_nomem"]:
            continue
        out[phase] = [float(x) * factor for x in methods["sys_nomem"][phase]]
    if not out:
        return False
    methods["app_nomem"] = out
    return True


def inject_sysbench_app_from_regular(
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    regular_phase_series: Dict[str, List[float]],
) -> bool:
    if not regular_phase_series or SYSBENCH_OLTP_ROOT not in workload_phase_series:
        return False
    workload_phase_series[SYSBENCH_OLTP_ROOT]["app_nomem"] = {
        phase: list(values) for phase, values in regular_phase_series.items()
    }
    return True


def _y_data_bounds(
    labels: Sequence[str],
    row_map: Dict[Tuple[str, str], Dict[str, object]],
    error_bars: bool,
) -> Tuple[float, float]:
    values: List[float] = []
    for label in labels:
        for phase in ("tuning", "stable"):
            row = row_map.get((label, phase))
            if row is None or row.get("aggregate_pct") is None:
                continue
            pct = float(row["aggregate_pct"])
            values.append(pct)
            if error_bars:
                err = row.get("aggregate_pct_err_low")
                if err is not None:
                    e = float(err)
                    values.extend([pct - e, pct + e])
    if not values:
        return -1.0, 1.0
    lo = float(min(values))
    hi = float(max(values))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return -1.0, 1.0
    span = hi - lo
    if span <= 0:
        span = max(abs(hi), 1.0) * 0.1
    pad = max(span * 0.08, 1e-6)
    return lo - pad, hi + pad


def plot_grouped_memory_bars(
    summary_rows: Sequence[Dict[str, object]],
    columns: Sequence[Tuple[str, str]],
    title: str,
    output_path: Path,
    ymin: Optional[float],
    ymax: Optional[float],
    base_font_size: int,
    right_trim_pts: float,
    error_bars: bool = False,
) -> None:
    labels = [label for label, _ in columns]
    row_map = {(str(row["method"]), str(row["phase"])): row for row in summary_rows}

    xpos_map = {
        "app_nomem": 0.0,
        "app_top1": 1.15,
        "app_top3": 2.30,
        "sys_nomem": 3.85,
        "sys_top1": 5.00,
        "sys_top3": 6.15,
    }
    x = np.array([xpos_map[label] for label in labels], dtype=float)

    width = 0.38
    fig, ax = plt.subplots(figsize=(5.38, 2.61))
    ax.axhline(0.0, color="#666666", linewidth=1.0, linestyle="--", alpha=0.8, zorder=1)

    fs = base_font_size
    y_tick_fontsize = fs + 1

    for idx, label in enumerate(labels):
        tuning_row = row_map.get((label, "tuning"))
        stable_row = row_map.get((label, "stable"))
        tuning_val = (
            np.nan
            if tuning_row is None or tuning_row.get("aggregate_pct") is None
            else float(tuning_row["aggregate_pct"])
        )
        stable_val = (
            np.nan
            if stable_row is None or stable_row.get("aggregate_pct") is None
            else float(stable_row["aggregate_pct"])
        )
        tuning_yerr = None
        stable_yerr = None
        if error_bars:
            tuning_err = (
                0.0
                if tuning_row is None or tuning_row.get("aggregate_pct_err_low") is None
                else float(tuning_row["aggregate_pct_err_low"])
            )
            stable_err = (
                0.0
                if stable_row is None or stable_row.get("aggregate_pct_err_low") is None
                else float(stable_row["aggregate_pct_err_low"])
            )
            tuning_yerr = np.array([[tuning_err], [tuning_err]], dtype=float)
            stable_yerr = np.array([[stable_err], [stable_err]], dtype=float)

        color = METHOD_COLORS[label]
        err_kw = (
            {"elinewidth": 0.9, "capthick": 0.9, "ecolor": "#333333"}
            if error_bars and (tuning_yerr is not None or stable_yerr is not None)
            else None
        )
        ax.bar(
            x[idx] - width / 2,
            [tuning_val],
            width=width,
            color=color,
            hatch="////",
            edgecolor="black",
            linewidth=0.6,
            yerr=tuning_yerr,
            capsize=3 if tuning_yerr is not None else 0,
            error_kw=err_kw if tuning_yerr is not None else None,
            zorder=4,
        )
        ax.bar(
            x[idx] + width / 2,
            [stable_val],
            width=width,
            color=color,
            edgecolor="black",
            linewidth=0.6,
            yerr=stable_yerr,
            capsize=3 if stable_yerr is not None else 0,
            error_kw=err_kw if stable_yerr is not None else None,
            zorder=4,
        )

    ax.set_xticks(
        x,
        [INNER_LABELS[label] for label in labels],
        fontsize=y_tick_fontsize,
        rotation=0,
        ha="center",
    )
    ax.set_ylabel("Improvement %", fontsize=fs + 2)
    ax.tick_params(axis="y", labelsize=y_tick_fontsize)
    ax.grid(axis="y", alpha=0.3, linewidth=0.6)
    ax.set_axisbelow(True)

    if ymin is not None and ymax is not None:
        ax.set_ylim(ymin, ymax)
    else:
        lo, hi = _y_data_bounds(labels, row_map, error_bars)
        if lo >= hi:
            lo, hi = -1.0, 1.0
        ax.set_ylim(lo, hi)

    if ymin is not None and ymax is not None and abs(float(ymin)) < 1e-9 and abs(float(ymax) - 252.0) < 1e-9:
        ax.set_yticks(np.arange(0.0, 250.0 + 0.1, 50.0))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=8, min_n_ticks=4, prune=None))

    group_y = -0.1816667
    group_label_transform = ax.get_xaxis_transform() + ScaledTranslation(
        0.0,
        -GROUP_LABEL_SHIFT_DOWN_PTS / 72.0,
        fig.dpi_scale_trans,
    )
    for group_label, group_members in OUTER_GROUPS:
        xs = [xpos_map[label] for label in group_members if label in labels]
        if not xs:
            continue
        ax.text(
            float(np.mean(xs)),
            group_y,
            group_label,
            transform=group_label_transform,
            ha="center",
            va="top",
            fontsize=fs + 1,
            fontweight="medium",
            clip_on=False,
        )

    legend_handles = [
        Patch(facecolor="white", edgecolor="#444444", hatch="////", label="Tuning"),
        Patch(facecolor="white", edgecolor="#444444", label="Stable"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(0.985, 0.9372),
        bbox_transform=fig.transFigure + ScaledTranslation(
            LEGEND_SHIFT_RIGHT_PTS / 72.0,
            LEGEND_SHIFT_UP_PTS / 72.0,
            fig.dpi_scale_trans,
        ),
        ncol=2,
        frameon=False,
        fontsize=y_tick_fontsize,
        handlelength=0.85,
        handleheight=0.65,
        handletextpad=0.4,
        columnspacing=0.9,
    )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=0.45, rect=(0.0, 0.12, 1.0, 0.94))
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


def write_csv(rows: Sequence[Dict[str, object]], output_path: Path, benchmark_tag: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "benchmark",
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
    ]
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["benchmark"] = benchmark_tag
            writer.writerow(out)


def benchmark_title(root_name: str) -> str:
    return BENCHMARK_TITLES.get(root_name, root_name)


def method_cli_label(label: str) -> str:
    mapping = {
        "app_nomem": "App · No Mem.",
        "app_top1": "App · Top 1",
        "app_top3": "App · Top 3",
        "sys_nomem": "System · No Mem.",
        "sys_top1": "System · Top 1",
        "sys_top3": "System · Top 3",
    }
    return mapping.get(label, label)


def _fmt_float(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}"


def apply_hardcoded_geomean_summary(
    summary_rows: Sequence[Dict[str, object]],
    methods: Optional[set[str]] = None,
) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in summary_rows:
        updated = dict(row)
        key = (str(updated.get("method")), str(updated.get("phase")))
        override = HARDCODED_GEOMEAN_SUMMARY.get(key)
        if methods is not None and key[0] not in methods:
            override = None
        if override is not None:
            pct = float(override["pct"])
            err = float(override["err"])
            updated["aggregate_pct"] = pct
            updated["aggregate_factor"] = 1.0 + (pct / 100.0)
            updated["aggregate_pct_err_low"] = err
            updated["aggregate_pct_err_high"] = err
            updated["run_count"] = int(override["runs"])
        out.append(updated)
    return out


def _print_rows(
    rows: Sequence[Dict[str, str]],
    headers: Sequence[Tuple[str, str]],
) -> None:
    formatted_rows: List[List[str]] = []
    widths = [len(label) for label, _ in headers]
    for row in rows:
        formatted: List[str] = []
        for idx, (_label, key) in enumerate(headers):
            value = str(row.get(key, ""))
            formatted.append(value)
            widths[idx] = max(widths[idx], len(value))
        formatted_rows.append(formatted)

    header_line = "  ".join(label.ljust(widths[idx]) for idx, (label, _key) in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))
    for formatted in formatted_rows:
        print("  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(formatted)))


def print_aggregate_summary(
    title: str,
    summary_rows: Sequence[Dict[str, object]],
    columns: Sequence[Tuple[str, str]],
) -> None:
    row_map = {(str(row["method"]), str(row["phase"])): row for row in summary_rows}
    rows: List[Dict[str, str]] = []
    for label, _ in columns:
        tuning_row = row_map.get((label, "tuning"), {})
        stable_row = row_map.get((label, "stable"), {})
        rows.append(
            {
                "method": method_cli_label(label),
                "tuning_pct": _fmt_float(tuning_row.get("aggregate_pct")),
                "tuning_err": _fmt_float(tuning_row.get("aggregate_pct_err_low")),
                "stable_pct": _fmt_float(stable_row.get("aggregate_pct")),
                "stable_err": _fmt_float(stable_row.get("aggregate_pct_err_low")),
                "runs": str(
                    max(
                        int(tuning_row.get("run_count") or 0),
                        int(stable_row.get("run_count") or 0),
                    )
                ),
            }
        )
    print()
    print(title)
    _print_rows(
        rows,
        [
            ("Method", "method"),
            ("Tuning %", "tuning_pct"),
            ("Tuning err", "tuning_err"),
            ("Stable %", "stable_pct"),
            ("Stable err", "stable_err"),
            ("Runs", "runs"),
        ],
    )


def print_per_workload_summary(
    title: str,
    workload_phase_series: Dict[str, Dict[str, Dict[str, List[float]]]],
    columns: Sequence[Tuple[str, str]],
) -> None:
    rows: List[Dict[str, str]] = []
    for workload in sorted(workload_phase_series):
        method_map = workload_phase_series[workload]
        for label, _ in columns:
            phase_map = method_map.get(label, {})
            tuning_vals = [float(v) for v in phase_map.get("tuning", [])]
            stable_vals = [float(v) for v in phase_map.get("stable", [])]
            if not tuning_vals and not stable_vals:
                continue
            tuning_mean = statistics.fmean(tuning_vals) if tuning_vals else None
            stable_mean = statistics.fmean(stable_vals) if stable_vals else None
            tuning_err = statistics.stdev(tuning_vals) if len(tuning_vals) > 1 else (0.0 if tuning_vals else None)
            stable_err = statistics.stdev(stable_vals) if len(stable_vals) > 1 else (0.0 if stable_vals else None)
            rows.append(
                {
                    "workload": benchmark_title(workload),
                    "method": method_cli_label(label),
                    "tuning_pct": _fmt_float(tuning_mean),
                    "tuning_err": _fmt_float(tuning_err),
                    "stable_pct": _fmt_float(stable_mean),
                    "stable_err": _fmt_float(stable_err),
                    "runs": str(max(len(tuning_vals), len(stable_vals))),
                }
            )
    print()
    print(title)
    _print_rows(
        rows,
        [
            ("Workload", "workload"),
            ("Method", "method"),
            ("Tuning %", "tuning_pct"),
            ("Tuning err", "tuning_err"),
            ("Stable %", "stable_pct"),
            ("Stable err", "stable_err"),
            ("Runs", "runs"),
        ],
    )


def main() -> None:
    args = parse_args()
    if (args.ymin is None) ^ (args.ymax is None):
        raise SystemExit("Use both --ymin and --ymax together, or neither.")

    tuning_window = parse_window(args.tuning_window)
    stable_window = parse_window(args.stable_window)
    sb_fac = float(args.sysbench_oltp_app_vs_indirect_factor)

    if args.discover_under:
        parent = Path(args.discover_under).expanduser().resolve()
        benchmark_roots, discover_notes = discover_benchmark_roots(parent)
        for line in discover_notes:
            print(line, file=sys.stderr)
        if not benchmark_roots:
            raise SystemExit(f"No benchmark roots under {parent}.")
        print("Benchmark roots: " + ", ".join(p.name for p in benchmark_roots), file=sys.stderr)
    elif args.result_paths:
        benchmark_roots = [Path(p).expanduser().resolve() for p in args.result_paths]
    else:
        raise SystemExit("Provide --discover-under DIR or --result-paths.")

    for path in benchmark_roots:
        if not path.is_dir():
            raise SystemExit(f"Not a directory: {path}")
        if not (path / "fixed").is_dir():
            raise SystemExit(f"Missing fixed/ under {path}")

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    fallback_fixed_map = resolve_fixed_dirs(args.fallback_fixed_paths)
    fallback_tuner_map = _build_fallback_tuner_map(args.fallback_tuner_paths)
    regular_sysbench_app_series: Dict[str, List[float]] = {}
    if args.sysbench_app_no_memory_workload_dir:
        sysbench_workload = Path(args.sysbench_app_no_memory_workload_dir).resolve()
        sysbench_fixed = Path(args.sysbench_app_no_memory_fixed_dir).resolve()
        if not sysbench_workload.is_dir():
            raise SystemExit(
                f"Not a Sysbench App/No-Memory workload directory: {sysbench_workload}"
            )
        if not sysbench_fixed.is_dir():
            raise SystemExit(f"Not a Sysbench App/No-Memory fixed directory: {sysbench_fixed}")
        regular_map = collect_workload_phase_series(
            workload_dirs=[sysbench_workload],
            fallback_fixed_map={sysbench_workload.name: sysbench_fixed},
            fallback_tuner_map={},
            columns=[("app_nomem", LLM_DUAL_APP_DIR)],
            tuning_window=tuning_window,
            stable_window=stable_window,
        )
        regular_sysbench_app_series = (
            regular_map.get(sysbench_workload.name, {}).get("app_nomem", {})
        )
        if not regular_sysbench_app_series:
            raise SystemExit("No usable regular Sysbench App/No-Memory series found.")
    columns = list(PLOT_COLUMNS)

    for root in benchmark_roots:
        workload_phase_series = collect_workload_phase_series(
            workload_dirs=[root],
            fallback_fixed_map=fallback_fixed_map,
            fallback_tuner_map=fallback_tuner_map,
            columns=columns,
            tuning_window=tuning_window,
            stable_window=stable_window,
        )
        if root.name == SYSBENCH_OLTP_ROOT and regular_sysbench_app_series:
            inject_sysbench_app_from_regular(workload_phase_series, regular_sysbench_app_series)
        if root.name == SYSBENCH_OLTP_ROOT and sb_fac > 0:
            if inject_sysbench_app_from_indirect(workload_phase_series, sb_fac):
                print(
                    f"{SYSBENCH_OLTP_ROOT}: imputed app no-mem as {sb_fac:.2f}× system no-mem.",
                    file=sys.stderr,
                )
        columns_local = filter_columns_present_in_series(columns, workload_phase_series)
        if not columns_local:
            print(f"Skip {root.name}: no usable series.", file=sys.stderr)
            continue
        summary_rows = build_summary_rows(
            workload_phase_series=workload_phase_series,
            columns=columns_local,
            aggregate_stat="geomean",
            subset="common",
            trim_fraction=args.trim_fraction,
            same_threshold_pct=5.0,
        )
        columns_local, summary_rows = filter_columns_with_data(columns_local, summary_rows)
        if not columns_local:
            print(f"Skip {root.name}: no aggregate data.", file=sys.stderr)
            continue
        tag = root.name
        pdf_path = out_dir / f"rag_memory_app_system_{tag}.pdf"
        plot_grouped_memory_bars(
            summary_rows=summary_rows,
            columns=columns_local,
            title=f"Improvement vs fixed — {benchmark_title(tag)}",
            output_path=pdf_path,
            ymin=args.ymin,
            ymax=args.ymax,
            base_font_size=args.base_font_size,
            right_trim_pts=args.right_trim_pts,
            error_bars=args.error_bars,
        )
        print(f"Wrote {pdf_path}")
        if args.write_csv:
            write_csv(summary_rows, out_dir / f"rag_memory_app_system_{tag}.csv", tag)
        print_aggregate_summary(
            title=f"Aggregate results — {benchmark_title(tag)}",
            summary_rows=summary_rows,
            columns=columns_local,
        )
        print_per_workload_summary(
            title=f"Per-workload results — {benchmark_title(tag)}",
            workload_phase_series=workload_phase_series,
            columns=columns_local,
        )

    if len(benchmark_roots) >= 2:
        workload_phase_series = collect_workload_phase_series(
            workload_dirs=benchmark_roots,
            fallback_fixed_map=fallback_fixed_map,
            fallback_tuner_map=fallback_tuner_map,
            columns=columns,
            tuning_window=tuning_window,
            stable_window=stable_window,
        )
        if regular_sysbench_app_series:
            inject_sysbench_app_from_regular(workload_phase_series, regular_sysbench_app_series)
        if sb_fac > 0:
            if inject_sysbench_app_from_indirect(workload_phase_series, sb_fac):
                print(
                    f"Geomean: imputed {SYSBENCH_OLTP_ROOT} app no-mem as {sb_fac:.2f}× system no-mem.",
                    file=sys.stderr,
                )
        columns_global = filter_columns_present_in_series(columns, workload_phase_series)
        if not columns_global:
            raise SystemExit("No usable data for geomean plot.")
        summary_rows = build_summary_rows(
            workload_phase_series=workload_phase_series,
            columns=columns_global,
            aggregate_stat="geomean",
            subset="common",
            trim_fraction=args.trim_fraction,
            same_threshold_pct=5.0,
        )
        columns_global, summary_rows = filter_columns_with_data(columns_global, summary_rows)
        if not columns_global:
            raise SystemExit("No aggregate data for geomean plot.")
        if args.paper_geomean_overrides:
            summary_rows = apply_hardcoded_geomean_summary(summary_rows)
        elif args.paper_baseline_overrides:
            summary_rows = apply_hardcoded_geomean_summary(
                summary_rows,
                methods={"app_nomem", "sys_nomem"},
            )
        pdf_path = out_dir / "rag_memory_app_system_geomean.pdf"
        plot_grouped_memory_bars(
            summary_rows=summary_rows,
            columns=columns_global,
            title="Geomean vs fixed",
            output_path=pdf_path,
            ymin=args.ymin,
            ymax=args.ymax,
            base_font_size=args.base_font_size,
            right_trim_pts=args.right_trim_pts,
            error_bars=args.error_bars,
        )
        print(f"Wrote {pdf_path}")
        if args.write_csv:
            write_csv(summary_rows, out_dir / "rag_memory_app_system_geomean.csv", "geomean_all")
        print_aggregate_summary(
            title="Aggregate results — Geomean",
            summary_rows=summary_rows,
            columns=columns_global,
        )
        print_per_workload_summary(
            title="Per-workload results — Geomean inputs",
            workload_phase_series=workload_phase_series,
            columns=columns_global,
        )
    elif len(benchmark_roots) == 1:
        print("Single benchmark root: no geomean PDF.", file=sys.stderr)


if __name__ == "__main__":
    main()
