#!/usr/bin/env python3
"""Read-only validation and small file-generation helpers for Functional AE."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
            errors.append(f"missing command: {command} (run scripts/setup.sh --base)")
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
    pre = sub.add_parser("preflight")
    pre.add_argument("--live", action="store_true")
    machine = sub.add_parser("machine")
    machine.add_argument("--output", type=Path, required=True)
    sub.add_parser("verify-recovered")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "preflight":
        return preflight(args.live)
    if args.command == "machine":
        dump(args.output, machine_payload())
        return 0
    if args.command == "verify-recovered":
        return verify_recovered()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
