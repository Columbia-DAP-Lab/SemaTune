#!/usr/bin/env python3
"""Validation, materialization, and summarization for the Functional suite."""

from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SUITE_PATH = HERE / "sysbench_suite.json"
TRACE_PATH = HERE / "sysbench_trace_replay.json"
KNOBS = (
    "min_granularity_ns", "latency_ns", "cstate_max", "napi_busy_poll",
    "wakeup_granularity_ns", "migration_cost_ns", "max_perf_pct", "min_perf_pct",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def suite() -> dict[str, Any]:
    return load(SUITE_PATH)


def config_paths() -> list[tuple[dict[str, Any], Path]]:
    return [(method, HERE / method["config"]) for method in suite()["methods"]]


def range_ok(ranges: dict[str, Any], name: str, value: Any) -> bool:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    allowed = ranges[name]
    if len(allowed) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in allowed):
        return allowed[0] <= value <= allowed[1]
    return value in allowed


def validate(runtime: bool = False) -> int:
    from barebones_optimizer.config import SimpleConfig

    errors: list[str] = []
    loaded: list[tuple[str, Any]] = []
    for method, path in config_paths():
        try:
            payload = load(path)
            structural = copy.deepcopy(payload)
            if structural.get("tuner_type") == "llm" or structural.get("trimming_enabled"):
                structural["llm_replay_file"] = str(TRACE_PATH)
            config = SimpleConfig.from_dict(structural)
            config.validate()
            loaded.append((method["id"], config))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if payload.get("benchmark") != "sysbench_oltp":
            errors.append(f"{path.name}: benchmark must be sysbench_oltp")
        if tuple(payload.get("parameters_to_tune") or ()) != KNOBS:
            errors.append(f"{path.name}: expected the canonical ordered eight knobs")
        if (payload.get("max_iterations"), payload.get("post_tuning_windows"), payload.get("window_duration")) != (10, 5, 10):
            errors.append(f"{path.name}: expected 10 tuning + 5 stable windows at 10 seconds")
        serialized = json.dumps(payload)
        if "AIza" in serialized or "sk-or-" in serialized:
            errors.append(f"{path.name}: contains a credential-like value")
        if payload.get("experiment_profile"):
            errors.append(f"{path.name}: experiment_profile would override the reduced budget")

    try:
        replay = load(TRACE_PATH)
        entries = {int(row["iteration"]): row for row in replay["history"]}
        ranges = load(HERE / "sysbench_sematune_dual.json")["parameter_ranges"]
        for iteration in range(10):
            for role in ("quick", "reasoning"):
                response = (entries.get(iteration, {}).get("responses") or {}).get(role)
                if not response:
                    errors.append(f"trace: missing {role} response at iteration {iteration}")
                    continue
                params = response.get("parameters") or {}
                if set(params) != set(KNOBS) or len(params) != len(KNOBS):
                    errors.append(f"trace: {iteration}/{role} does not contain all eight knobs")
                for name, value in params.items():
                    if name not in ranges or not range_ok(ranges, name, value):
                        errors.append(f"trace: invalid {iteration}/{role} {name}={value!r}")
                if not str(response.get("justification") or "").strip():
                    errors.append(f"trace: missing justification for {iteration}/{role}")
        final = ((entries.get(10, {}).get("responses") or {}).get("reasoning_final") or {})
        final_params = final.get("parameters") or {}
        if final.get("converged") is not True or set(final_params) != set(KNOBS) or len(final_params) != len(KNOBS):
            errors.append("trace: missing converged final Actor response")
        for iteration in range(5):
            response = (entries[iteration]["responses"]["reasoning"])
            if "suggested_ranges" not in response or "eliminated_params" not in response:
                errors.append(f"trace: trimming action fields missing at iteration {iteration}")
    except Exception as exc:
        errors.append(f"trace: {exc}")

    if runtime and not errors:
        from barebones_optimizer.main_helpers import create_tuner_from_config
        from barebones_optimizer.tuners.llm import LLMTuner
        from barebones_optimizer.tuners.llm_trimming import LLMTrimmingTuner

        for method_id, config in loaded:
            tuner = None
            try:
                config.llm_replay_file = str(TRACE_PATH)
                if getattr(config, "_explicit_dual_loop", False):
                    LLMTuner(config, agent_type="quick")
                    tuner = LLMTuner(config, agent_type="reasoning")
                else:
                    tuner = create_tuner_from_config(config)
                if config.trimming_enabled:
                    LLMTrimmingTuner(config, agent_type="single")
            except Exception as exc:
                errors.append(f"{method_id}: tuner initialization failed: {exc}")
            finally:
                cleanup = getattr(tuner, "cleanup", None)
                if callable(cleanup):
                    cleanup()

    if errors:
        print("SYSBENCH_CONFIG_VALIDATION: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    qualifier = " + runtime tuner imports" if runtime else ""
    print(f"SYSBENCH_CONFIG_VALIDATION: PASS (8 methods, canonical knobs, role-complete trace{qualifier})")
    return 0


def preflight(live: bool, real_llm: bool) -> int:
    errors: list[str] = []
    for command in ("sysbench", "psql", "pg_isready", "perf", "taskset", "timeout", "setsid"):
        if shutil.which(command) is None:
            errors.append(f"missing command: {command} (run functional_example/install.sh)")
    affinity = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set(range(os.cpu_count() or 0))
    missing = sorted(set(range(20)) - affinity)
    if missing:
        errors.append(f"CPUs 0-19 are required; unavailable: {missing}")
    if real_llm and not os.environ.get("GEMINI_API_KEY"):
        errors.append("real LLM mode requires the caller's exported GEMINI_API_KEY")
    if live:
        for name in ("SEMATUNE_SYSBENCH_HOST", "SEMATUNE_SYSBENCH_PORT", "SEMATUNE_SYSBENCH_USER", "SEMATUNE_SYSBENCH_PASSWORD", "SEMATUNE_SYSBENCH_DB"):
            if not os.environ.get(name):
                errors.append(f"missing site environment variable: {name}")
    if errors:
        print("SYSBENCH_PREFLIGHT: FAIL", file=sys.stderr)
        for error in dict.fromkeys(errors):
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"SYSBENCH_PREFLIGHT: PASS ({'live' if live else 'read-only'}; {'real Gemini' if real_llm else 'trace replay'})")
    return 0


