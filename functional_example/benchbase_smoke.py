#!/usr/bin/env python3
"""Prepare and validate one-window Fixed BenchBase smoke runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKLOADS = {
    "wikipedia": "wikipedia_p99",
    "twitter": "twitter_p99",
    "ycsb": "ycsb_hi_p99",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_path(workload: str) -> Path:
    return ROOT / "reproduction" / "configs" / "common" / WORKLOADS[workload] / "fixed.json"


def validate_sources() -> int:
    from optimizer.config import SimpleConfig

    errors: list[str] = []
    jar = ROOT / "deps" / "benchbase" / "target" / "benchbase-postgres" / "benchbase.jar"
    if not jar.is_file():
        errors.append(f"missing BenchBase JAR: {jar} (run scripts/setup.sh --base)")
    for workload in WORKLOADS:
        path = source_path(workload)
        try:
            payload = load(path)
            SimpleConfig.from_dict(payload).validate()
        except Exception as exc:
            errors.append(f"{workload}: {exc}")
            continue
        if payload.get("benchmark") != workload:
            errors.append(f"{workload}: benchmark field differs")
        if payload.get("tuner_type") != "fixed":
            errors.append(f"{workload}: source is not Fixed")
        if payload.get("optimization_metric") != "latency_p99":
            errors.append(f"{workload}: expected latency_p99")
        config = ROOT / str(payload.get("benchbase_config_file"))
        if not config.is_file():
            errors.append(f"{workload}: missing XML config {config}")
    data = jar.parent / "data" / "twitter"
    for name in ("twitter_tweetids.txt", "twitter_user_ids.txt"):
        if not (data / name).is_file():
            errors.append(f"twitter: missing assembled trace {data / name}")
    if errors:
        raise ValueError("; ".join(errors))
    print("BENCHBASE_SMOKE_SOURCES: PASS (Wikipedia, Twitter, YCSB; Fixed; assembled traces present)")
    return 0


def materialize(workload: str, output: Path, results_dir: Path) -> int:
    payload = load(source_path(workload))
    payload.update({
        "max_iterations": 1,
        "post_tuning_windows": 0,
        "experiment_profile": None,
        "results_dir": str(results_dir.resolve()),
        "llm_api_key": None,
        "openrouter_api_key": None,
        "llm_replay_file": None,
        "previous_run_gist": None,
        "llm_api_log_enabled": False,
        "benchbase_timeout_buffer_seconds": 60,
        "benchbase_timeout_retries": 1,
    })
    dump(output, payload)
    return 0


def history_files(directory: Path) -> list[Path]:
    return sorted(
        directory.glob("optimization_history_*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def validate_result(workload: str, results_dir: Path, state_dir: Path) -> dict[str, Any]:
    for path in history_files(results_dir):
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        config = payload.get("config") or {}
        rows = [row for row in payload.get("history") or [] if int(row.get("iteration", -1)) == 1]
        if reason not in {"complete", "completed", "completed_early"} or len(rows) != 1:
            continue
        if config.get("max_iterations") != 1 or config.get("post_tuning_windows") != 0:
            continue
        metrics = rows[0].get("metrics") or {}
        required = ("throughput", "goodput", "latency_p99")
        invalid = [
            name for name in required
            if not isinstance(metrics.get(name), (int, float))
            or not math.isfinite(metrics[name]) or metrics[name] <= 0
        ]
        if invalid:
            raise ValueError(f"{workload}: invalid metrics: {', '.join(invalid)}")
        summaries = list((results_dir / "benchbase_windows" / "window_1").glob("*.summary.json"))
        if not summaries:
            raise ValueError(f"{workload}: BenchBase summary JSON is missing")
        restoration = load(state_dir / f"{workload}_restoration.json")
        if (
            restoration.get("restore_status") != "PASS"
            or restoration.get("verify_status") != "PASS"
            or restoration.get("byte_mismatches")
        ):
            raise ValueError(f"{workload}: host restoration did not pass")
        return {
            "workload": workload,
            "history": str(path),
            "benchbase_summary": str(summaries[0]),
            "throughput": float(metrics["throughput"]),
            "goodput": float(metrics["goodput"]),
            "latency_p99_ms": float(metrics["latency_p99"]),
            "latency_avg_ms": float(metrics.get("latency_avg", 0.0)),
            "restoration": "PASS",
        }
    raise ValueError(f"{workload}: no completed one-window Fixed history")


def check_one(workload: str, output_dir: Path) -> int:
    item = validate_result(workload, output_dir / "raw" / workload, output_dir / "state")
    print(json.dumps(item, indent=2, sort_keys=True))
    return 0


def summarize(output_dir: Path) -> int:
    rows = [
        validate_result(workload, output_dir / "raw" / workload, output_dir / "state")
        for workload in WORKLOADS
    ]
    report = {"schema_version": 1, "status": "PASS", "iterations_per_workload": 1, "results": rows}
    dump(output_dir / "smoke_summary.json", report)
    with (output_dir / "smoke_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ("workload", "throughput", "goodput", "latency_p99_ms", "latency_avg_ms", "restoration")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row[name] for name in fields})
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"BENCHBASE_SMOKE: PASS ({output_dir})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-sources")
    make = sub.add_parser("materialize"); make.add_argument("--workload", choices=WORKLOADS, required=True); make.add_argument("--output", type=Path, required=True); make.add_argument("--results-dir", type=Path, required=True)
    check = sub.add_parser("check"); check.add_argument("--workload", choices=WORKLOADS, required=True); check.add_argument("--output-dir", type=Path, required=True)
    summary = sub.add_parser("summarize"); summary.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-sources": return validate_sources()
        if args.command == "materialize": return materialize(args.workload, args.output, args.results_dir)
        if args.command == "check": return check_one(args.workload, args.output_dir)
        return summarize(args.output_dir)
    except Exception as exc:
        print(f"BENCHBASE_SMOKE: FAIL ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
