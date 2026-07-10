#!/usr/bin/env python3
"""
Plot a per-window bump chart for tuner rankings with a separate global column.

Inputs are JSON files produced by scripts/rank_tuners_vs_fixed.py --json-output.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


def parse_entry(entry: str) -> Tuple[str, str]:
    if "=" not in entry:
        raise ValueError(f"Invalid --interval entry '{entry}'. Expected LABEL=PATH.")
    label, path = entry.split("=", 1)
    label = label.strip()
    path = path.strip()
    if not label or not path:
        raise ValueError(f"Invalid --interval entry '{entry}'. Expected LABEL=PATH.")
    return label, path


def load_ranks(path: str) -> Dict[str, int]:
    p = Path(path)
    with p.open("r") as f:
        data = json.load(f)

    ranks: Dict[str, int] = {}
    for idx, row in enumerate(data.get("global_ranking", []), start=1):
        tuner = row.get("tuner_name")
        rank = row.get("rank", idx)
        if not isinstance(tuner, str):
            continue
        try:
            rank_int = int(rank)
        except (TypeError, ValueError):
            rank_int = idx
        ranks[tuner] = rank_int
    return ranks


def classify_tuner(tuner: str) -> str:
    name = tuner.lower()
    if "dual" in name:
        return "dual"
    if "flash_lite" in name or "flash-lite" in name:
        return "flash_lite"
    if "flash" in name:
        return "flash"
    return "other"


def palette_shades(cmap_name: str, count: int, low: float, high: float) -> List[Tuple[float, float, float, float]]:
    cmap = plt.get_cmap(cmap_name)
    if count <= 0:
        return []
    if count == 1:
        return [cmap((low + high) / 2.0)]
    step = (high - low) / float(count - 1)
    return [cmap(low + i * step) for i in range(count)]


def build_tuner_colors(tuners: List[str]) -> Dict[str, Tuple[float, float, float, float]]:
    group_to_tuners: Dict[str, List[str]] = {"dual": [], "flash_lite": [], "flash": [], "other": []}
    for tuner in tuners:
        group_to_tuners[classify_tuner(tuner)].append(tuner)

    colors: Dict[str, Tuple[float, float, float, float]] = {}

    # Requested mapping:
    # - flash        -> red shades
    # - flash lite   -> green shades
    # - dual         -> blue shades
    # Remaining tuners get gray shades.
    for group, cmap, low, high in [
        ("dual", "Blues", 0.45, 0.9),
        ("flash_lite", "Greens", 0.45, 0.9),
        ("flash", "Reds", 0.45, 0.9),
        ("other", "Greys", 0.45, 0.85),
    ]:
        tuners_in_group = group_to_tuners[group]
        shades = palette_shades(cmap, len(tuners_in_group), low, high)
        for tuner, color in zip(tuners_in_group, shades):
            colors[tuner] = color
    return colors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a bump chart of tuner rank per interval plus a global ranking column."
    )
    parser.add_argument(
        "--interval",
        action="append",
        default=[],
        help="Interval entry as LABEL=JSON_PATH. Repeat for each interval in order.",
    )
    parser.add_argument(
        "--global-json",
        required=True,
        help="JSON output from full-range run (global ranking column).",
    )
    parser.add_argument(
        "--global-label",
        default="Global",
        help="Label for the independent global ranking column (default: Global).",
    )
    parser.add_argument(
        "--sort-mode",
        choices=["mean", "median"],
        default="mean",
        help="Sort mode label for chart title.",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output PNG path.",
    )
    args = parser.parse_args()

    if not args.interval:
        raise SystemExit("Error: provide at least one --interval LABEL=JSON_PATH entry.")

    interval_labels: List[str] = []
    interval_rank_maps: List[Dict[str, int]] = []
    for entry in args.interval:
        label, path = parse_entry(entry)
        interval_labels.append(label)
        interval_rank_maps.append(load_ranks(path))

    global_rank_map = load_ranks(args.global_json)
    if not global_rank_map:
        raise SystemExit(f"Error: no global ranks found in {args.global_json}")

    # Base tuner order from global ranking, then any extras.
    tuners_global_order = sorted(global_rank_map.keys(), key=lambda t: global_rank_map[t])
    extra_tuners = sorted(
        set().union(*[set(m.keys()) for m in interval_rank_maps]).difference(set(tuners_global_order))
    )
    tuners = tuners_global_order + extra_tuners

    max_rank = max(
        [*global_rank_map.values(), *[r for m in interval_rank_maps for r in m.values()]],
        default=len(tuners),
    )
    if max_rank < len(tuners):
        max_rank = len(tuners)

    n_intervals = len(interval_labels)
    interval_x = list(range(n_intervals))
    global_x = n_intervals + 1  # leave one empty slot to create a visually independent column

    fig_width = max(10, 1.2 * n_intervals + 8)
    fig_height = max(6, 0.32 * len(tuners) + 3)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    tuner_colors = build_tuner_colors(tuners)

    for tuner in tuners:
        xs = interval_x + [global_x]
        ys: List[float] = []
        for rank_map in interval_rank_maps:
            rank = rank_map.get(tuner)
            ys.append(float(rank) if rank is not None else math.nan)
        g_rank = global_rank_map.get(tuner)
        ys.append(float(g_rank) if g_rank is not None else math.nan)

        color = tuner_colors.get(tuner, plt.get_cmap("Greys")(0.6))
        ax.plot(xs, ys, marker="o", linewidth=1.6, markersize=4.5, alpha=0.9, color=color, label=tuner)

    # Separate "Global" as independent column.
    ax.axvline(x=n_intervals + 0.5, color="black", linestyle="--", linewidth=1.0, alpha=0.8)
    ax.text(n_intervals + 0.5, 0.6, "Global Column", ha="center", va="bottom", fontsize=9, alpha=0.85)

    # Rank axis: 1 at top.
    ax.set_ylim(max_rank + 0.5, 0.5)
    ax.set_yticks(range(1, max_rank + 1))
    ax.set_ylabel("Rank (1 = best)")

    ax.set_xticks(interval_x + [global_x])
    ax.set_xticklabels(interval_labels + [args.global_label], rotation=0)
    ax.set_xlabel("Interval Window")
    ax.set_title(f"Tuner Ranking Bump Chart ({args.sort_mode.title()} Ranking)")
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)

    # For many tuners, legends get large but remain necessary to identify lines.
    ncol = 1 if len(tuners) <= 18 else 2 if len(tuners) <= 36 else 3
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=8, ncol=ncol)

    plt.tight_layout()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=240, bbox_inches="tight")
    plt.close(fig)

    print(f"Bump chart saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
