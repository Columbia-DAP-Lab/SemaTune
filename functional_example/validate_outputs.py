#!/usr/bin/env python3
"""Validate live and archived Functional outputs with broad AE tolerances."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path


def validate(results_dir: Path, plots_dir: Path, expected_path: Path) -> tuple[dict, int]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    report = {"schema_version": 1, "status": "PASS", "checks": []}

    def check(name: str, passed: bool, detail: str) -> None:
        report["checks"].append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            report["status"] = "FAIL"

    summary = json.loads((results_dir / "quick_summary.json").read_text(encoding="utf-8"))
    ratio = float(summary["sematune_over_fixed_stable_p99_ratio"])
    bounds = expected["validation"]
    check("quick_positive_metrics", all(float(value) > 0 for row in summary["methods"].values() for phase in ("tuning_values", "stable_values") for value in row[phase]), "all 30 measured p99 values are positive")
    check("quick_window_counts", all(len(row["tuning_values"]) == 10 and len(row["stable_values"]) == 5 for row in summary["methods"].values()), "both methods have 10 tuning and 5 stable values")
    check("quick_broad_ratio", float(bounds["stable_sematune_over_fixed_ratio_min"]) <= ratio <= float(bounds["stable_sematune_over_fixed_ratio_max"]), f"SemaTune/Fixed stable p99 ratio={ratio:.4f}; accepted range=0.5..2.0; improvement is not required")
    for suffix in ("pdf", "png", "csv"):
        path = plots_dir / f"quick_tuning_vs_stable.{suffix}"
        check(f"quick_plot_{suffix}", path.is_file() and path.stat().st_size > 0, str(path))

    archived_csv = plots_dir / "archived_headline.csv"
    rows = list(csv.DictReader(archived_csv.open(newline="", encoding="utf-8")))
    target = next((row for row in rows if row.get("phase") == "stable" and row.get("method") == "Tuxbot App Metrics Dual Loop"), None)
    actual = float(target["aggregate_pct"]) if target else math.nan
    oracle = expected["archived_headline"]
    check("archived_headline_numeric", target is not None and int(float(target["n_workloads"])) == int(oracle["workloads"]) and math.isclose(actual, float(oracle["stable_tuxbot_app_metrics_dual_loop_pct"]), abs_tol=float(oracle["absolute_tolerance_percentage_points"])), f"stable headline={actual:.3f}% over {target.get('n_workloads') if target else '?'} workloads")
    check("archived_headline_pdf", (plots_dir / "archived_headline.pdf").is_file(), str(plots_dir / "archived_headline.pdf"))
    return report, 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--plots-dir", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()
    try:
        report, status = validate(args.results_dir.resolve(), args.plots_dir.resolve(), args.expected.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        report, status = {"schema_version": 1, "status": "FAIL", "checks": [], "error": str(exc)}, 1
    output = args.plots_dir.resolve() / "validation.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"FUNCTIONAL_VALIDATION: {report['status']} ({output})")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
