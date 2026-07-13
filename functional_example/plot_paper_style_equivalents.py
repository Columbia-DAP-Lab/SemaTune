#!/usr/bin/env python3
"""Render Functional Figure 6/7/8 equivalents with the paper plot styles."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.transforms import ScaledTranslation  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from plot_retry_aggregate_improvement import plot_summary, write_csv  # noqa: E402


FIGURE_6 = (
    ("Tuxbot App Metrics Dual Loop", "sematune_dual"),
    ("MLOS + Tuxbot", "sematune_trim"),
    ("MLOS", "mlos"),
    ("Bayesian", "bayesian"),
    ("DQN", "dqn"),
    ("Q-Learning", "qlearning"),
)
FIGURE_7 = (
    ("Tuxbot App Only Dual", "sematune_dual"),
    ("Tuxbot Indirect Dump Dual", "sematune_system"),
    ("Tuxbot IPC Dual", "sematune_ipc"),
    ("TuxBot Trim App", "sematune_trim"),
    ("TuxBot Trim IPC", "sematune_trim_ipc"),
    ("TuxBot Trim Cache", "sematune_trim_cache"),
    ("MLOS App Metrics", "mlos"),
    ("MLOS IPC", "mlos_ipc"),
    ("MLOS Cache Misses", "mlos_cache"),
)
FIGURE_8 = (
    ("TuxBot", "sematune_dual"),
    ("MLOS + TuxBot", "sematune_trim"),
    ("Single-Instant", "sematune_single"),
    ("MLOS", "mlos"),
)
FIGURE_9 = (
    ("TuxBot", "sematune_dual"),
    ("TuxBot-Trim", "sematune_trim"),
    ("MLOS", "mlos"),
)

COLORS = {
    "TuxBot": "#0072B2",
    "Single-Reasoning": "#D55E00",
    "Single-Instant": "#009E73",
    "MLOS": "#E69F00",
    "MLOS + TuxBot": "#CC79A7",
}
MARKERS = {
    "TuxBot": "D",
    "Single-Reasoning": "s",
    "Single-Instant": "^",
    "MLOS": "H",
    "MLOS + TuxBot": "P",
}
COLOR_DARK_GRAY = "#4D4D4D"
FOOTER = "* For demonstration purposes only. Actual evaluation setup is in the Results-Reproduced section."


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def improvement_row(label: str, phase: str, fixed: dict, candidate: dict) -> dict:
    fixed_mean = float(fixed[f"{phase}_mean"])
    candidate_mean = float(candidate[f"{phase}_mean"])
    factor = fixed_mean / candidate_mean
    pct = (factor - 1.0) * 100.0
    return {
        "phase": phase,
        "method": label,
        "n_workloads": 1,
        "run_count": 1,
        "aggregate_factor": factor,
        "aggregate_pct": pct,
        "aggregate_pct_err_low": 0.0,
        "aggregate_pct_err_high": 0.0,
        "workloads": "sysbench_oltp_rw",
        "phase_union_workloads": "sysbench_oltp_rw",
        "n_improve": int(pct > 5.0),
        "n_same": int(abs(pct) <= 5.0),
        "n_deteriorate": int(pct < -5.0),
    }


def render_paper_bar_style(
    methods: dict,
    mapping: tuple[tuple[str, str], ...],
    output: Path,
    figure: int,
) -> None:
    fixed = methods["fixed"]
    rows = [
        improvement_row(label, phase, fixed, methods[method_id])
        for label, method_id in mapping
        for phase in ("tuning", "stable")
    ]
    columns = [(label, method_id) for label, method_id in mapping]
    kwargs = {
        "summary_rows": rows,
        "columns": columns,
        "aggregate_stat": "geomean",
        "subset": "union",
        "error_bars": False,
        "auto_y": False,
        "auto_y_min": None,
        "auto_y_max": None,
        "base_font_size": 16,
        "x_shift_map": {},
        "x_group_shift_map": {},
        "x_font_delta_map": {},
        "footer_text": FOOTER,
    }
    if figure == 6:
        kwargs.update({
            "ymin": -60.0,
            "ymax": 110.0,
            "x_label_map": {},
            "x_group_map": {},
            "x_group_y_shift_pts": 0.0,
            "right_trim_pts": 0.0,
        })
    else:
        kwargs.update({
            "ymin": -75.0,
            "ymax": 100.0,
            "x_label_map": {
                "Tuxbot App Only Dual": "App",
                "Tuxbot Indirect Dump Dual": "System",
                "Tuxbot IPC Dual": "IPC",
                "TuxBot Trim App": "App",
                "TuxBot Trim IPC": "IPC",
                "TuxBot Trim Cache": "Cache",
                "MLOS App Metrics": "App",
                "MLOS IPC": "IPC",
                "MLOS Cache Misses": "Cache",
            },
            "x_group_map": {
                "Tuxbot App Only Dual": "TuxBot",
                "Tuxbot Indirect Dump Dual": "TuxBot",
                "Tuxbot IPC Dual": "TuxBot",
                "TuxBot Trim App": "TuxBot-trim",
                "TuxBot Trim IPC": "TuxBot-trim",
                "TuxBot Trim Cache": "TuxBot-trim",
                "MLOS App Metrics": "MLOS",
                "MLOS IPC": "IPC",
                "MLOS Cache Misses": "MLOS",
            },
            "x_group_y_shift_pts": 13.0,
            "right_trim_pts": 3.0,
        })
    for suffix in ("pdf", "png"):
        plot_summary(output_path=output / f"functional_figure_{figure}_equivalent.{suffix}", **kwargs)
    write_csv(rows, output / f"functional_figure_{figure}_equivalent.csv")


def render_figure_8(methods: dict, output: Path) -> None:
    fixed = methods["fixed"]
    results = {
        label: {
            "tuning": (float(fixed["tuning_mean"]) / float(methods[method_id]["tuning_mean"]) - 1.0) * 100.0,
            "stable": (float(fixed["stable_mean"]) / float(methods[method_id]["stable_mean"]) - 1.0) * 100.0,
        }
        for label, method_id in FIGURE_8
    }
    fs = 29
    labels = list(results)
    colors = {label: COLORS[label] for label in labels}
    x = np.arange(len(labels), dtype=float)
    width = 0.35
    fig, ax = plt.subplots(figsize=(6.912, 4.08))
    for index, label in enumerate(labels):
        ax.bar(
            x[index] - width / 2, results[label]["tuning"], width,
            color=colors[label], edgecolor="black", linewidth=0.8,
            hatch="//", zorder=4,
        )
        ax.bar(
            x[index] + width / 2, results[label]["stable"], width,
            color=colors[label], edgecolor="black", linewidth=0.8, zorder=4,
        )
    values = [results[label][phase] for label in labels for phase in ("tuning", "stable")]
    tick_lo = 25.0 * math.floor((min([0.0] + values) - 5.0) / 25.0)
    tick_hi = 25.0 * math.ceil((max([0.0] + values) + 5.0) / 25.0)
    ax.axhline(0, color=COLOR_DARK_GRAY, linestyle="--", linewidth=0.8)
    display_labels = ("TuxBot", "TuxBot-\nTrim", "Single-\nInstant", "MLOS")
    ax.set_xticks(x, display_labels, fontsize=15)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_ylabel("Improvement %", fontsize=fs - 1)
    ax.set_yticks(np.arange(tick_lo, tick_hi + 0.1, 25.0))
    ax.set_ylim(tick_lo, tick_hi)
    ax.tick_params(axis="y", labelsize=fs - 1)
    ax.grid(axis="y", alpha=0.3, linewidth=0.6)
    ax.set_axisbelow(True)
    marker_transform = ax.transData + ScaledTranslation(0.0, -9.0 / 72.0, fig.dpi_scale_trans)
    for index, label in enumerate(labels):
        ax.scatter(
            x[index], -36.0, s=380, marker=MARKERS[label], c=colors[label],
            edgecolors=COLOR_DARK_GRAY, linewidths=0.8, zorder=6,
            transform=marker_transform,
        )
    ax.legend(
        handles=[
            Patch(facecolor="white", edgecolor="black", hatch="//", label="Tuning"),
            Patch(facecolor="white", edgecolor=COLOR_DARK_GRAY, label="Stable"),
        ],
        frameon=False, fontsize=fs - 4, loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        bbox_transform=ax.transAxes + ScaledTranslation(-18.0 / 72.0, -8.0 / 72.0, fig.dpi_scale_trans),
        ncol=2, handlelength=1.2, handleheight=0.8, handletextpad=0.23,
        columnspacing=0.12, borderaxespad=0.0,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.text(0.5, 0.012, FOOTER, ha="center", va="bottom", fontsize=8, fontstyle="italic")
    fig.tight_layout(pad=0.5, rect=(0, 0.18, 1, 1))
    for suffix in ("pdf", "png"):
        fig.savefig(output / f"functional_figure_8_equivalent.{suffix}", bbox_inches="tight", pad_inches=0.0, dpi=220)
    plt.close(fig)
    with (output / "functional_figure_8_equivalent.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("method", "tuning_improvement_pct", "stable_improvement_pct"))
        writer.writeheader()
        for label in labels:
            writer.writerow({
                "method": "TuxBot-Trim" if label == "MLOS + TuxBot" else label,
                "tuning_improvement_pct": results[label]["tuning"],
                "stable_improvement_pct": results[label]["stable"],
            })


def render_figure_9(methods: dict, output: Path) -> None:
    fixed_values = [float(value) for value in methods["fixed"]["tuning_values"]]
    fixed_ref = statistics.fmean(fixed_values)
    rows = []
    for label, method_id in FIGURE_9:
        values = [float(value) for value in methods[method_id]["tuning_values"]]
        bad_rate = 100.0 * sum(value > fixed_ref for value in values) / len(values)
        variability = 100.0 * (statistics.stdev(values) if len(values) > 1 else 0.0) / abs(fixed_ref)
        rows.append({
            "method": label,
            "p50_poor_measurement_rate_pct": bad_rate,
            "p50_poor_measurement_rate_error_pct": 0.0,
            "p10_poor_measurement_rate_pct": bad_rate,
            "p10_poor_measurement_rate_error_pct": 0.0,
            "variability_pct_of_fixed": variability,
            "variability_pct_of_fixed_error_pct": 0.0,
            "n_workloads": 1,
            "run_count": 1,
            "workloads": "sysbench_oltp_rw",
        })
    colors = {"TuxBot": "#0072B2", "TuxBot-Trim": "#009E73", "MLOS": "#E69F00"}
    metric_fields = (
        "p50_poor_measurement_rate_pct",
        "p10_poor_measurement_rate_pct",
        "variability_pct_of_fixed",
    )
    x = np.arange(3, dtype=float)
    width = 0.22
    offsets = np.linspace(-width, width, num=3)
    fs = 8.5
    fig, ax = plt.subplots(figsize=(3.45, 0.99), constrained_layout=False)
    for offset, row in zip(offsets, rows):
        ax.bar(
            x + offset, [row[field] for field in metric_fields], width=width,
            color=colors[row["method"]], hatch="////", edgecolor="#333333",
            linewidth=0.7, zorder=4,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(("P50", "P10", "Variability"), fontsize=fs)
    ax.set_ylabel("Aggregate %", fontsize=fs, labelpad=5)
    ax.yaxis.label.set_transform(
        ax.yaxis.label.get_transform()
        + ScaledTranslation(0.0, -5.0 / 72.0, fig.dpi_scale_trans)
    )
    ax.set_ylim(0.0, 120.0)
    ax.set_yticks(np.arange(0.0, 120.0 + 0.1, 20.0))
    ax.tick_params(axis="y", labelsize=fs - 1, pad=1)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", alpha=0.28)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    legend_transform = ax.transAxes + ScaledTranslation(0, 4 / 72.0, fig.dpi_scale_trans)
    ax.legend(
        handles=[Patch(facecolor=colors[label], edgecolor="#333333", label=label) for label, _ in FIGURE_9],
        frameon=False, fontsize=fs - 1, loc="upper left", bbox_to_anchor=(0.0, 1.0),
        bbox_transform=legend_transform, ncol=3, handlelength=1.2,
        columnspacing=0.8, borderaxespad=0.1,
    )
    fig.subplots_adjust(left=0.14, right=0.995, top=0.95, bottom=0.22)
    fig.text(0.5, -0.18, FOOTER, ha="center", va="top", fontsize=4.8, fontstyle="italic")
    for suffix in ("pdf", "png"):
        fig.savefig(output / f"functional_figure_9_equivalent.{suffix}", bbox_inches="tight", pad_inches=0.0, dpi=300)
    plt.close(fig)
    with (output / "functional_figure_9_equivalent.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--summary", type=Path)
    source.add_argument("--results-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.results_dir is not None:
        results_dir = args.results_dir.resolve()
        from tpcc_tool import summarize
        if summarize(results_dir) != 0:
            raise ValueError(f"could not summarize {results_dir}")
        summary_path = results_dir / "sysbench_summary.json"
    else:
        summary_path = args.summary.resolve()
    summary = load(summary_path)
    methods = summary["methods"]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    render_paper_bar_style(methods, FIGURE_6, output, 6)
    render_paper_bar_style(methods, FIGURE_7, output, 7)
    render_figure_8(methods, output)
    render_figure_9(methods, output)
    print(f"FUNCTIONAL_PAPER_STYLE_PLOTS: PASS ({output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
