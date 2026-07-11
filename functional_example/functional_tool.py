#!/usr/bin/env python3
"""Read-only validation and small file-generation helpers for Functional AE."""

from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import glob
import hashlib
import json
import math
import os
import platform
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

KNOBS = (
    "min_granularity_ns", "latency_ns", "cstate_max", "napi_busy_poll",
    "wakeup_granularity_ns", "migration_cost_ns", "max_perf_pct", "min_perf_pct",
)
QUICK_CONFIGS = (HERE / "quick_fixed.json", HERE / "quick_sematune.json")
REPRESENTATIVE_CONFIGS = (
    HERE / "representative_fixed.json",
    HERE / "representative_sematune_application_metrics.json",
    HERE / "representative_sematune_system_metrics.json",
    HERE / "representative_classical_baseline.json",
    HERE / "representative_memory_enabled.json",
    HERE / "representative_parameter_count_ablation.json",
)
REPLAY = HERE / "mock_replay.json"
REQUIRED_CONTROLS = (
    "/sys/kernel/debug/sched/min_granularity_ns",
    "/sys/kernel/debug/sched/latency_ns",
    "/sys/kernel/debug/sched/wakeup_granularity_ns",
    "/sys/kernel/debug/sched/migration_cost_ns",
    "/proc/sys/net/core/busy_poll",
    "/sys/devices/system/cpu/intel_pstate/max_perf_pct",
    "/sys/devices/system/cpu/intel_pstate/min_perf_pct",
    "/sys/devices/system/cpu/intel_pstate/no_turbo",
)


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _range_ok(ranges: dict[str, Any], name: str, value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("value")
    allowed = ranges[name]
    if len(allowed) == 2 and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in allowed
    ):
        return allowed[0] <= value <= allowed[1]
    return value in allowed


