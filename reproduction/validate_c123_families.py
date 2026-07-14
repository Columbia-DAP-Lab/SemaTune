#!/usr/bin/env python3
"""Validate provider provenance and end-to-end C1--C3 family outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import suite


REPRO_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = REPRO_ROOT / "c123_family_manifest.json"
BASELINE_WORKLOADS = {"silo_hi_p99", "tpcc_hi_p99", "sysbench_oltp_rw_hi_p99"}
PLOT_FILES = {
    "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf": (
        564.1350915211,
        226.18,
        24,
    ),
    "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf": (
        566.0,
        213.646144,
        36,
    ),
}
TABLE_COUNTS = {
    "c123_phase_metrics.csv": 88,
    "c123_improvement_factors.csv": 66,
    "c123_aggregate_improvement.csv": 12,
    "c123_claim_summary.csv": 6,
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object: {path}")
    return value


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def pdf_page_size(path: Path) -> tuple[float, float] | None:
    match = re.search(
        rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\]",
        path.read_bytes(),
    )
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def is_allowed_leading_empty_window(
    row: dict[str, Any], *, value: float, index: int, maximum: int
) -> bool:
    """Recognize the bounded TailBench startup interval before its first request."""

    metrics = row.get("metrics", {})
    return (
        value == 0
        and index < maximum
        and not bool(row.get("post_tuning_phase"))
        and isinstance(metrics, dict)
        and metrics.get("request_count") == 0
        and metrics.get("intervals_aggregated") == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Validate only the three provider-backed jobs already present before extension.",
    )
    args = parser.parse_args()

    output = args.output_dir.resolve()
    manifest = suite.load_manifest(args.manifest.resolve())
    raw = output / "fresh" / "raw"
    status_path = output / "fresh" / "run_status.json"
    errors: list[str] = []
    if not raw.is_dir():
        errors.append(f"missing raw results directory: {raw}")
    if not status_path.is_file():
        errors.append(f"missing run status: {status_path}")
        status: dict[str, Any] = {}
    else:
        try:
            status = load_json(status_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            status = {}

    selected = [
        job
        for job in manifest["jobs"]
        if not args.baseline_only or job["id"].split(":")[1] in BASELINE_WORKLOADS
    ]
    for job in selected:
        history_path = suite.completed_result(raw / job["target_results_dir"], job)
        if history_path is None:
            errors.append(f"{job['id']}: missing strict complete history")
            continue
        try:
            history = load_json(history_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{job['id']}: {exc}")
            continue
        config = history.get("config")
        if not isinstance(config, dict):
            errors.append(f"{job['id']}: history lacks materialized config")
            continue
        if suite.is_llm_config(config) and config.get("llm_replay_file"):
            errors.append(f"{job['id']}: provider validation rejects llm_replay_file")
        metric = str(job["completion"]["optimization_metric"])
        rows = [
            row
            for row in history.get("history", [])
            if isinstance(row, dict) and int(row.get("iteration", 0)) > 0
        ]
        max_leading_empty = int(job["completion"].get("max_leading_empty_windows", 0))
        for index, row in enumerate(rows):
            try:
                value = float(row["metrics"][metric])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{job['id']}: missing numeric {metric}")
                break
            if not math.isfinite(value):
                errors.append(f"{job['id']}: non-finite {metric}")
                break
            if value > 0:
                continue
            if not is_allowed_leading_empty_window(
                row,
                value=value,
                index=index,
                maximum=max_leading_empty,
            ):
                errors.append(
                    f"{job['id']}: non-positive {metric} outside the declared "
                    "leading empty-window allowance"
                )
                break
        job_status = status.get("jobs", {}).get(job["id"], {})
        if job_status.get("replay_trace"):
            errors.append(f"{job['id']}: run status contains replay metadata")
        if job_status and job_status.get("status") not in {"passed", "reused_existing"}:
            errors.append(f"{job['id']}: unexpected run status {job_status.get('status')!r}")

    if status.get("execution_mode") == "trace-replay":
        errors.append("run status identifies trace-replay measurements")
    if not args.baseline_only:
        if status.get("execution_mode") != "real-provider":
            errors.append("final run status must identify real-provider execution")
        if status.get("failures") != 0:
            errors.append(f"final run status has failures={status.get('failures')!r}")
        replay_dir = output / "fresh" / "replay_traces"
        if replay_dir.is_dir() and any(replay_dir.iterdir()):
            errors.append("provider-only output unexpectedly contains replay traces")
        restoration = output / "fresh" / "restoration_report.json"
        if not restoration.is_file():
            errors.append("missing host restoration report")
        else:
            value = load_json(restoration)
            if value.get("restore_status") != "PASS" or value.get("verify_status") != "PASS":
                errors.append("host restoration or byte verification did not pass")
            if value.get("byte_mismatches") or value.get("write_errors"):
                errors.append("host restoration report contains mismatches/errors")

        for filename, (expected_width, expected_height, expected_csv_rows) in PLOT_FILES.items():
            path = output / "fresh" / "plots" / filename
            if not path.is_file() or path.stat().st_size < 1000:
                errors.append(f"missing or empty plot: {path}")
            elif path.read_bytes()[:5] != b"%PDF-":
                errors.append(f"plot is not a PDF: {path}")
            else:
                page_size = pdf_page_size(path)
                if page_size is None:
                    errors.append(f"plot lacks a readable PDF MediaBox: {path}")
                elif not (
                    math.isclose(page_size[0], expected_width, abs_tol=0.01)
                    and math.isclose(page_size[1], expected_height, abs_tol=0.01)
                ):
                    errors.append(
                        f"{filename}: expected paper page {expected_width:.3f}x"
                        f"{expected_height:.3f} pt, found {page_size[0]:.3f}x"
                        f"{page_size[1]:.3f} pt"
                    )
            csv_path = path.with_suffix(".csv")
            if not csv_path.is_file():
                errors.append(f"missing plot input table: {csv_path}")
            else:
                plot_rows = csv_rows(csv_path)
                if len(plot_rows) != expected_csv_rows:
                    errors.append(
                        f"{csv_path.name}: expected {expected_csv_rows} paper-grid rows, "
                        f"found {len(plot_rows)}"
                    )
                if not any(int(row.get("n_workloads") or 0) == 0 for row in plot_rows):
                    errors.append(f"{csv_path.name}: missing-method slots were not preserved")
        for filename, expected in TABLE_COUNTS.items():
            path = output / "fresh" / "tables" / filename
            if not path.is_file():
                errors.append(f"missing table: {path}")
                continue
            rows = csv_rows(path)
            if len(rows) != expected:
                errors.append(f"{filename}: expected {expected} rows, found {len(rows)}")
            for row in rows:
                numeric = row.get("fresh_factor") or row.get("aggregate_factor") or row.get("improvement_factor") or row.get("mean_metric")
                if numeric:
                    try:
                        if not math.isfinite(float(numeric)):
                            raise ValueError
                    except ValueError:
                        errors.append(f"{filename}: non-finite numeric value")
                        break
        report_path = output / "c123_family_report.json"
        if not report_path.is_file():
            errors.append("missing C1-C3 family report")
        else:
            report = load_json(report_path)
            claims = report.get("fresh_claims", [])
            expected = {(claim, cohort) for claim in ("C1", "C2", "C3") for cohort in ("all", "excluding_xapian")}
            present = {(row.get("claim"), row.get("cohort")) for row in claims if isinstance(row, dict)}
            if present != expected:
                errors.append(f"claim/cohort report coverage mismatch: {sorted(present)}")
            if report.get("c4_evaluated") is not False:
                errors.append("C1-C3 report must explicitly mark C4 unevaluated")
            if report.get("claim_plot_map") != {
                "C1": "plot_6",
                "C2": "plot_6",
                "C3": "plot_7",
            }:
                errors.append("claim-to-paper-plot mapping is missing or incorrect")

    if errors:
        for error in errors:
            print(f"C123_VALIDATION_ERROR: {error}")
        return 1
    scope = "provider baseline" if args.baseline_only else "end-to-end provider C1-C3 family run"
    print(f"C123_FAMILY_VALIDATION: PASS ({scope}; {len(selected)} configurations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
