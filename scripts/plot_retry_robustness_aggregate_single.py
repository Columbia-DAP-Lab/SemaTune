#!/usr/bin/env python3
"""Compact aggregate robustness figure for TuxBot, TuxBot-Trim, and MLOS."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator

from plot_agentic_memory_comparison import (
    ROBUSTNESS_ERROR_KEYS,
    ROBUSTNESS_METRICS,
    benchmark_label,
    collect_aggregate_robustness,
)

ALL_EXPERIMENTS = [
    "masstree_hi_p99",
    "mutilate_high",
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
]

DEFAULT_EXPERIMENTS = [exp for exp in ALL_EXPERIMENTS if exp != "xapian_hi_p99"]

BASE_METHODS = [
    ("TuxBot", "llm_dual_app_metrics_final_actor"),
    ("TuxBot-Trim", "mlos_trimming_aggressive|mlos_trimming"),
    ("MLOS", "mlos_50_tuning_only|mlos"),
]

METHOD_COLORS = {
    "TuxBot": "#0072B2",
    "TuxBot-Trim": "#009E73",
    "MLOS": "#E69F00",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot compact aggregate robustness for selected workloads.")
    p.add_argument("--result-paths", nargs="+", required=True)
    p.add_argument("--fallback-fixed-paths", nargs="+", required=True)
    p.add_argument("--experiments", nargs="+", default=list(DEFAULT_EXPERIMENTS))
    p.add_argument(
        "--aggregate-exclude-experiments",
        nargs="*",
        default=[],
        help=(
            "Optional experiments to exclude only from the aggregate plot/summary/CSV. "
            "They still appear in the per-benchmark breakdown."
        ),
    )
    p.add_argument("--tuning-window", default="1-30")
    p.add_argument(
        "--metrics",
        default="p50_bad_window_rate_pct,p10_bad_window_rate_pct,variability_pct_of_fixed",
        help=(
            "Comma-separated robustness metric keys to plot. "
            "Choices: p50_bad_window_rate_pct,p10_bad_window_rate_pct,variability_pct_of_fixed"
        ),
    )
    p.add_argument("--plot-output", required=True)
    p.add_argument("--csv-output", required=True)
    p.add_argument("--with-and-without-xapian", action="store_true")
    p.add_argument("--ymin", type=float, default=0.0)
    p.add_argument("--ymax", type=float, default=50.0)
    p.add_argument(
        "--ytick-step",
        type=float,
        default=10.0,
        help="Major y-tick spacing for the non-broken y-axis case.",
    )
    p.add_argument(
        "--ytick-max",
        type=float,
        default=None,
        help="Optional maximum value to use when generating explicit y ticks.",
    )
    p.add_argument(
        "--ybreak-lower-end",
        type=float,
        default=None,
        help="Optional lower segment end for a broken y-axis.",
    )
    p.add_argument(
        "--ybreak-upper-start",
        type=float,
        default=None,
        help="Optional upper segment start for a broken y-axis.",
    )
    p.add_argument("--base-font-size", type=float, default=8.5)
    p.add_argument(
        "--figure-height-scale",
        type=float,
        default=1.0,
        help="Scale factor applied to the default figure height (default: 1.0).",
    )
    return p.parse_args()


def parse_window(spec: str) -> tuple[int, int]:
    a, b = spec.split("-")
    return int(a), int(b)


def _base_method_label(label: str) -> str:
    suffix = " no Xapian"
    return label[:-len(suffix)] if label.endswith(suffix) else label


def _excluded_workloads_for_label(label: str) -> set[str]:
    if label.endswith(" no Xapian"):
        return {"xapian_hi_p99"}
    return set()


def _normalize_paths(paths: list[str], experiments: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw).resolve()
        candidates = [p]
        if p.is_dir():
            for exp in experiments:
                child = p / exp
                if child.is_dir():
                    candidates.append(child)
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _parse_metric_keys(spec: str) -> list[str]:
    valid = {key for key, _label in ROBUSTNESS_METRICS}
    keys = [item.strip() for item in spec.split(",") if item.strip()]
    if not keys:
        raise SystemExit("--metrics must contain at least one metric key.")
    invalid = [key for key in keys if key not in valid]
    if invalid:
        raise SystemExit(f"Unsupported --metrics keys: {', '.join(invalid)}")
    return keys


def main() -> None:
    args = parse_args()
    metric_keys = _parse_metric_keys(args.metrics)
    normalized_result_paths = _normalize_paths(list(args.result_paths), list(args.experiments))
    normalized_fixed_paths = _normalize_paths(list(args.fallback_fixed_paths), list(args.experiments))
    aggregate_experiments = [
        exp for exp in args.experiments if exp not in set(args.aggregate_exclude_experiments)
    ]
    full_aggregates = collect_aggregate_robustness(
        result_paths=normalized_result_paths,
        fallback_fixed_paths=normalized_fixed_paths,
        experiments=aggregate_experiments,
        tuning_window=parse_window(args.tuning_window),
        methods=BASE_METHODS,
        method_workload_overrides={},
    )
    labels = [label for label, _ in BASE_METHODS]
    aggregate_by_label = {label: full_aggregates.get(label, {}) for label in labels}

    if args.with_and_without_xapian:
        no_xapian_experiments = [exp for exp in aggregate_experiments if exp != "xapian_hi_p99"]
        no_xapian_aggregates = collect_aggregate_robustness(
            result_paths=normalized_result_paths,
            fallback_fixed_paths=normalized_fixed_paths,
            experiments=no_xapian_experiments,
            tuning_window=parse_window(args.tuning_window),
            methods=BASE_METHODS,
            method_workload_overrides={},
        )
        for label, _ in BASE_METHODS:
            aggregate_by_label[f"{label} no Xapian"] = no_xapian_aggregates.get(label, {})
            labels.append(f"{label} no Xapian")

    fs = args.base_font_size
    metric_labels = [label for key, label in ROBUSTNESS_METRICS if key in metric_keys]
    x = np.arange(len(metric_keys), dtype=float)
    base_labels = [label for label, _ in BASE_METHODS]
    width = 0.22
    offsets = np.linspace(-width, width, num=len(base_labels))

    fig_width = 1.75 if len(metric_keys) == 1 else 3.45
    fig_height = 1.1 * 0.9
    use_broken_axis = (
        args.ybreak_lower_end is not None
        and args.ybreak_upper_start is not None
        and float(args.ybreak_lower_end) < float(args.ybreak_upper_start)
    )
    y_label_pad = 5
    y_label_down_pts = 5.0
    if use_broken_axis:
        lower_range = float(args.ybreak_lower_end) - float(args.ymin)
        upper_range = float(args.ymax) - float(args.ybreak_upper_start)
        if lower_range <= 0 or upper_range <= 0:
            raise SystemExit("Broken-axis ranges must be positive.")
        fig_height *= 1.15
    fig_height *= float(args.figure_height_scale)

    if use_broken_axis:
        fig, (ax_top, ax_bottom) = plt.subplots(
            2,
            1,
            figsize=(fig_width, fig_height),
            constrained_layout=False,
            sharex=True,
            gridspec_kw={"height_ratios": [upper_range, lower_range], "hspace": 0.05},
        )
        axes = [ax_top, ax_bottom]
    else:
        fig, ax = plt.subplots(1, 1, figsize=(fig_width, fig_height), constrained_layout=False)
        axes = [ax]

    def _plot_label(label: str) -> None:
        base = _base_method_label(label)
        row = aggregate_by_label.get(label, {})
        vals = [row.get(metric_key) or 0.0 for metric_key in metric_keys]
        errs = [row.get(ROBUSTNESS_ERROR_KEYS[metric_key]) or 0.0 for metric_key in metric_keys]
        dashed_variant = label.endswith(" no Xapian")
        edgecolor = METHOD_COLORS[base]
        for axis in axes:
            axis.bar(
                x + offsets[base_labels.index(base)],
                vals,
                width=width,
                yerr=errs,
                color="none" if dashed_variant else METHOD_COLORS[base],
                hatch="////",
                edgecolor=edgecolor if dashed_variant else "#333333",
                linewidth=1.2 if dashed_variant else 0.7,
                linestyle="--" if dashed_variant else "-",
                capsize=2.2,
                error_kw={
                    "elinewidth": 0.8,
                    "ecolor": edgecolor if dashed_variant else "#444444",
                    "capthick": 0.8,
                },
                zorder=3 if dashed_variant else 4,
            )

    for label in labels:
        if label.endswith(" no Xapian"):
            _plot_label(label)
    for label in labels:
        if not label.endswith(" no Xapian"):
            _plot_label(label)

    legend_ax = axes[0] if use_broken_axis else axes[0]
    if use_broken_axis:
        ax_top.set_ylim(float(args.ybreak_upper_start), args.ymax)
        ax_bottom.set_ylim(args.ymin, float(args.ybreak_lower_end))
        ax_top.yaxis.set_major_locator(MultipleLocator(10))
        ax_bottom.yaxis.set_major_locator(MultipleLocator(10))
        ax_top.tick_params(axis="y", labelsize=fs - 1, pad=1)
        ax_bottom.tick_params(axis="y", labelsize=fs - 1, pad=1)
        ax_bottom.tick_params(axis="x", length=0)
        ax_top.tick_params(axis="x", length=0, labelbottom=False)
        ax_bottom.set_xticks(x)
        ax_bottom.set_xticklabels(metric_labels, fontsize=fs)
        ax_bottom.set_ylabel("Aggregate %", fontsize=fs, labelpad=y_label_pad)
        ax_bottom.yaxis.label.set_transform(
            ax_bottom.yaxis.label.get_transform()
            + mtransforms.ScaledTranslation(0.0, -y_label_down_pts / 72.0, fig.dpi_scale_trans)
        )
        for axis in axes:
            axis.grid(axis="y", alpha=0.28)
            axis.set_axisbelow(True)
            axis.spines["right"].set_visible(False)
        ax_top.spines["top"].set_visible(False)
        ax_bottom.spines["top"].set_visible(False)
        ax_top.spines["bottom"].set_visible(False)

        d = 0.012
        kwargs_bottom = dict(transform=ax_bottom.transAxes, color="#333333", clip_on=False, linewidth=0.8)
        kwargs_top = dict(transform=ax_top.transAxes, color="#333333", clip_on=False, linewidth=0.8)
        ax_bottom.plot((-d, +d), (1 - d, 1 + d), **kwargs_bottom)
        ax_bottom.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs_bottom)
        ax_top.plot((-d, +d), (-d, +d), **kwargs_top)
        ax_top.plot((1 - d, 1 + d), (-d, +d), **kwargs_top)
    else:
        ax = axes[0]
        ax.set_xticks(x)
        ax.set_xticklabels(metric_labels, fontsize=fs)
        ax.set_ylabel("Aggregate %", fontsize=fs, labelpad=y_label_pad)
        ax.yaxis.label.set_transform(
            ax.yaxis.label.get_transform()
            + mtransforms.ScaledTranslation(0.0, -y_label_down_pts / 72.0, fig.dpi_scale_trans)
        )
        ax.set_ylim(args.ymin, args.ymax)
        tick_max = args.ymax if args.ytick_max is None else min(args.ymax, float(args.ytick_max))
        ax.set_yticks(np.arange(args.ymin, tick_max + 0.1, float(args.ytick_step)))
        ax.tick_params(axis="y", labelsize=fs - 1, pad=1)
        ax.tick_params(axis="x", length=0)
        ax.grid(axis="y", alpha=0.28)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    legend_handles = [
        Patch(facecolor=METHOD_COLORS["TuxBot"], edgecolor="#333333", label="TuxBot"),
        Patch(facecolor=METHOD_COLORS["TuxBot-Trim"], edgecolor="#333333", label="TuxBot-Trim"),
        Patch(facecolor=METHOD_COLORS["MLOS"], edgecolor="#333333", label="MLOS"),
    ]
    if args.with_and_without_xapian:
        legend_handles.append(
            Patch(facecolor="white", edgecolor="#444444", linestyle="--", linewidth=1.4, label="No Xapian")
        )
    legend_transform = legend_ax.transAxes + mtransforms.ScaledTranslation(0, 4 / 72.0, fig.dpi_scale_trans)
    legend_ax.legend(
        handles=legend_handles,
        frameon=False,
        fontsize=fs - 1,
        loc="upper left",
        bbox_to_anchor=(0.0, 1.0),
        bbox_transform=legend_transform,
        ncol=3,
        handlelength=1.2,
        columnspacing=0.8,
        borderaxespad=0.1,
    )

    if use_broken_axis:
        fig.subplots_adjust(left=0.16, right=0.995, top=0.95, bottom=0.22)
    else:
        fig.subplots_adjust(left=0.14, right=0.995, top=0.95, bottom=0.22)

    plot_path = Path(args.plot_output)
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)

    csv_path = Path(args.csv_output)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "method",
                "p50_poor_measurement_rate_pct",
                "p50_poor_measurement_rate_error_pct",
                "p10_poor_measurement_rate_pct",
                "p10_poor_measurement_rate_error_pct",
                "variability_pct_of_fixed",
                "variability_pct_of_fixed_error_pct",
                "n_workloads",
                "run_count",
                "workloads",
            ],
        )
        writer.writeheader()
        for label in labels:
            row = aggregate_by_label.get(label, {})
            workloads = row.get("workloads") or ""
            if not workloads:
                if label.endswith(" no Xapian"):
                    workloads = ",".join(exp for exp in aggregate_experiments if exp != "xapian_hi_p99")
                else:
                    workloads = full_aggregates.get("_workloads", {}).get("names", "")
            writer.writerow(
                {
                    "method": label,
                    "p50_poor_measurement_rate_pct": row.get("p50_bad_window_rate_pct"),
                    "p50_poor_measurement_rate_error_pct": row.get("p50_bad_window_rate_error_pct"),
                    "p10_poor_measurement_rate_pct": row.get("p10_bad_window_rate_pct"),
                    "p10_poor_measurement_rate_error_pct": row.get("p10_bad_window_rate_error_pct"),
                    "variability_pct_of_fixed": row.get("variability_pct_of_fixed"),
                    "variability_pct_of_fixed_error_pct": row.get("variability_pct_of_fixed_error_pct"),
                    "n_workloads": row.get("n_workloads"),
                    "run_count": row.get("run_count"),
                    "workloads": workloads,
                }
            )

    print(f"Wrote plot: {plot_path}")
    print(f"Wrote CSV: {csv_path}")
    print()
    metric_label_map = dict(ROBUSTNESS_METRICS)
    summary_headers = [metric_label_map[key] for key in metric_keys]
    header = f"{'Method':<20}"
    for label in summary_headers:
        header += f" {label:>18}"
    header += f" {'n':>4} {'runs':>5}"
    print(header)
    print("-" * len(header))
    for label in labels:
        row = aggregate_by_label.get(label, {})
        line = f"{label:<20}"
        for metric_key in metric_keys:
            value = row.get(metric_key)
            error = row.get(ROBUSTNESS_ERROR_KEYS[metric_key])
            if value is None:
                cell = "N/A"
            else:
                err_value = 0.0 if error is None else float(error)
                cell = f"{float(value):.1f} +/- {err_value:.1f}"
            line += f" {cell:>18}"
        n_workloads = row.get("n_workloads")
        run_count = row.get("run_count")
        line += f" {'' if n_workloads is None else int(n_workloads):>4} {'' if run_count is None else int(run_count):>5}"
        print(line)
    workload_names = full_aggregates.get("_workloads", {}).get("names", "")
    if workload_names:
        print()
        print(f"Workloads: {workload_names}")

    print()
    print("Per-benchmark:")
    for experiment in args.experiments:
        per_benchmark = collect_aggregate_robustness(
            result_paths=normalized_result_paths,
            fallback_fixed_paths=normalized_fixed_paths,
            experiments=[experiment],
            tuning_window=parse_window(args.tuning_window),
            methods=BASE_METHODS,
            method_workload_overrides={},
        )
        print(benchmark_label(experiment))
        print(header)
        print("-" * len(header))
        for label in labels:
            base_label = _base_method_label(label)
            row = per_benchmark.get(base_label, {})
            line = f"{label:<20}"
            for metric_key in metric_keys:
                value = row.get(metric_key)
                error = row.get(ROBUSTNESS_ERROR_KEYS[metric_key])
                if value is None:
                    cell = "N/A"
                else:
                    err_value = 0.0 if error is None else float(error)
                    cell = f"{float(value):.1f} +/- {err_value:.1f}"
                line += f" {cell:>18}"
            n_workloads = row.get("n_workloads")
            run_count = row.get("run_count")
            line += f" {'' if n_workloads is None else int(n_workloads):>4} {'' if run_count is None else int(run_count):>5}"
            print(line)
        print()


if __name__ == "__main__":
    main()
