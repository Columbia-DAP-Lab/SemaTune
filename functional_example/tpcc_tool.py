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
    from optimizer.config import SimpleConfig

    errors: list[str] = []
    loaded: list[tuple[str, Any]] = []
    manifest = suite()
    tuning_windows = int(manifest["tuning_windows"])
    stable_windows = int(manifest["stable_windows"])
    window_seconds = int(manifest["window_duration_seconds"])
    for method, path in config_paths():
        try:
            payload = load(path)
            structural = copy.deepcopy(payload)
            if structural.get("tuner_type") == "llm" or structural.get("trimming_enabled"):
                trace_name = method.get("trace")
                if not trace_name:
                    raise ValueError("LLM method lacks a recorded trace")
                trace_path = HERE / trace_name
                trace = load(trace_path)
                if trace.get("method") != method["id"] or trace.get("provider_requests") != 0:
                    raise ValueError(f"{trace_path.name}: invalid method or provider-request metadata")
                if not trace.get("history"):
                    raise ValueError(f"{trace_path.name}: contains no recorded responses")
                if any(marker in json.dumps(trace) for marker in ("AIza", "sk-or-", "GEMINI_API_KEY")):
                    raise ValueError(f"{trace_path.name}: contains credential-like material")
                structural["llm_replay_file"] = str(trace_path)
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
        if (payload.get("max_iterations"), payload.get("post_tuning_windows"), payload.get("window_duration")) != (tuning_windows, stable_windows, window_seconds):
            errors.append(
                f"{path.name}: expected {tuning_windows} tuning + {stable_windows} "
                f"stable windows at {window_seconds} seconds"
            )
        is_dual = bool(payload.get("llm_actor_model") and payload.get("llm_speculator_model"))
        if is_dual and (
            payload.get("llm_actor_model") != "gemini-2.5-flash-lite"
            or payload.get("llm_speculator_model") != "gemini-2.5-flash-lite"
        ):
            errors.append(f"{path.name}: Functional Actor and Speculator must both use Gemini 2.5 Flash-Lite")
        if (payload.get("tuner_type") == "llm" or payload.get("trimming_enabled")) and payload.get("llm_model_name") != "gemini-2.5-flash-lite":
            errors.append(f"{path.name}: Functional LLM calls must use Gemini 2.5 Flash-Lite")
        serialized = json.dumps(payload)
        if "AIza" in serialized or "sk-or-" in serialized:
            errors.append(f"{path.name}: contains a credential-like value")
        if payload.get("experiment_profile"):
            errors.append(f"{path.name}: experiment_profile would override the reduced budget")

    if runtime and not errors:
        from optimizer.main_helpers import create_tuner_from_config
        from optimizer.tuners.llm import LLMTuner
        from optimizer.tuners.llm_trimming import LLMTrimmingTuner

        for method_id, config in loaded:
            tuner = None
            try:
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
    print(
        f"SYSBENCH_CONFIG_VALIDATION: PASS ({len(manifest['methods'])} methods, "
        f"{tuning_windows}+{stable_windows}, canonical knobs, Flash-Lite Functional models, "
        f"method-specific recorded traces{qualifier})"
    )
    return 0


