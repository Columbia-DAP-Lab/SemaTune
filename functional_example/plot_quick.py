#!/usr/bin/env python3
"""Plot the quick Fixed/SemaTune tuning and stable phases."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import functional_tool


COLORS = {"Fixed": "#7F8C8D", "SemaTune": "#0072B2"}


def values_from_results(results_dir: Path) -> tuple[list[dict[str, float | str]], dict]:
    summary = functional_tool.summarize(results_dir)
    rows: list[dict[str, float | str]] = []
    for method in ("fixed", "sematune"):
        data = summary["methods"][method]
        rows.append({"method": data["label"], "phase": "tuning", "mean_latency_p99_ms": data["tuning_mean"], "std_latency_p99_ms": data["tuning_std"], "windows": 10})
        rows.append({"method": data["label"], "phase": "stable", "mean_latency_p99_ms": data["stable_mean"], "std_latency_p99_ms": data["stable_std"], "windows": 5})
    return rows, summary


def values_from_expected(expected: Path) -> tuple[list[dict[str, float | str]], dict]:
    payload = json.loads(expected.read_text(encoding="utf-8"))
    rows: list[dict[str, float | str]] = []
    for method, values in payload["reference_plot_values"].items():
        for phase, windows in (("tuning", 10), ("stable", 5)):
            rows.append({"method": method, "phase": phase, "mean_latency_p99_ms": values[f"{phase}_mean"], "std_latency_p99_ms": values[f"{phase}_std"], "windows": windows})
    return rows, payload


def render(rows: list[dict[str, float | str]], output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("method", "phase", "mean_latency_p99_ms", "std_latency_p99_ms", "windows"))
        writer.writeheader()
        writer.writerows(rows)

    methods = ("Fixed", "SemaTune")
    by_key = {(str(row["method"]), str(row["phase"])): row for row in rows}
    x = np.arange(len(methods), dtype=float)
    width = 0.34
    fig, ax = plt.subplots(figsize=(6.4, 4.25))
    for index, method in enumerate(methods):
        tuning = by_key[(method, "tuning")]
        stable = by_key[(method, "stable")]
        ax.bar(x[index] - width / 2, float(tuning["mean_latency_p99_ms"]), width, yerr=float(tuning["std_latency_p99_ms"]), capsize=4, color=COLORS[method], edgecolor="#333333", linewidth=0.9)
        ax.bar(x[index] + width / 2, float(stable["mean_latency_p99_ms"]), width, yerr=float(stable["std_latency_p99_ms"]), capsize=4, color=COLORS[method], edgecolor="#333333", linewidth=0.9, hatch="////")
    ax.set_xticks(x, methods)
    ax.set_ylabel("p99 latency (ms; lower is better)")
    ax.set_title("Sysbench OLTP-RW Functional example")
    ax.grid(axis="y", alpha=0.25, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(handles=[Patch(facecolor="white", edgecolor="#333333", label="Tuning (10 windows)"), Patch(facecolor="white", edgecolor="#333333", hatch="////", label="Stable (5 windows)")], loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, frameon=True)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--stem", default="quick_tuning_vs_stable")
    args = parser.parse_args()
    try:
        if args.expected:
            rows, _ = values_from_expected(args.expected.resolve())
        elif args.results_dir:
            rows, _ = values_from_results(args.results_dir.resolve())
        else:
            raise ValueError("provide --results-dir or --expected")
        render(rows, args.output_dir.resolve(), args.stem)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"QUICK_PLOT: FAIL ({exc})", file=sys.stderr)
        return 1
    print(f"QUICK_PLOT: PASS ({args.output_dir.resolve() / (args.stem + '.pdf')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
