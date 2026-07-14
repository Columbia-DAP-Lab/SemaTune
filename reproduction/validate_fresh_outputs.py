#!/usr/bin/env python3
"""Validate the structure and finite metrics of one-rerun reproduction plots.

Fresh one-rerun results are not expected to numerically equal five-rerun paper
aggregates.  This validator therefore checks claim coverage, workload counts,
finite measurements, and generated presentation files without promising a
particular performance improvement.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any, Callable


PLOTS: dict[int, tuple[str, str]] = {
    6: ("retry_aggregate_improvement_geomean_with_and_without_xapian.csv", "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf"),
    7: ("retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.csv", "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf"),
    8: ("dual_vs_single_cost_geomean_error_bars.csv", "dual_vs_single_cost_geomean_error_bars.pdf"),
    9: ("retry_robustness_memory_tuxbot_mlos_1_30_aggregate.csv", "retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf"),
    10: ("ablation_param_geomean.csv", "ablation_param_geomean.pdf"),
    11: ("rag_memory_app_system_geomean.csv", "rag_memory_app_system_geomean.pdf"),
    12: ("mlos_motivation_examples_combined.csv", "mlos_motivation_examples_combined.pdf"),
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def finite(row: dict[str, str], *keys: str) -> bool:
    for key in keys:
        raw = row.get(key, "").strip()
        if not raw:
            continue
        try:
            if math.isfinite(float(raw)):
                return True
        except ValueError:
            continue
    return False


def has(data: list[dict[str, str]], predicate: Callable[[dict[str, str]], bool]) -> bool:
    return any(predicate(row) for row in data)


def check_plot(plot: int, data: list[dict[str, str]], output: Path) -> list[str]:
    errors: list[str] = []
    if plot == 6:
        for method in ("TuxBot App Metrics Dual Loop", "MLOS + TuxBot", "MLOS", "Bayesian", "DQN", "Q-Learning"):
            if not has(data, lambda row, method=method: row.get("method") == method and finite(row, "aggregate_pct")):
                errors.append(f"missing finite method row: {method}")
    elif plot == 7:
        for method in ("TuxBot App Only Dual", "TuxBot Indirect Dump Dual", "TuxBot IPC Dual", "MLOS App Metrics", "MLOS IPC", "MLOS Cache Misses"):
            if not has(data, lambda row, method=method: row.get("method") == method and finite(row, "aggregate_pct")):
                errors.append(f"missing finite method row: {method}")
    elif plot == 8:
        for method in ("TuxBot", "Single-Reasoning", "Single-Instant", "MLOS"):
            if not has(data, lambda row, method=method: row.get("method") == method and finite(row, "stable_geomean_pct")):
                errors.append(f"missing finite method row: {method}")
    elif plot == 9:
        for method in ("TuxBot", "TuxBot-Trim", "MLOS"):
            if not has(data, lambda row, method=method: row.get("method") == method and finite(row, "p50_poor_measurement_rate_pct")):
                errors.append(f"missing finite method row: {method}")
    elif plot == 10:
        counts = {int(row["count"]) for row in data if row.get("count", "").isdigit()}
        missing = {1, 2, 4, 8, 16, 32, 41} - counts
        if missing:
            errors.append(f"missing parameter counts: {sorted(missing)}")
        if not all(finite(row, "llm_stable_pct", "mlos_stable_pct") for row in data):
            errors.append("one or more parameter-count rows lack finite measurements")
    elif plot == 11:
        expected = {
            (phase, method)
            for phase in ("tuning", "stable")
            for method in ("app_nomem", "app_top1", "app_top3", "sys_nomem", "sys_top1", "sys_top3")
        }
        present = {
            (row.get("phase", ""), row.get("method", ""))
            for row in data
            if finite(row, "aggregate_pct")
        }
        if expected - present:
            errors.append(f"missing memory phase/method rows: {sorted(expected - present)}")
        if not (output / "PLOT_11_REGULAR_BASELINE.txt").is_file():
            errors.append("missing Plot 11 baseline provenance marker")
    elif plot == 12:
        wiki = {row.get("label") for row in data if row.get("panel") == "wikipedia" and finite(row, "stable_mean_metric")}
        tpcc = {row.get("label") for row in data if row.get("panel") == "tpcc" and finite(row, "stable_mean_metric")}
        if not {"App", "IPC", "Cache"}.issubset(wiki):
            errors.append("Wikipedia motivation panel is incomplete")
        if not {"1", "2", "8", "32"}.issubset(tpcc):
            errors.append("TPC-C motivation panel is incomplete")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--plot", type=int, choices=range(6, 13))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    selected = [args.plot] if args.plot else sorted(PLOTS)
    failures = 0
    for plot in selected:
        csv_name, pdf_name = PLOTS[plot]
        csv_path, pdf_path = output / csv_name, output / pdf_name
        errors: list[str] = []
        if not csv_path.is_file():
            errors.append(f"missing {csv_name}")
            data: list[dict[str, str]] = []
        else:
            data = read_rows(csv_path)
            if not data:
                errors.append(f"empty {csv_name}")
        if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            errors.append(f"missing or empty {pdf_name}")
        if data:
            errors.extend(check_plot(plot, data, output))
        if errors:
            failures += 1
            print(f"FAIL: fresh plot {plot}: " + "; ".join(errors))
        else:
            print(f"PASS: fresh plot {plot} has complete finite one-rerun evidence")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