def preflight(live: bool, real_llm: bool) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    for command in ("sysbench", "psql", "pg_isready", "taskset", "timeout", "setsid"):
        if shutil.which(command) is None:
            errors.append(f"missing command: {command} (run scripts/setup.sh --base)")
    perf_path = shutil.which("perf")
    if perf_path is None:
        warnings.append(
            "perf is unavailable; continuing without hardware counters "
            "(IPC/cache results are not performance-comparable)"
        )
    else:
        perf = subprocess.run(
            [perf_path, "--version"], capture_output=True, text=True, check=False
        )
        if perf.returncode != 0:
            warnings.append(
                "perf is unusable for the running kernel; continuing without hardware "
                "counters (IPC/cache results are not performance-comparable)"
            )
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
    for warning in warnings:
        print(f"SYSBENCH_PREFLIGHT_WARNING: {warning}")
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
    manifest = suite()
    tuning_windows = int(manifest["tuning_windows"])
    stable_windows = int(manifest["stable_windows"])
    if int(config.get("max_iterations", 0)) != tuning_windows or int(config.get("post_tuning_windows", 0)) != stable_windows:
        raise ValueError(f"history does not use the reduced {tuning_windows}+{stable_windows} schedule")
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
        if row.get("post_tuning_phase") or iteration > tuning_windows:
            stable.append(value)
        else:
            tuning.append(value)
    if len(tuning) != tuning_windows or len(stable) != stable_windows:
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
            has_recorded_proposal = "proposed_parameters" in item
            proposed = item.get("proposed_parameters")
            recorded_required = item.get("required_parameters")
            # The single-loop trimming history used to record the applied
            # parameters on the row, while its timing object retained the LLM
            # justification/token usage but omitted proposed_parameters. This
            # is still an inspectable provider response for a trimming-phase
            # row. New histories store proposed_parameters directly.
            if not has_recorded_proposal and row.get("trimming_phase") and item.get("token_metrics"):
                proposed = row.get("parameters")
            elif has_recorded_proposal and row.get("trimming_phase") and not proposed:
                if recorded_required == []:
                    checked += 1
                    if not str(item.get("justification") or row.get("llm_justification") or "").strip():
                        errors.append("LLM response lacks a justification")
                    continue
                errors.append("trimming response did not provide the required parameter configuration")
                continue
            if not proposed:
                continue
            checked += 1
            if not str(item.get("justification") or row.get("llm_justification") or "").strip():
                errors.append("LLM response lacks a justification")
            for name, value in proposed.items():
                if name in ranges and not range_ok(ranges, name, value):
                    errors.append(f"LLM response proposed out-of-range {name}={value!r}")
            if row.get("trimming_phase"):
                expected = set(recorded_required) if isinstance(recorded_required, list) else set(ranges)
                missing = sorted(expected - set(proposed))
                if missing:
                    errors.append(
                        f"trimming response omitted required parameters: {', '.join(missing)}"
                    )
    if checked == 0 and not errors:
        errors.append("history contains no inspectable LLM responses")
    return errors


def summarize(results_dir: Path) -> int:
    manifest = suite()
    tuning_windows = int(manifest["tuning_windows"])
    stable_windows = int(manifest["stable_windows"])
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
        "tuning_windows": tuning_windows,
        "stable_windows": stable_windows,
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
    print(
        f"SYSBENCH_SUMMARY: PASS ({len(manifest['methods'])} methods, "
        f"{tuning_windows} tuning + {stable_windows} stable values each)"
    )
    return 0


def completed_methods(results_dir: Path) -> int:
    """Print suite method IDs with complete, structurally valid reduced histories."""
    manifest = suite()
    tuning_windows = int(manifest["tuning_windows"])
    stable_windows = int(manifest["stable_windows"])
    for method in manifest["methods"]:
        try:
            _, payload = find_history(results_dir / "raw" / method["results_subdir"])
            tuning, stable = phase_values(payload)
            if len(tuning) == tuning_windows and len(stable) == stable_windows:
                print(method["id"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return 0


def write_manifest(output: Path, mode: str) -> int:
    manifest = suite()
    payload = {
        "schema_version": 1,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "benchmark": "sysbench_oltp_rw",
        "mode": mode,
        "methods": [method["id"] for method in manifest["methods"]],
        "schedule": {
            "tuning_windows": manifest["tuning_windows"],
            "stable_windows": manifest["stable_windows"],
            "seconds_per_window": manifest["window_duration_seconds"],
        },
        "cpu_allocation": {"controls_and_perf": "0-9", "sysbench": "10-19"},
        "provider_requests_expected": 0 if mode == "trace-replay" else "Single, App/System/IPC Dual, and App/IPC/Cache trimming model calls",
        "credential_source": None if mode == "trace-replay" else "GEMINI_API_KEY environment variable",
    }
    dump(output, payload)
    return 0


def real_llm_smoke() -> int:
    """Issue one real provider request and validate, but never print its content."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not exported")
    from optimizer.benchmark import BenchmarkMetrics
    from optimizer.config import SimpleConfig
    from optimizer.tuners.llm import LLMTuner

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
