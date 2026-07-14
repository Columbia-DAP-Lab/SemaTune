#!/usr/bin/env python3
"""Generate the measured C4 TuxBot/MLOS/TuxBot-Trim paper-style comparison."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import suite


REPRO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = REPRO_ROOT.parent
DEFAULT_MANIFEST = REPRO_ROOT / "c4_method_manifest.json"


def roots_for(
    results: Path,
    manifest: dict[str, Any],
    prefix: str,
    *,
    methods: set[str] | None = None,
) -> list[str]:
    roots: list[str] = []
    for job in manifest["jobs"]:
        if not job["id"].startswith(prefix):
            continue
        if methods is not None and job["id"].rsplit(":", 1)[-1] not in methods:
            continue
        parts = Path(job["target_results_dir"]).parts
        root = results / parts[0]
        if parts[0] == "results_params":
            root = root / parts[1]
        text = str(root)
        if text not in roots:
            roots.append(text)
    return roots


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def selected_method_knob_counts(
    manifest: dict[str, Any], knob_counts: list[int]
) -> dict[str, list[int]]:
    declared = manifest.get("method_knob_counts")
    if declared is not None:
        return {
            method: [int(value) for value in values]
            for method, values in declared.items()
        }

    selected: dict[str, set[int]] = {
        "sematune_app": set(),
        "sematune_trim_app": set(),
        "mlos_app": set(),
    }
    parameter_methods = {
        "llm_dual_app_metrics_final_actor": "sematune_app",
        "llm_trimming": "sematune_trim_app",
        "mlos": "mlos_app",
    }
    for job in manifest["jobs"]:
        job_id = job["id"]
        if job_id.startswith("common:"):
            method = job_id.rsplit(":", 1)[-1]
            if method in selected:
                selected[method].add(8)
        elif job_id.startswith("parameter:"):
            fields = job_id.split(":")
            method = parameter_methods.get(fields[-1])
            if method is not None:
                selected[method].add(int(fields[2].removesuffix("_param")))
    return {
        method: [count for count in knob_counts if count in counts]
        for method, counts in selected.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--execution-mode",
        choices=("real-provider", "trace-replay"),
        default="real-provider",
    )
    args = parser.parse_args()

    results = args.results_dir.resolve()
    output = args.output_dir.resolve()
    report_dir = args.report_dir.resolve()
    manifest = suite.load_manifest(args.manifest.resolve())
    knob_counts = [int(value) for value in manifest.get("knob_counts", [2, 8, 16, 41])]
    parameter_workloads = list(manifest.get("parameter_workloads", manifest["workloads"]))
    method_knob_counts = selected_method_knob_counts(manifest, knob_counts)
    plots = output / "plots"
    tables = output / "tables"
    plots.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    plot_path = plots / "ablation_param_geomean.pdf"
    csv_path = tables / "ablation_param_geomean.csv"
    workload_csv = tables / "ablation_param_geomean_per_workload.csv"

    param_roots = roots_for(results, manifest, "parameter:")
    fixed_roots = roots_for(results, manifest, "common:", methods={"fixed"})
    full_roots = roots_for(results, manifest, "common:", methods={"sematune_app"})
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "plot_ablation_param_aggregate.py"),
        "--results-paths", *param_roots,
        "--fallback-fixed-paths", *fixed_roots,
        "--fallback-full-results-paths", *full_roots,
        "--counts", *(str(value) for value in knob_counts),
        "--llm-trimming-dir-name",
        (
            "llm_trimming|mlos_trimming_aggressive|mlos_trimming"
            if method_knob_counts["sematune_trim_app"]
            else "__missing_sematune_trim_app__"
        ),
        "--mlos-dir-name",
        "mlos" if method_knob_counts["mlos_app"] else "__missing_mlos_app__",
        "--mlos-counts",
        *(str(value) for value in method_knob_counts["mlos_app"]),
        "--aggregate-stat", "geomean",
        "--unit", "pct",
        "--measured-trim-only",
        "--plot-output", str(plot_path),
        "--csv-output", str(csv_path),
        "--per-workload-csv-output", str(workload_csv),
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts = [int(row["count"]) for row in rows]
    if counts != knob_counts:
        raise ValueError(f"unexpected C4 counts: {counts}")
    methods = {
        "TuxBot": ("llm", "sematune_app"),
        "TuxBot-Trim": ("llm_trim", "sematune_trim_app"),
        "MLOS": ("mlos", "mlos_app"),
    }
    observations: list[dict[str, Any]] = []
    for row in rows:
        count = int(row["count"])
        for label, (key, method_id) in methods.items():
            n_tuning = int(row[f"{key}_tuning_n"])
            n_stable = int(row[f"{key}_stable_n"])
            if count not in method_knob_counts[method_id]:
                if (n_tuning, n_stable) != (0, 0):
                    raise ValueError(f"{label}/{count}: expected an intentionally empty point")
                if row[f"{key}_tuning_factor"] or row[f"{key}_stable_factor"]:
                    raise ValueError(f"{label}/{count}: empty point unexpectedly has an aggregate")
                observations.append(
                    {
                        "method": label,
                        "knob_count": count,
                        "tuning_factor": None,
                        "tuning_pct": None,
                        "stable_factor": None,
                        "stable_pct": None,
                        "workload_count": 0,
                        "status": "NOT_RUN",
                    }
                )
                continue
            tuning = float(row[f"{key}_tuning_factor"])
            stable = float(row[f"{key}_stable_factor"])
            if not all(math.isfinite(value) and value > 0 for value in (tuning, stable)):
                raise ValueError(f"{label}/{count}: invalid aggregate factor")
            expected_workloads = len(parameter_workloads)
            if (n_tuning, n_stable) != (expected_workloads, expected_workloads):
                raise ValueError(
                    f"{label}/{count}: expected all {expected_workloads} parameter workloads"
                )
            observations.append(
                {
                    "method": label,
                    "knob_count": count,
                    "tuning_factor": tuning,
                    "tuning_pct": (tuning - 1.0) * 100.0,
                    "stable_factor": stable,
                    "stable_pct": (stable - 1.0) * 100.0,
                    "workload_count": expected_workloads,
                    "status": "MEASURED",
                }
            )
    tuxbot_41 = next(
        row for row in observations if row["method"] == "TuxBot" and row["knob_count"] == 41
    )
    payload = {
        "schema_version": 1,
        "workflow": manifest["workflow"],
        "execution_mode": args.execution_mode,
        "claim": "C4",
        "claim_status": "CONSISTENT" if tuxbot_41["stable_factor"] > 1.0 else "DIVERGENT",
        "claim_comparison": tuxbot_41,
        "methods": list(methods),
        "knob_counts": knob_counts,
        "workloads": parameter_workloads,
        "fresh_repetitions": 1,
        "measured_trim_only": True,
        "omitted_points": [
            {
                "method": row["method"],
                "knob_count": row["knob_count"],
                "reason": (
                    "Excluded because high-dimensional optimizer handoff can stall."
                    if row["knob_count"] == 41
                    else "Not selected by this workflow tier."
                ),
            }
            for row in observations
            if row["status"] == "NOT_RUN"
        ],
        "observations": observations,
        "plot": str(plot_path),
        "table": str(csv_path),
        "per_workload_table": str(workload_csv),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "c4_method_report.json", payload)
    lines = [
        "# Live C4 method and parameter-count comparison",
        "",
        "This one-repeat result uses the paper Plot 10 implementation and style, restricted to "
        "2, 8, 16, and 41 knobs. Every populated value is measured; points not selected "
        "by this workflow tier remain empty. Historical Trim overrides and proxy fallbacks "
        "are disabled. TuxBot-Trim and MLOS at 41 knobs are always omitted because their "
        "high-dimensional iterations take too long.",
        "",
        f"C4 observation: **{payload['claim_status']}**.",
        "",
        "| Method | Knobs | Tuning | Stable |",
        "|---|---:|---:|---:|",
    ]
    for row in observations:
        if row["status"] == "NOT_RUN":
            lines.append(f"| {row['method']} | {row['knob_count']} | not run | not run |")
        else:
            lines.append(
                f"| {row['method']} | {row['knob_count']} | {row['tuning_factor']:.4f}× "
                f"({row['tuning_pct']:+.2f}%) | {row['stable_factor']:.4f}× "
                f"({row['stable_pct']:+.2f}%) |"
            )
    lines.extend(
        [
            "",
            "Plot: [`fresh/plots/ablation_param_geomean.pdf`](fresh/plots/ablation_param_geomean.pdf).",
            "",
            "Disaggregated inputs: [`fresh/tables/ablation_param_geomean_per_workload.csv`](fresh/tables/ablation_param_geomean_per_workload.csv).",
            "",
        ]
    )
    (report_dir / "c4_method_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"C4_METHOD_PLOT: PASS ({plot_path})")
    print(f"C4: {payload['claim_status']} ({tuxbot_41['stable_pct']:+.2f}% at 41 knobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
