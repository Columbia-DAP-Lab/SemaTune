#!/usr/bin/env python3
"""Build a single two-panel motivation figure for MLOS proxy and knob-surface failures."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.transforms import ScaledTranslation


ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "LLM-OS-Tuning-SOSP-26" / "figures" / "motivation"
STABLE_WINDOW = (31, 50)
DEFAULT_WIKIPEDIA_RESULTS = ROOT / "all_results" / "results_config_full_param_wikipedia_p99_retry" / "wikipedia_p99"
TPCC_WORKLOAD = "tpcc_hi_p99"
GOAL_METRIC = "latency_p99"
FIGSIZE = (6.6, 1.91)
SUBPLOTS_ADJUST = {
    "left": 0.10,
    "right": 0.995,
    "bottom": 0.18,
    "top": 0.82,
    "wspace": 0.34,
}
WIKIPEDIA_SHIFT_RIGHT_PTS = 10.0
LEGEND_SHIFT_UP_PTS = 7.0
X_LABEL_SHIFT_DOWN_PTS = 5.0

WIKIPEDIA_METHODS: List[Tuple[str, str, str]] = [
    ("App", "mlos_50_tuning_only", "#e67e22"),
    ("IPC", "mlos_ipc_50_tuning_only", "#c0392b"),
    ("Cache", "mlos_cache_misses_50_tuning_only", "#8c564b"),
]

TPCC_METHODS: List[Tuple[str, int, str]] = [
    ("1", 1, "#4CC9F0"),
    ("2", 2, "#4895EF"),
    ("8", 8, "#4361EE"),
    ("32", 32, "#5A4FB2"),
]

TPCC_8_PARAM_FALLBACK = (
    ROOT / "all_results" / "results_config_full_param_tpcc_hi_p99_20260306_080130_retry"
    / TPCC_WORKLOAD / "mlos_50_tuning_only"
)

sys.path.insert(0, str(ROOT / "scripts"))
from generate_full_performance_table import (  # noqa: E402
    detect_metric_goal_from_fixed,
    history_run_completed_successfully,
    iter_history_files,
    load_json,
    phase_mean_for_run,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--wikipedia-results-root",
        default=str(DEFAULT_WIKIPEDIA_RESULTS),
        help="Wikipedia workload directory containing fixed/mlos_* subdirectories.",
    )
    p.add_argument(
        "--tpcc-default-dir",
        default=str(ROOT / "all_results" / "results_config_full_param_tpcc_hi_p99_new" / TPCC_WORKLOAD / "fixed"),
        help="TPC-C fixed-baseline directory.",
    )
    p.add_argument(
        "--tpcc-param-results-root",
        default=str(ROOT / "all_results" / "results_params" / "tpcc_p99_final"),
        help="Root containing the TPC-C parameter-count result directories.",
    )
    p.add_argument(
        "--tpcc-eight-param-dir",
        default=str(TPCC_8_PARAM_FALLBACK),
        help="Fallback MLOS directory for the eight-parameter TPC-C point.",
    )
    p.add_argument(
        "--output-pdf",
        default=str(FIG_DIR / "mlos_motivation_examples_combined.pdf"),
        help="Combined two-panel PDF output path.",
    )
    p.add_argument(
        "--output-csv",
        default=str(FIG_DIR / "mlos_motivation_examples_combined.csv"),
        help="Optional combined summary CSV output path.",
    )
    p.add_argument(
        "--default-label",
        default="Default Params",
        help="Legend label for the dashed baseline on the left panel.",
    )
    return p.parse_args()


def _stable_run_means(tuner_dir: Path, metric_name: str) -> List[float]:
    values: List[float] = []
    for fp in iter_history_files(tuner_dir):
        data = load_json(fp)
        if data is None or not history_run_completed_successfully(data):
            continue
        value = phase_mean_for_run(data, metric_name, STABLE_WINDOW)
        if value is not None:
            values.append(value)
    return values


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.fmean(values)


def _sd(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _wikipedia_axis_label(metric_name: str) -> str:
    if (metric_name or "").lower() == "latency_p99":
        return "p99 latency (ms)"
    return metric_name.replace("_", " ")


def _collect_wikipedia_panel(results_root: Path) -> Dict[str, object]:
    fixed_dir = results_root / "fixed"
    if not fixed_dir.is_dir():
        raise SystemExit(f"Missing fixed dir: {fixed_dir}")

    metric_name, _goal = detect_metric_goal_from_fixed(fixed_dir, results_root.name)
    if metric_name is None:
        metric_name = GOAL_METRIC

    default_values = _stable_run_means(fixed_dir, metric_name)
    default_mean = _mean(default_values)
    if default_mean is None:
        raise SystemExit(f"No completed fixed runs found in {fixed_dir}")

    rows: List[Dict[str, object]] = []
    labels: List[str] = []
    means: List[float] = []
    sds: List[float] = []
    colors: List[str] = []

    for display_label, dirname, color in WIKIPEDIA_METHODS:
        tuner_dir = results_root / dirname
        values = _stable_run_means(tuner_dir, metric_name)
        mean = _mean(values)
        sd = _sd(values)
        rows.append(
            {
                "panel": "wikipedia",
                "label": display_label,
                "source_dir": str(tuner_dir),
                "stable_mean_metric": f"{mean:.6f}" if mean is not None else "",
                "stable_sd_metric": f"{sd:.6f}",
                "completed_runs": len(values),
                "default_stable_mean_metric": f"{default_mean:.6f}",
            }
        )
        if mean is not None:
            labels.append(display_label)
            means.append(mean)
            sds.append(sd)
            colors.append(color)

    return {
        "title": "Wikipedia",
        "axis_label": _wikipedia_axis_label(metric_name),
        "labels": labels,
        "means": means,
        "sds": sds,
        "colors": colors,
        "default_mean": default_mean,
        "rows": rows,
    }


def _tpcc_ablation_dir(param_count: int, results_root: Path, eight_param_dir: Path) -> Optional[Path]:
    glob_pat = (
        f"results_config_ablation_params_{param_count}_param_{TPCC_WORKLOAD}_*/"
        f"ablation_params/{param_count}_param/{TPCC_WORKLOAD}/mlos"
    )
    matches = sorted(results_root.glob(glob_pat))
    if matches:
        return matches[-1]
    if param_count == 8 and eight_param_dir.exists():
        return eight_param_dir
    return None


def _collect_tpcc_panel(default_dir: Path, results_root: Path, eight_param_dir: Path) -> Dict[str, object]:
    default_values = _stable_run_means(default_dir, GOAL_METRIC)
    default_mean = _mean(default_values)
    if default_mean is None:
        raise SystemExit(f"No completed fixed runs found in {default_dir}")

    rows: List[Dict[str, object]] = []
    labels: List[str] = []
    means: List[float] = []
    sds: List[float] = []
    colors: List[str] = []

    for display_label, param_count, color in TPCC_METHODS:
        tuner_dir = _tpcc_ablation_dir(param_count, results_root, eight_param_dir)
        values: List[float] = []
        if tuner_dir is not None and tuner_dir.is_dir():
            values = _stable_run_means(tuner_dir, GOAL_METRIC)
        mean = _mean(values)
        sd = _sd(values)
        rows.append(
            {
                "panel": "tpcc",
                "label": display_label,
                "param_count": param_count,
                "source_dir": str(tuner_dir) if tuner_dir is not None else "",
                "stable_mean_metric": f"{mean:.6f}" if mean is not None else "",
                "stable_sd_metric": f"{sd:.6f}",
                "completed_runs": len(values),
                "default_stable_mean_metric": f"{default_mean:.6f}",
            }
        )
        if mean is not None:
            labels.append(display_label)
            means.append(mean)
            sds.append(sd)
            colors.append(color)

    return {
        "title": "TPC-C",
        "axis_label": "p99 latency (ms)",
        "labels": labels,
        "means": means,
        "sds": sds,
        "colors": colors,
        "default_mean": default_mean,
        "rows": rows,
    }


def _style_axis(ax: plt.Axes) -> None:
    ax.tick_params(axis="x", labelsize=16)
    ax.tick_params(axis="y", length=0, labelsize=17)
    ax.grid(axis="x", alpha=0.28, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#000000")
    ax.spines["bottom"].set_color("#000000")
    ax.spines["left"].set_linewidth(1.0)
    ax.spines["bottom"].set_linewidth(1.0)


def _plot_panel(
    ax: plt.Axes,
    panel: Dict[str, object],
    *,
    show_default_legend: bool,
    default_label: str,
) -> None:
    labels = list(panel["labels"])
    means = list(panel["means"])
    sds = list(panel["sds"])
    colors = list(panel["colors"])
    default_mean = float(panel["default_mean"])

    y = np.arange(len(labels), dtype=float) * 0.90
    ax.barh(
        y,
        means,
        xerr=sds,
        height=0.52,
        color=colors,
        edgecolor="black",
        linewidth=0.7,
        capsize=3,
        error_kw={"elinewidth": 0.9, "capthick": 0.9, "ecolor": "#333333"},
        zorder=2,
    )
    ax.axvline(default_mean, color="#000000", linestyle="--", linewidth=1.6, zorder=4)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    x_max = max([default_mean] + [mean + sd for mean, sd in zip(means, sds)])
    if str(panel["title"]) == "TPC-C":
        ax.set_xlim(0, 140)
    else:
        ax.set_xlim(0, x_max * 1.15)
    _style_axis(ax)

    if show_default_legend:
        ax.legend(
            handles=[
                Line2D([0], [0], color="#000000", linestyle="--", linewidth=1.6, label=default_label)
            ],
            loc="upper left",
            bbox_to_anchor=(0.0, 1.02),
            borderaxespad=0.0,
            frameon=False,
            fontsize=16.5,
            handlelength=1.6,
        )


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "panel",
                "label",
                "param_count",
                "source_dir",
                "stable_mean_metric",
                "stable_sd_metric",
                "completed_runs",
                "default_stable_mean_metric",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    wikipedia_panel = _collect_wikipedia_panel(Path(args.wikipedia_results_root).resolve())
    tpcc_panel = _collect_tpcc_panel(
        Path(args.tpcc_default_dir).resolve(),
        Path(args.tpcc_param_results_root).resolve(),
        Path(args.tpcc_eight_param_dir).resolve(),
    )

    out_pdf = Path(args.output_pdf).resolve()
    out_csv = Path(args.output_csv).resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=FIGSIZE)
    _plot_panel(ax_left, wikipedia_panel, show_default_legend=True, default_label=args.default_label)
    _plot_panel(ax_right, tpcc_panel, show_default_legend=False, default_label=args.default_label)

    xlabel_text = fig.text(
        0.5,
        0.045,
        "Stable p99 latency (ms)",
        ha="center",
        va="center",
        fontsize=17,
        transform=fig.transFigure + ScaledTranslation(0.0, (-6.0 - X_LABEL_SHIFT_DOWN_PTS) / 72.0, fig.dpi_scale_trans),
    )
    fig.subplots_adjust(**SUBPLOTS_ADJUST)
    shift_frac = WIKIPEDIA_SHIFT_RIGHT_PTS / (72.0 * FIGSIZE[0])
    left_pos = ax_left.get_position()
    right_pos = ax_right.get_position()
    left_pos_shifted = [left_pos.x0 + shift_frac, left_pos.y0, left_pos.width, left_pos.height]
    ax_left.set_position(left_pos_shifted)
    left_pos = ax_left.get_position()

    legend = ax_left.get_legend()
    if legend is not None:
        legend_anchor = (
            left_pos.x0,
            left_pos.y0 + 1.02 * left_pos.height + LEGEND_SHIFT_UP_PTS / (72.0 * FIGSIZE[1]),
        )
        legend.set_bbox_to_anchor(legend_anchor, transform=fig.transFigure)

    wikipedia_height = left_pos.height * 0.80
    ax_left.set_position([left_pos.x0, right_pos.y0, left_pos.width, wikipedia_height])
    fig.savefig(out_pdf, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)

    combined_rows = list(wikipedia_panel["rows"]) + list(tpcc_panel["rows"])
    write_csv(out_csv, combined_rows)

    print(f"Wrote {out_pdf}")
    print(f"Wrote {out_csv}")


if __name__ == "__main__":
    main()
