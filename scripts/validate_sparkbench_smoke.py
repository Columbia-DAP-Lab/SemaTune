#!/usr/bin/env python3
"""Materialize and validate a one-iteration canonical SparkBench Fixed smoke."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "reproduction" / "configs" / "common" / "dcperf_spark_tput" / "fixed.json"
sys.path.insert(0, str(ROOT / "src"))


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def materialize(output: Path, results_dir: Path, max_runtime: int) -> int:
    payload = load(SOURCE)
    expected = {
        "benchmark": "dcperf_spark",
        "optimization_metric": "queries_per_hour",
        "optimization_goal": "maximize",
        "tuner_type": "fixed",
        "pin_to_cores": "0-9",
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"canonical SparkBench {key} changed: {payload.get(key)!r}")
    payload.update({
        "dcperf_path": str((ROOT / "deps" / "DCPerf").resolve()),
        "dcperf_benchmark_name": "spark_standalone_local",
        "dcperf_max_runtime": max_runtime,
        "max_iterations": 1,
        "post_tuning_windows": 0,
        "experiment_profile": None,
        "results_dir": str(results_dir.resolve()),
        "llm_api_key": None,
        "openrouter_api_key": None,
        "llm_replay_file": None,
        "previous_run_gist": None,
        "llm_api_log_enabled": False,
    })
    from optimizer.config import SimpleConfig
    SimpleConfig.from_dict(payload).validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SPARKBENCH_SMOKE_CONFIG: PASS ({output}; canonical Fixed, one iteration)")
    return 0


def history_files(directory: Path) -> list[Path]:
    return sorted(
        directory.glob("optimization_history_dcperf_spark_*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def validate(
    history_dir: Path,
    log: Path,
    restoration_path: Path,
    dcperf_work: Path,
    java_version: Path,
    output: Path,
) -> int:
    selected = None
    for path in history_files(history_dir):
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        config = payload.get("config") or {}
        rows = [row for row in payload.get("history") or [] if int(row.get("iteration", -1)) == 1]
        if (
            reason in {"complete", "completed", "completed_early"}
            and config.get("max_iterations") == 1
            and config.get("post_tuning_windows") == 0
            and config.get("benchmark") == "dcperf_spark"
            and config.get("dcperf_benchmark_name") == "spark_standalone_local"
            and len(rows) == 1
        ):
            selected = (path, config, rows[0].get("metrics") or {})
            break
    if selected is None:
        raise ValueError("no completed one-iteration SparkBench Fixed history")
    history, config, metrics = selected
    queries_per_hour = metrics.get("queries_per_hour")
    execution_time = metrics.get("execution_time_test_93586")
    if not isinstance(queries_per_hour, (int, float)) or not math.isfinite(queries_per_hour) or queries_per_hour <= 0:
        raise ValueError("SparkBench history has no positive queries_per_hour")
    if not isinstance(execution_time, (int, float)) or not math.isfinite(execution_time) or execution_time <= 0:
        raise ValueError("SparkBench history has no positive execution_time_test_93586")
    version_text = java_version.read_text(encoding="utf-8", errors="replace")
    if 'version "1.8.0_' not in version_text:
        raise ValueError(f"SparkBench did not use Java 8:\n{version_text}")
    stage_logs = [
        dcperf_work / "create_db.log",
        dcperf_work / "create_tables.log",
        dcperf_work / "release_test_93586.log",
    ]
    failure_markers = ("Exception in thread", "Caused by:", "Traceback (most recent call last)")
    for stage_log in stage_logs:
        if not stage_log.is_file() or stage_log.stat().st_size == 0:
            raise ValueError(f"SparkBench stage log is missing or empty: {stage_log}")
        stage_text = stage_log.read_text(encoding="utf-8", errors="replace")
        marker = next((item for item in failure_markers if item in stage_text), None)
        if marker:
            raise ValueError(f"SparkBench stage failed ({marker}): {stage_log}")
    results_text = (dcperf_work / "results.txt").read_text(
        encoding="utf-8", errors="replace"
    )
    expected_cores = config.get("dcperf_worker_cores")
    if f"worker-cores : {expected_cores}" not in results_text:
        raise ValueError(
            f"SparkBench did not use the configured {expected_cores} worker cores"
        )
    restoration = load(restoration_path)
    if (
        restoration.get("restore_status") != "PASS"
        or restoration.get("verify_status") != "PASS"
        or restoration.get("byte_mismatches")
    ):
        raise ValueError("host restoration did not pass")
    result = {
        "schema_version": 1,
        "status": "PASS",
        "benchmark": "dcperf_spark",
        "dcperf_job": "spark_standalone_local",
        "query": "release_test_93586",
        "iterations": 1,
        "worker_cores_configured": config.get("dcperf_worker_cores"),
        "pin_to_cores": config.get("pin_to_cores"),
        "queries_per_hour": float(queries_per_hour),
        "execution_time_seconds": float(execution_time),
        "score": metrics.get("score"),
        "history": str(history.resolve()),
        "log": str(log.resolve()),
        "restoration": "PASS",
        "java": "OpenJDK 8",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"SPARKBENCH_SMOKE: PASS ({output})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("materialize")
    make.add_argument("--output", type=Path, required=True)
    make.add_argument("--results-dir", type=Path, required=True)
    make.add_argument("--max-runtime", type=int, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--history-dir", type=Path, required=True)
    check.add_argument("--log", type=Path, required=True)
    check.add_argument("--restoration", type=Path, required=True)
    check.add_argument("--dcperf-work", type=Path, required=True)
    check.add_argument("--java-version", type=Path, required=True)
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "materialize":
        return materialize(args.output, args.results_dir, args.max_runtime)
    return validate(
        args.history_dir,
        args.log,
        args.restoration,
        args.dcperf_work,
        args.java_version,
        args.output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
