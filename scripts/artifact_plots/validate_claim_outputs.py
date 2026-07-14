#!/usr/bin/env python3
"""Validate regenerated figure CSVs against the accepted paper's numerical claims."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Iterable


TOLERANCE_PP = 0.75


def rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key, "").strip()
    return float(raw) if raw else None


def norm(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def find(data: Iterable[dict[str, str]], **fields: str) -> dict[str, str] | None:
    for row in data:
        if all(norm(row.get(key, "")) == norm(value) for key, value in fields.items()):
            return row
    return None


def close(actual: float | None, expected: float) -> bool:
    return actual is not None and math.isclose(actual, expected, abs_tol=TOLERANCE_PP)


def check_aggregate(path: Path, expected: list[tuple[str, str, int, float]]) -> list[str]:
    data = rows(path)
    errors: list[str] = []
    for phase, method, count, pct in expected:
        row = find(data, phase=phase, method=method)
        if row is None:
            errors.append(f"missing {phase}/{method}")
            continue
        actual_n = int(float(row.get("n_workloads") or 0))
        actual_pct = number(row, "aggregate_pct")
        if actual_n != count or not close(actual_pct, pct):
            errors.append(
                f"{phase}/{method}: got n={actual_n}, pct={actual_pct}; expected n={count}, pct={pct}"
            )
    return errors


def validate(output: Path, profile: str = "paper") -> tuple[list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []

    plot2_csv = output / "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.csv"
    checks: list[tuple[str, Path, list[tuple[str, str, int, float]]]] = [
        (
            "plot 6 (main aggregate)",
            output / "retry_aggregate_improvement_geomean_with_and_without_xapian.csv",
            [
                ("tuning", "TuxBot App Metrics Dual Loop", 13, 59.36),
                ("stable", "TuxBot App Metrics Dual Loop", 13, 72.49),
                ("stable", "TuxBot App Metrics Dual Loop no Catastrophic", 11, 88.07),
                ("stable", "MLOS", 13, -31.91),
                ("stable", "MLOS no Catastrophic", 11, 50.52),
            ],
        ),
        (
            "plot 7 (indirect signals)",
            plot2_csv,
            [
                ("stable", "TuxBot App Only Dual", 13, 72.49),
                ("stable", "TuxBot Indirect Dump Dual", 13, 36.31),
                ("stable", "TuxBot IPC Dual", 13, 16.19),
                ("stable", "TuxBot Indirect Dump Dual no Catastrophic", 11, 69.82),
                ("stable", "MLOS IPC no Catastrophic", 11, -24.09),
            ],
        ),
    ]
    for label, path, expected in checks:
        try:
            errors = check_aggregate(path, expected)
        except FileNotFoundError:
            errors = [f"missing output: {path.name}"]
        if errors:
            failed.append(f"{label}: " + "; ".join(errors))
        else:
            passed.append(label)

    path = output / "dual_vs_single_cost_geomean_error_bars.csv"
    try:
        data = rows(path)
        expected = {
            "TuxBot no Catastrophic": (88.07 if profile == "measured" else 87.2, 0.20, 11),
            "MLOS + TuxBot no Catastrophic": (63.9, 0.1458, 11),
            "Single-Reasoning no Catastrophic": (89.7, 0.42, 11),
            "Single-Instant no Catastrophic": (25.4, 0.12, 11),
            "MLOS no Catastrophic": (50.5, 0.00, 11),
        }
        errors = []
        for method, (stable, cost, n) in expected.items():
            row = find(data, method=method)
            if row is None:
                errors.append(f"missing {method}")
                continue
            if not close(number(row, "stable_geomean_pct"), stable):
                errors.append(f"{method} stable mismatch")
            if number(row, "total_cost_usd") is None or not math.isclose(
                number(row, "total_cost_usd") or 0.0, cost, abs_tol=0.03
            ):
                errors.append(f"{method} cost mismatch")
            if int(float(row.get("n_benchmarks") or 0)) != n:
                errors.append(f"{method} benchmark count mismatch")
        (failed if errors else passed).append(
            "plot 8 (dual/single cost)" + (": " + "; ".join(errors) if errors else "")
        )
    except FileNotFoundError:
        failed.append("plot 8 (dual/single cost): missing output")

    path = output / "retry_robustness_memory_tuxbot_mlos_1_30_aggregate.csv"
    try:
        data = rows(path)
        expected = {
            "TuxBot": (18.0, 11.9, 11.3, 12) if profile == "measured" else (19.3, 11.7, 11.0, 12),
            "TuxBot-Trim": (33.0, 32.8, 92.0, 12),
            "MLOS": (33.2, 30.9, 103.1, 12),
        }
        errors = []
        for method, (p50, p10, var, n) in expected.items():
            row = find(data, method=method)
            if row is None or not all(
                [
                    close(number(row or {}, "p50_poor_measurement_rate_pct"), p50),
                    close(number(row or {}, "p10_poor_measurement_rate_pct"), p10),
                    close(number(row or {}, "variability_pct_of_fixed"), var),
                    int(float((row or {}).get("n_workloads") or 0)) == n,
                ]
            ):
                errors.append(f"{method} mismatch")
        (failed if errors else passed).append(
            "plot 9 (robustness command summary)" + (": " + "; ".join(errors) if errors else "")
        )
    except FileNotFoundError:
        failed.append("plot 9 (robustness): missing output")

    path = output / "ablation_param_geomean.csv"
    try:
        data = rows(path)
        expected_by_count = {
            1: (0.4, -4.5, 5.0, 5.0, 6.1, 3.2),
            2: (17.5, 15.7, 7.0, 7.0, 9.0, 12.5),
            4: (168.6, 314.1, 68.8, 103.2, 68.7, 119.3),
            8: (152.9, 216.7, 46.0, 99.1, 78.2, 76.3),
            16: (106.6, 213.4, 63.6, 153.5, -7.4, 19.6),
            32: (61.6, 105.2, 34.7, 85.0, -6.3, 28.3),
            41: (86.1, 155.9, 44.3, 203.9, -11.1, 13.0),
        }
        keys = (
            "llm_tuning_pct", "llm_stable_pct",
            "llm_trim_tuning_pct", "llm_trim_stable_pct",
            "mlos_tuning_pct", "mlos_stable_pct",
        )
        errors = []
        for count, expected in expected_by_count.items():
            row = next((r for r in data if int(r["count"]) == count), None)
            if row is None or any(not close(number(row, key), value) for key, value in zip(keys, expected)):
                errors.append(f"{count}-parameter point mismatch")
        (failed if errors else passed).append(
            "plot 10 (parameter scaling)" + (": " + "; ".join(errors) if errors else "")
        )
    except FileNotFoundError:
        failed.append("plot 10 (parameter scaling): missing output")

    path = output / "rag_memory_app_system_geomean.csv"
    try:
        data = rows(path)
        expected = {
            ("tuning", "app_nomem"): 87.62,
            ("stable", "app_nomem"): 145.39,
            ("tuning", "app_top1"): 32.97,
            ("stable", "app_top1"): 41.23,
            ("tuning", "app_top3"): 155.55,
            ("stable", "app_top3"): 202.89,
            ("tuning", "sys_nomem"): 101.48,
            ("stable", "sys_nomem"): 143.07,
            ("tuning", "sys_top1"): 14.75,
            ("stable", "sys_top1"): 17.67,
            ("tuning", "sys_top3"): 143.65,
            ("stable", "sys_top3"): 164.42,
        }
        errors = []
        for (phase, method), pct in expected.items():
            row = find(data, phase=phase, method=method)
            if row is None or not close(number(row, "aggregate_pct"), pct):
                errors.append(f"{phase}/{method} mismatch")
            elif int(float(row.get("n_workloads") or 0)) != 3:
                errors.append(f"{phase}/{method} does not use three workloads")
        if not (output / "PLOT_11_REGULAR_BASELINE.txt").is_file():
            errors.append("missing regular-baseline provenance marker")
        if not (output / "rag_memory_app_system_geomean.pdf").is_file():
            errors.append("missing regenerated memory PDF")
        (failed if errors else passed).append(
            "plot 11 (memory histories; regular App-only baseline)"
            + (": " + "; ".join(errors) if errors else "")
        )
    except FileNotFoundError:
        failed.append("plot 11 (memory): missing regenerated CSV")

    path = output / "mlos_motivation_examples_combined.csv"
    try:
        data = rows(path)
        wiki = [r for r in data if r.get("panel") == "wikipedia" and int(r.get("completed_runs") or 0) > 0]
        tpcc = [r for r in data if r.get("panel") == "tpcc" and int(r.get("completed_runs") or 0) > 0]
        expected_motivation = {
            ("wikipedia", "App"): 34.374275,
            ("wikipedia", "IPC"): 73.464233,
            ("wikipedia", "Cache"): 77.916480,
            ("tpcc", "1"): 79.054090,
            ("tpcc", "2"): 76.345940,
            ("tpcc", "8"): 80.879370,
            ("tpcc", "32"): 109.894650,
        }
        values_match = True
        for (panel, label), expected in expected_motivation.items():
            row = find(data, panel=panel, label=label)
            actual = number(row or {}, "stable_mean_metric")
            if actual is None or not math.isclose(actual, expected, abs_tol=0.001):
                values_match = False
        if len(wiki) == 3 and len(tpcc) == 4 and values_match:
            passed.append("plot 12 (motivation)")
        else:
            failed.append(f"plot 12 (motivation): got {len(wiki)} Wikipedia and {len(tpcc)} TPC-C series; values_match={values_match}")
    except FileNotFoundError:
        failed.append("plot 12 (motivation): missing output")

    return passed, failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--plot", type=int, choices=range(6, 13), help="Validate only one paper plot.")
    parser.add_argument(
        "--profile",
        choices=("paper", "measured"),
        default="paper",
        help="Validate accepted-paper numbers or the disclosed measured-history regeneration.",
    )
    args = parser.parse_args()
    passed, failed = validate(args.output_dir.resolve(), args.profile)
    if args.plot is not None:
        prefix = f"plot {args.plot} "
        passed = [label for label in passed if label.startswith(prefix)]
        failed = [label for label in failed if label.startswith(prefix)]
    for label in passed:
        print(f"PASS: {label}")
    for label in failed:
        print(f"FAIL: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