def materialize(source: Path, output: Path, results_dir: Path, replay: Path | None) -> int:
    allowed = {path.resolve() for _, path in config_paths()}
    allowed.add(
        (ROOT / "reproduction/configs/common/sysbench_oltp_rw_hi_p99/sematune_app.json").resolve()
    )
    source = source.resolve()
    if source not in allowed:
        raise ValueError(f"refusing config outside the Sysbench Functional suite: {source}")
    payload = load(source)
    payload["results_dir"] = str(results_dir.resolve())
    payload["llm_replay_file"] = str(replay.resolve()) if replay else None
    dump(output, payload)
    return 0


def find_history(directory: Path) -> tuple[Path, dict[str, Any]]:
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        if reason in {"complete", "completed", "completed_early"}:
            return path, payload
    raise ValueError(f"no completed optimizer history under {directory}")


def phase_values(payload: dict[str, Any]) -> tuple[list[float], list[float]]:
    config = payload.get("config") or {}
    if int(config.get("max_iterations", 0)) != 10 or int(config.get("post_tuning_windows", 0)) != 5:
        raise ValueError("history does not use the reduced 10+5 schedule")
    tuning: list[float] = []
    stable: list[float] = []
    for position, row in enumerate(payload.get("history") or [], start=1):
        iteration = int(row.get("iteration", position))
        if iteration == 0 or row.get("pre_tuning_default_config"):
            continue
        metrics = row.get("metrics") or {}
        raw = metrics.get("latency_p99", metrics.get("p_99_latency"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if "latency_p99" not in metrics:
            value /= 1000.0
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"iteration {iteration} has invalid p99 latency {raw!r}")
        if row.get("post_tuning_phase") or iteration > 10:
            stable.append(value)
        else:
            tuning.append(value)
    if len(tuning) != 10 or len(stable) != 5:
        raise ValueError(f"history contains {len(tuning)} tuning and {len(stable)} stable values")
    return tuning, stable


def validate_llm_trace(payload: dict[str, Any], ranges: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    checked = 0
    for row in payload.get("history") or []:
        timing = row.get("tuner_timing")
        role_keys = {"quick", "reasoning", "reasoning_final", "speculator", "actor"}
        nested_roles = (
            [value for key, value in timing.items() if key in role_keys and isinstance(value, dict)]
            if isinstance(timing, dict) else []
        )
        timings = nested_roles or [timing]
        for item in timings:
            if not isinstance(item, dict):
                continue
            proposed = item.get("proposed_parameters")
            # The single-loop trimming history used to record the applied
            # parameters on the row, while its timing object retained the LLM
            # justification/token usage but omitted proposed_parameters. This
            # is still an inspectable provider response for a trimming-phase
            # row. New histories store proposed_parameters directly.
            if not proposed and row.get("trimming_phase") and item.get("token_metrics"):
                proposed = row.get("parameters")
            if not proposed:
                continue
            checked += 1
            if not str(item.get("justification") or row.get("llm_justification") or "").strip():
                errors.append("LLM response lacks a justification")
            for name, value in proposed.items():
                if name in ranges and not range_ok(ranges, name, value):
                    errors.append(f"LLM response proposed out-of-range {name}={value!r}")
    if checked == 0:
        errors.append("history contains no inspectable LLM responses")
    return errors


def summarize(results_dir: Path) -> int:
    manifest = suite()
    methods: dict[str, Any] = {}
    errors: list[str] = []
    for method in manifest["methods"]:
        try:
            history_path, payload = find_history(results_dir / "raw" / method["results_subdir"])
            tuning, stable = phase_values(payload)
            config = load(HERE / method["config"])
            if method["id"].startswith("sematune"):
                errors.extend(f"{method['id']}: {error}" for error in validate_llm_trace(payload, config["parameter_ranges"]))
            methods[method["id"]] = {
                "label": method["label"],
                "color": method["color"],
                "history_file": str(history_path),
                "tuning_values": tuning,
                "stable_values": stable,
                "tuning_mean": statistics.fmean(tuning),
                "tuning_std": statistics.stdev(tuning),
                "stable_mean": statistics.fmean(stable),
                "stable_std": statistics.stdev(stable),
            }
        except Exception as exc:
            errors.append(f"{method['id']}: {exc}")
    if errors:
        raise ValueError("; ".join(errors))
    output = {
        "schema_version": 1,
        "benchmark": "sysbench_oltp_rw",
        "metric": "latency_p99_ms",
        "goal": "minimize",
        "tuning_windows": 10,
        "stable_windows": 5,
        "methods": methods,
    }
    dump(results_dir / "sysbench_summary.json", output)
    with (results_dir / "sysbench_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("method", "phase", "mean_latency_p99_ms", "std_latency_p99_ms", "windows", "history_file"))
        writer.writeheader()
        for method in manifest["methods"]:
            row = methods[method["id"]]
            for phase in ("tuning", "stable"):
                writer.writerow({
                    "method": row["label"], "phase": phase,
                    "mean_latency_p99_ms": row[f"{phase}_mean"],
                    "std_latency_p99_ms": row[f"{phase}_std"],
                    "windows": len(row[f"{phase}_values"]),
                    "history_file": row["history_file"],
                })
    print("SYSBENCH_SUMMARY: PASS (8 methods, 10 tuning + 5 stable values each)")
    return 0


def completed_methods(results_dir: Path) -> int:
    """Print suite method IDs with complete, structurally valid 10+5 histories."""
    for method in suite()["methods"]:
        try:
            _, payload = find_history(results_dir / "raw" / method["results_subdir"])
            tuning, stable = phase_values(payload)
            if len(tuning) == 10 and len(stable) == 5:
                print(method["id"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return 0


def write_manifest(output: Path, mode: str) -> int:
    payload = {
        "schema_version": 1,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "benchmark": "sysbench_oltp_rw",
        "mode": mode,
        "methods": [method["id"] for method in suite()["methods"]],
        "schedule": {"tuning_windows": 10, "stable_windows": 5, "seconds_per_window": 10},
        "cpu_allocation": {"controls_and_perf": "0-9", "sysbench": "10-19"},
        "provider_requests_expected": 0 if mode == "trace-replay" else "Single, Dual, and trimming model calls",
        "credential_source": None if mode == "trace-replay" else "GEMINI_API_KEY environment variable",
    }
    dump(output, payload)
    return 0


def real_llm_smoke() -> int:
    """Issue one real provider request and validate, but never print its content."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not exported")
    from barebones_optimizer.benchmark import BenchmarkMetrics
    from barebones_optimizer.config import SimpleConfig
    from barebones_optimizer.tuners.llm import LLMTuner

    payload = load(HERE / "sysbench_sematune_single.json")
    payload["llm_replay_file"] = None
    payload["llm_api_log_enabled"] = False
    config = SimpleConfig.from_dict(payload)
    config.validate()
    current: dict[str, Any] = {}
    for name, allowed in config.parameter_ranges.items():
        if len(allowed) == 2 and all(isinstance(value, (int, float)) for value in allowed):
            current[name] = int((allowed[0] + allowed[1]) / 2)
        else:
            current[name] = allowed[0]
    tuner = LLMTuner(config, agent_type="single")
    response = tuner.suggest_parameters(
        BenchmarkMetrics(
            throughput=1250.0,
            latency_avg=18.0,
            latency_p95=42.0,
            extra_metrics={"latency_p99": 75.0},
        ),
        current,
        iteration=0,
        best_reward=75.0,
        aggregation_interval_s=5.0,
    )
    parameters = response.parameters or {}
    if set(parameters) != set(KNOBS):
        raise ValueError("provider response did not contain exactly the canonical eight knobs")
    for name, value in parameters.items():
        if not range_ok(config.parameter_ranges, name, value):
            raise ValueError(f"provider response placed {name} outside its configured range")
    if not str(response.justification or "").strip():
        raise ValueError("provider response omitted its justification")
    print(
        "SYSBENCH_REAL_LLM_SMOKE: PASS "
        f"(model={tuner.model_name}; eight range-valid knobs; non-empty justification; credentials not recorded)"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--runtime", action="store_true")
    pre = sub.add_parser("preflight")
    pre.add_argument("--live", action="store_true")
    pre.add_argument("--real-llm", action="store_true")
    make = sub.add_parser("materialize")
    make.add_argument("--source", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    make.add_argument("--results-dir", type=Path, required=True)
    make.add_argument("--replay", type=Path)
    summary = sub.add_parser("summarize")
    summary.add_argument("--results-dir", type=Path, required=True)
    completed = sub.add_parser("completed-methods")
    completed.add_argument("--results-dir", type=Path, required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--mode", choices=("trace-replay", "real-llm"), required=True)
    sub.add_parser("real-llm-smoke")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "validate":
            return validate(args.runtime)
        if args.command == "preflight":
            return preflight(args.live, args.real_llm)
        if args.command == "materialize":
            return materialize(args.source, args.output, args.results_dir, args.replay)
        if args.command == "summarize":
            return summarize(args.results_dir.resolve())
        if args.command == "completed-methods":
            return completed_methods(args.results_dir.resolve())
        if args.command == "manifest":
            return write_manifest(args.output, args.mode)
        if args.command == "real-llm-smoke":
            return real_llm_smoke()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"SYSBENCH_TOOL: FAIL ({exc})", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
