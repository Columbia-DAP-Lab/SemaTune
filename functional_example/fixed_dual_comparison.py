#!/usr/bin/env python3
"""Materialize and compare canonical 30+20 Fixed/dual-loop workload pairs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KNOBS = (
    "min_granularity_ns", "latency_ns", "cstate_max", "napi_busy_poll",
    "wakeup_granularity_ns", "migration_cost_ns", "max_perf_pct", "min_perf_pct",
)
WORKLOADS = {
    "sysbench_cpu_tput": {
        "benchmark": "sysbench_cpu", "metric": "throughput", "goal": "maximize",
        "label": "Sysbench CPU throughput",
    },
    "tpcc_hi_p99": {
        "benchmark": "tpcc", "metric": "latency_p99", "goal": "minimize",
        "label": "BenchBase TPC-C p99 latency",
    },
}
METHODS = ("fixed", "sematune_app")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_path(workload: str, method: str) -> Path:
    if workload not in WORKLOADS or method not in METHODS:
        raise ValueError(f"unsupported workload/method: {workload}/{method}")
    return ROOT / "reproduction" / "configs" / "common" / workload / f"{method}.json"


def validate(workload: str) -> int:
    from optimizer.config import SimpleConfig

    spec = WORKLOADS[workload]
    payloads = {method: load(source_path(workload, method)) for method in METHODS}
    errors: list[str] = []
    for method, payload in payloads.items():
        try:
            structural = dict(payload)
            if structural.get("tuner_type") == "llm":
                structural["llm_replay_file"] = str(
                    HERE / "traces/sysbench_sematune_dual_trace.json"
                )
            SimpleConfig.from_dict(structural).validate()
        except Exception as exc:
            errors.append(f"{method}: {exc}")
        if payload.get("benchmark") != spec["benchmark"]:
            errors.append(f"{method}: benchmark is not {spec['benchmark']}")
        if tuple(payload.get("parameters_to_tune") or ()) != KNOBS:
            errors.append(f"{method}: canonical knob order changed")
        if payload.get("window_duration") != 5:
            errors.append(f"{method}: expected a 5-second window")
    for key in ("benchmark", "parameters_to_tune", "parameter_ranges", "pin_to_cores"):
        if payloads["fixed"].get(key) != payloads["sematune_app"].get(key):
            errors.append(f"Fixed and dual-loop differ on {key}")
    dual = payloads["sematune_app"]
    if dual.get("tuner_type") != "llm" or not dual.get("dual_loop_force_final_actor_before_stable"):
        errors.append("sematune_app is not the final-Actor dual-loop configuration")
    if errors:
        raise ValueError("; ".join(errors))
    print(f"FIXED_DUAL_CONFIGS: PASS ({workload}; canonical workload; normalized budget 30+20)")
    return 0


def materialize(workload: str, method: str, output: Path, results_dir: Path) -> int:
    payload = load(source_path(workload, method))
    payload.update({
        "max_iterations": 30,
        "post_tuning_windows": 20,
        "results_dir": str(results_dir.resolve()),
        "llm_api_key": None,
        "openrouter_api_key": None,
        "llm_replay_file": None,
        "previous_run_gist": None,
        "llm_api_log_enabled": False,
    })
    dump(output, payload)
    return 0


def histories(directory: Path) -> list[Path]:
    return sorted(
        (path for path in directory.glob("*.json")
         if path.name.startswith(("optimization_history_", "dual_loop_actor_speculator_"))),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def completed_history(directory: Path) -> tuple[Path, dict[str, Any]]:
    for path in histories(directory):
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        rows = payload.get("history") or []
        iterations = {int(row.get("iteration", -1)) for row in rows}
        config = payload.get("config") or {}
        if (
            reason in {"complete", "completed", "completed_early"}
            and config.get("max_iterations") == 30
            and config.get("post_tuning_windows") == 20
            and set(range(1, 51)).issubset(iterations)
        ):
            return path, payload
    raise ValueError(f"no completed 30+20 history under {directory}")


def completed(results_dir: Path, method: str) -> int:
    completed_history(results_dir / "raw" / method)
    print(method)
    return 0


def stable_values(payload: dict[str, Any], metric: str) -> list[float]:
    values: list[float] = []
    for row in payload.get("history") or []:
        iteration = int(row.get("iteration", -1))
        if 31 <= iteration <= 50:
            value = (row.get("metrics") or {}).get(metric)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {metric} at window {iteration}: {value!r}")
            values.append(float(value))
    if len(values) != 20:
        raise ValueError(f"expected 20 stable {metric} values, found {len(values)}")
    return values


def validate_real_dual(payload: dict[str, Any]) -> dict[str, Any]:
    provider_responses = 0
    applied_responses = 0
    for row in payload.get("history") or []:
        timing = row.get("tuner_timing") or {}
        if not isinstance(timing, dict):
            continue
        candidates = [timing] + [value for value in timing.values() if isinstance(value, dict)]
        for response in candidates:
            if response.get("token_metrics"):
                provider_responses += 1
            if response.get("parameters_applied") is True:
                applied_responses += 1
    if provider_responses == 0 or applied_responses == 0:
        raise ValueError("dual-loop history lacks applied real-provider responses")
    return {"provider_responses_with_tokens": provider_responses, "applied_responses": applied_responses}


def compare(workload: str, results_dir: Path, output_dir: Path) -> int:
    spec = WORKLOADS[workload]
    fixed_path, fixed_payload = completed_history(results_dir / "raw" / "fixed")
    dual_path, dual_payload = completed_history(results_dir / "raw" / "sematune_app")
    llm_audit = validate_real_dual(dual_payload)
    restoration = {}
    for method in METHODS:
        path = results_dir / "state" / f"{method}_restoration.json"
        report = load(path)
        passed = (
            report.get("restore_status") == "PASS"
            and report.get("verify_status") == "PASS"
            and not report.get("byte_mismatches")
        )
        if not passed:
            raise ValueError(f"host restoration did not pass for {method}")
        restoration[method] = "PASS"

    fixed = stable_values(fixed_payload, spec["metric"])
    dual = stable_values(dual_payload, spec["metric"])
    fixed_mean = statistics.fmean(fixed)
    dual_mean = statistics.fmean(dual)
    if spec["goal"] == "maximize":
        improvement = (dual_mean / fixed_mean - 1.0) * 100.0
        conventional = improvement
        conventional_name = "throughput_gain_pct"
    else:
        improvement = (fixed_mean / dual_mean - 1.0) * 100.0
        conventional = (fixed_mean - dual_mean) / fixed_mean * 100.0
        conventional_name = "latency_reduction_pct"

    summary = {
        "schema_version": 1,
        "workload": workload,
        "label": spec["label"],
        "metric": spec["metric"],
        "goal": spec["goal"],
        "comparison_windows": [31, 50],
        "fixed": {
            "history": str(fixed_path), "mean": fixed_mean,
            "median": statistics.median(fixed), "samples": len(fixed),
        },
        "sematune_app": {
            "history": str(dual_path), "mean": dual_mean,
            "median": statistics.median(dual), "samples": len(dual), **llm_audit,
        },
        "paper_style_improvement_pct": improvement,
        conventional_name: conventional,
        "restoration": restoration,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dump(output_dir / "comparison.json", summary)
    with (output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("method", "metric", "mean", "median", "samples"))
        writer.writeheader()
        for method in METHODS:
            item = summary[method]
            writer.writerow({
                "method": method, "metric": spec["metric"],
                "mean": item["mean"], "median": item["median"], "samples": item["samples"],
            })
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"FIXED_DUAL_COMPARISON: PASS ({output_dir})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate"); check.add_argument("--workload", choices=WORKLOADS, required=True)
    make = sub.add_parser("materialize"); make.add_argument("--workload", choices=WORKLOADS, required=True); make.add_argument("--method", choices=METHODS, required=True); make.add_argument("--output", type=Path, required=True); make.add_argument("--results-dir", type=Path, required=True)
    done = sub.add_parser("completed"); done.add_argument("--results-dir", type=Path, required=True); done.add_argument("--method", choices=METHODS, required=True)
    report = sub.add_parser("compare"); report.add_argument("--workload", choices=WORKLOADS, required=True); report.add_argument("--results-dir", type=Path, required=True); report.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate": return validate(args.workload)
        if args.command == "materialize": return materialize(args.workload, args.method, args.output, args.results_dir)
        if args.command == "completed": return completed(args.results_dir, args.method)
        return compare(args.workload, args.results_dir, args.output_dir)
    except Exception as exc:
        print(f"FIXED_DUAL_COMPARISON: FAIL ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