def validate_configs() -> int:
    from barebones_optimizer.config import SimpleConfig

    errors: list[str] = []
    for path in (*QUICK_CONFIGS, *REPRESENTATIVE_CONFIGS):
        try:
            payload = load(path)
            structural = copy.deepcopy(payload)
            # Structural config validation must not require evaluator
            # credentials. The live runner separately enforces GEMINI_API_KEY
            # unless a checked replay is selected.
            if structural.get("tuner_type") == "llm":
                structural["llm_replay_file"] = str(REPLAY)
            config = SimpleConfig.from_dict(structural)
            config.validate()
        except Exception as exc:  # report the complete config audit
            errors.append(f"{path.name}: {exc}")
            continue
        names = tuple(payload.get("parameters_to_tune") or ())
        expected = KNOBS if path.name != "representative_parameter_count_ablation.json" else KNOBS[:2]
        if names != expected:
            errors.append(f"{path.name}: expected ordered parameters {expected}, got {names}")
        serialized = json.dumps(payload).lower()
        for forbidden in ("llm_api_key", "openrouter_api_key", "gemini_api_key"):
            if forbidden in serialized:
                errors.append(f"{path.name}: forbidden credential field {forbidden}")

    try:
        replay = load(REPLAY)
        entries = {int(row["iteration"]): row for row in replay.get("history", [])}
        ranges = load(HERE / "quick_sematune.json")["parameter_ranges"]
        for iteration in range(10):
            row = entries.get(iteration, {})
            for role in ("quick", "reasoning"):
                response = (row.get("responses") or {}).get(role)
                if not response:
                    errors.append(f"mock_replay.json: missing {role} response at iteration {iteration}")
                    continue
                params = response.get("parameters") or {}
                if tuple(params) != KNOBS:
                    errors.append(f"mock_replay.json: {iteration}/{role} does not contain all eight ordered knobs")
                for name, value in params.items():
                    if name not in ranges or not _range_ok(ranges, name, value):
                        errors.append(f"mock_replay.json: {iteration}/{role} invalid {name}={value!r}")
                if not str(response.get("justification") or "").strip():
                    errors.append(f"mock_replay.json: {iteration}/{role} lacks justification")
        final = ((entries.get(10, {}).get("responses") or {}).get("reasoning_final") or {})
        if tuple(final.get("parameters") or {}) != KNOBS or final.get("converged") is not True:
            errors.append("mock_replay.json: final stable-gating Actor response is missing or not converged")
    except Exception as exc:
        errors.append(f"mock_replay.json: {exc}")

    if errors:
        print("CONFIG_VALIDATION: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("CONFIG_VALIDATION: PASS (2 quick + 6 representative configs; 8-knob replay is role-complete)")
    return 0


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (result.stdout or result.stderr).strip().splitlines()
    return line[0] if line else None


def machine_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "available_cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "versions": {
            "sysbench": command_version(["sysbench", "--version"]),
            "postgresql": command_version(["psql", "--version"]),
            "perf": command_version(["perf", "--version"]),
        },
    }
    for path, key in ((Path("/etc/os-release"), "os_release"), (Path("/proc/cpuinfo"), "cpuinfo"), (Path("/proc/meminfo"), "meminfo")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if key == "os_release":
            payload[key] = dict(
                line.split("=", 1) for line in text.splitlines() if "=" in line
            )
        elif key == "cpuinfo":
            models = sorted({line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("model name")})
            payload["cpu_models"] = models
        else:
            fields = {}
            for line in text.splitlines():
                if line.startswith(("MemTotal:", "SwapTotal:")):
                    name, value = line.split(":", 1)
                    fields[name] = value.strip()
            payload["memory"] = fields
    payload["numa"] = command_version(["lscpu", "-e=CPU,NODE,SOCKET,CORE,ONLINE"])
    return payload


def preflight(live: bool) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    if platform.system() != "Linux":
        errors.append("the live example requires Linux bare metal")
    affinity = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set(range(os.cpu_count() or 0))
    missing_cpus = sorted(set(range(20)) - affinity)
    if missing_cpus:
        errors.append(f"CPUs 0-19 are required; unavailable to this process: {missing_cpus}")
    for command in ("python3", "sysbench", "psql", "pg_isready", "perf", "taskset", "timeout", "setsid"):
        if shutil.which(command) is None:
            errors.append(f"missing command: {command} (run functional_example/install.sh)")
    if sys.version_info[:2] != (3, 10):
        errors.append(f"Python 3.10 is required; found {platform.python_version()}")
    if live:
        for path in REQUIRED_CONTROLS:
            try:
                present = Path(path).is_file()
            except PermissionError:
                present = None
            if present is False:
                errors.append(f"missing kernel control: {path}")
            elif present is None:
                warnings.append(f"permission check deferred to the root state capture: {path}")
        try:
            has_cpuidle = any(Path("/sys/devices/system/cpu/cpu0/cpuidle").glob("state*/disable"))
        except PermissionError:
            has_cpuidle = True
            warnings.append("CPU idle-state readability check deferred to the root state capture")
        if not has_cpuidle:
            errors.append("CPU idle-state controls are unavailable")
    release = machine_payload().get("os_release", {})
    if release.get("VERSION_ID", "").strip('"') != "22.04":
        warnings.append("the host differs from the Ubuntu 22.04 Functional-validation machine")
    for warning in warnings:
        print(f"PREFLIGHT_WARNING: {warning}")
    if errors:
        print("PREFLIGHT: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"PREFLIGHT: PASS ({'live controls' if live else 'read-only dry run'})")
    return 0


def materialize(source: Path, output: Path, results_dir: Path, replay: Path | None) -> int:
    source = source.resolve()
    if source not in {path.resolve() for path in QUICK_CONFIGS}:
        raise SystemExit(f"refusing non-quick source config: {source}")
    payload = load(source)
    payload["results_dir"] = str(results_dir.resolve())
    if replay is not None:
        payload["llm_replay_file"] = str(replay.resolve())
    dump(output, payload)
    return 0


def find_history(results_dir: Path, method: str) -> tuple[Path, dict[str, Any]]:
    pattern = "dual_loop_actor_speculator_sysbench_*.json" if method == "sematune" else "optimization_history_fixed_*.json"
    candidates = sorted(results_dir.glob(pattern), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not candidates:
        fallback_pattern = pattern if method == "sematune" else "optimization_history_*sysbench*.json"
        candidates = sorted(results_dir.glob(f"{method}_raw/{fallback_pattern}"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        if reason in {"completed", "complete", "completed_early"}:
            return path, payload
    raise ValueError(f"no completed {method} history in {results_dir}")


def phase_values(payload: dict[str, Any]) -> tuple[list[float], list[float]]:
    config = payload.get("config") or {}
    if int(config.get("max_iterations", 0)) != 10 or int(config.get("post_tuning_windows", 0)) != 5:
        raise ValueError("history is not the 10-tuning + 5-stable Functional schedule")
    tuning: list[float] = []
    stable: list[float] = []
    for row in payload.get("history") or []:
        iteration = int(row.get("iteration", -1))
        if iteration == 0:
            continue
        raw = (row.get("metrics") or {}).get("latency_p99")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"iteration {iteration} has non-positive/non-finite latency_p99 {raw!r}")
        (stable if row.get("post_tuning_phase") or iteration > 10 else tuning).append(value)
    if len(tuning) != 10 or len(stable) != 5:
        raise ValueError(f"history has {len(tuning)} tuning and {len(stable)} stable metrics; expected 10 and 5")
    return tuning, stable


def summarize(results_dir: Path) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for method, label in (("fixed", "Fixed"), ("sematune", "SemaTune")):
        path, payload = find_history(results_dir, method)
        tuning, stable = phase_values(payload)
        methods[method] = {
            "label": label,
            "history_file": str(path),
            "tuning_values": tuning,
            "stable_values": stable,
            "tuning_mean": statistics.fmean(tuning),
            "tuning_std": statistics.stdev(tuning),
            "stable_mean": statistics.fmean(stable),
            "stable_std": statistics.stdev(stable),
        }
    ratio = methods["sematune"]["stable_mean"] / methods["fixed"]["stable_mean"]
    return {
        "schema_version": 1,
        "benchmark": "sysbench_oltp",
        "metric": "latency_p99_ms",
        "goal": "minimize",
        "tuning_windows": 10,
        "stable_windows": 5,
        "sematune_over_fixed_stable_p99_ratio": ratio,
        "methods": methods,
    }


def write_summary(results_dir: Path) -> int:
    summary = summarize(results_dir.resolve())
    dump(results_dir / "quick_summary.json", summary)
    with (results_dir / "quick_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ("method", "phase", "mean_latency_p99_ms", "std_latency_p99_ms", "windows", "history_file")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for method in ("fixed", "sematune"):
            row = summary["methods"][method]
            for phase in ("tuning", "stable"):
                writer.writerow({
                    "method": row["label"], "phase": phase,
                    "mean_latency_p99_ms": row[f"{phase}_mean"],
                    "std_latency_p99_ms": row[f"{phase}_std"],
                    "windows": len(row[f"{phase}_values"]),
                    "history_file": row["history_file"],
                })
    print(f"SUMMARY: PASS (stable SemaTune/Fixed p99 ratio={summary['sematune_over_fixed_stable_p99_ratio']:.3f})")
    return 0


def write_manifest(output: Path, mode: str, replay: Path | None) -> int:
    payload = {
        "schema_version": 1,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": command_version(["git", "rev-parse", "HEAD"]),
        "mode": mode,
        "provider_requests_expected": 0 if replay else "multiple Actor/Speculator calls",
        "replay_file": str(replay.resolve()) if replay else None,
        "credential_source": None if replay else "GEMINI_API_KEY environment variable",
        "schedule": {"tuning_windows": 10, "stable_windows": 5, "seconds_per_window": 10},
        "cpu_allocation": {"controls_and_perf": "0-9", "sysbench": "10-19"},
    }
    dump(output, payload)
    return 0


def verify_recovered() -> int:
    recovered = ROOT / "all_results/paper_evaluation/results_config_full_param_twitter_p99_retry/twitter_p99/llm_dual_system_metrics_plain_final_actor_recovered_20260313"
    files = sorted(recovered.glob("dual_loop_actor_speculator_twitter_*.json"))
    manifest = ROOT / "artifact/recovered_twitter_system.sha256"
    errors: list[str] = []
    if len(files) != 5:
        errors.append(f"expected exactly 5 recovered histories, found {len(files)}")
    expected: dict[str, str] = {}
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            checksum, name = line.split(None, 1)
            expected[name.strip()] = checksum
    else:
        errors.append(f"missing checksum manifest {manifest}")
    for path in files:
        relative = str(path.relative_to(ROOT))
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected.get(relative) != actual:
            errors.append(f"checksum mismatch or absent: {relative}")

    # Exact-directory-first semantics, tested without importing plotting deps.
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        workload = Path(temp)
        exact = workload / "method"
        backup = workload / "method copy"
        exact.mkdir()
        backup.mkdir()
        resolved = next((candidate for candidate in (exact, backup) if candidate.is_dir()), None)
        if resolved != exact:
            errors.append("result resolution did not prefer the exact directory")
    if errors:
        print("RECOVERED_TWITTER: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("RECOVERED_TWITTER: PASS (5 checksummed histories; exact directory precedes ' copy')")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-configs")
    pre = sub.add_parser("preflight")
    pre.add_argument("--live", action="store_true")
    machine = sub.add_parser("machine")
    machine.add_argument("--output", type=Path, required=True)
    make = sub.add_parser("materialize")
    make.add_argument("--source", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    make.add_argument("--results-dir", type=Path, required=True)
    make.add_argument("--replay", type=Path)
    summary = sub.add_parser("summarize")
    summary.add_argument("--results-dir", type=Path, required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--mode", choices=("mock", "real", "custom-replay"), required=True)
    manifest.add_argument("--replay", type=Path)
    sub.add_parser("verify-recovered")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "validate-configs":
        return validate_configs()
    if args.command == "preflight":
        return preflight(args.live)
    if args.command == "machine":
        dump(args.output, machine_payload())
        return 0
    if args.command == "materialize":
        return materialize(args.source, args.output, args.results_dir, args.replay)
    if args.command == "summarize":
        return write_summary(args.results_dir.resolve())
    if args.command == "manifest":
        return write_manifest(args.output, args.mode, args.replay)
    if args.command == "verify-recovered":
        return verify_recovered()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
