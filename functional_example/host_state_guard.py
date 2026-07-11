#!/usr/bin/env python3
"""Capture, restore, and byte-verify every host control the demo can affect."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any


EXACT_PATHS = (
    Path("/sys/kernel/debug/sched/min_granularity_ns"),
    Path("/sys/kernel/debug/sched/latency_ns"),
    Path("/sys/kernel/debug/sched/wakeup_granularity_ns"),
    Path("/sys/kernel/debug/sched/migration_cost_ns"),
    Path("/proc/sys/net/core/busy_poll"),
    Path("/sys/devices/system/cpu/intel_pstate/max_perf_pct"),
    Path("/sys/devices/system/cpu/intel_pstate/min_perf_pct"),
    Path("/sys/devices/system/cpu/intel_pstate/no_turbo"),
)
CPUIDLE_RE = re.compile(r"^/sys/devices/system/cpu/cpu[0-9]+/cpuidle/state[0-9]+/disable$")
IRQ_RE = re.compile(r"^/proc/irq/[0-9]+/smp_affinity_list$")
CPUFREQ_RE = re.compile(
    r"^/sys/devices/system/cpu/cpufreq/policy[0-9]+/"
    r"(scaling_governor|scaling_min_freq|scaling_max_freq|energy_performance_preference)$"
)


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("host_state_guard.py must run as root (use functional_example/run.sh)")


def nic_irq_paths() -> list[Path]:
    try:
        devices = {path.name for path in Path("/sys/class/net").iterdir() if path.name != "lo"}
        lines = Path("/proc/interrupts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    paths: set[Path] = set()
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        irq = fields[0].rstrip(":")
        if irq.isdigit() and any(device in line for device in devices):
            path = Path(f"/proc/irq/{irq}/smp_affinity_list")
            if path.is_file():
                paths.add(path)
    return sorted(paths, key=lambda path: int(path.parts[3]))


def cpufreq_paths() -> list[Path]:
    result: list[Path] = []
    for policy in sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy[0-9]*")):
        for name in ("scaling_governor", "scaling_min_freq", "scaling_max_freq", "energy_performance_preference"):
            path = policy / name
            if path.is_file():
                result.append(path)
    return result


def allowed_paths() -> list[Path]:
    cpuidle = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpuidle/state[0-9]*/disable"))
    return [*EXACT_PATHS, *cpufreq_paths(), *cpuidle, *nic_irq_paths()]


def path_is_allowed(path: Path) -> bool:
    text = str(path)
    return path in EXACT_PATHS or bool(CPUIDLE_RE.fullmatch(text)) or bool(IRQ_RE.fullmatch(text)) or bool(CPUFREQ_RE.fullmatch(text))


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def write_bytes(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        written = os.write(fd, value)
        if written != len(value):
            raise OSError(f"short write ({written}/{len(value)} bytes)")
    finally:
        os.close(fd)


def canonical(files: list[dict[str, str]]) -> bytes:
    return json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")


def capture(output: Path) -> int:
    require_root()
    paths = allowed_paths()
    missing = [str(path) for path in EXACT_PATHS if not path.is_file()]
    if not any(CPUIDLE_RE.fullmatch(str(path)) for path in paths):
        missing.append("/sys/devices/system/cpu/cpu*/cpuidle/state*/disable")
    if missing:
        print("HOST_STATE_CAPTURE: FAIL; required controls are unavailable:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 1
    files: list[dict[str, str]] = []
    try:
        for path in paths:
            raw = read_bytes(path)
            files.append({
                "path": str(path),
                "value_base64": base64.b64encode(raw).decode("ascii"),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    except OSError as exc:
        print(f"HOST_STATE_CAPTURE: FAIL: {exc}", file=sys.stderr)
        return 1
    payload: dict[str, Any] = {
        "schema_version": 2,
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": platform.node(),
        "kernel": platform.release(),
        "files_sha256": hashlib.sha256(canonical(files)).hexdigest(),
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"HOST_STATE_CAPTURE: PASS ({len(files)} byte-exact controls -> {output})")
    return 0


def load_snapshot(snapshot: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    files = payload.get("files")
    if payload.get("schema_version") != 2 or not isinstance(files, list):
        raise ValueError("unsupported or malformed host-state snapshot")
    if hashlib.sha256(canonical(files)).hexdigest() != payload.get("files_sha256"):
        raise ValueError("host-state snapshot manifest checksum mismatch")
    for item in files:
        if set(item) != {"path", "value_base64", "sha256"}:
            raise ValueError("malformed file entry")
        path = Path(item["path"])
        if not path_is_allowed(path):
            raise ValueError(f"snapshot contains non-whitelisted path: {path}")
        raw = base64.b64decode(item["value_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError(f"value checksum mismatch for {path}")
    return payload, files


def compare(files: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    for item in files:
        path = Path(item["path"])
        try:
            actual = read_bytes(path)
        except OSError as exc:
            errors.append(f"{path}: unreadable ({exc})")
            continue
        expected = base64.b64decode(item["value_base64"])
        if actual != expected:
            errors.append(
                f"{path}: expected sha256 {item['sha256']}, got {hashlib.sha256(actual).hexdigest()}"
            )
    return errors


def prepare_ranges(files: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    pstate_min = Path("/sys/devices/system/cpu/intel_pstate/min_perf_pct")
    if any(Path(item["path"]) == pstate_min for item in files):
        try:
            write_bytes(pstate_min, b"0\n")
        except OSError as exc:
            errors.append(f"{pstate_min}: could not lower minimum before restore ({exc})")
    policies = {Path(item["path"]).parent for item in files if item["path"].endswith("/scaling_min_freq")}
    for policy in policies:
        try:
            write_bytes(policy / "scaling_min_freq", read_bytes(policy / "cpuinfo_min_freq"))
        except OSError as exc:
            errors.append(f"{policy}: could not lower scaling minimum before restore ({exc})")
    return errors


def restore_order(item: dict[str, str]) -> tuple[int, str]:
    path = item["path"]
    if path.endswith("/max_perf_pct") or path.endswith("/scaling_max_freq"):
        return (0, path)
    if path.endswith("/min_perf_pct") or path.endswith("/scaling_min_freq"):
        return (1, path)
    if path.endswith("/scaling_governor") or path.endswith("/energy_performance_preference"):
        return (2, path)
    return (3, path)


def write_report(report: Path | None, payload: dict[str, Any]) -> None:
    if report is None:
        return
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def restore(snapshot: Path, report: Path | None) -> int:
    require_root()
    result: dict[str, Any] = {
        "schema_version": 1,
        "attempted_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "snapshot": str(snapshot),
        "restore_status": "FAIL",
        "verify_status": "FAIL",
        "write_errors": [],
        "byte_mismatches": [],
    }
    try:
        source, files = load_snapshot(snapshot)
        result["captured_files_sha256"] = source["files_sha256"]
        result["control_count"] = len(files)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result["write_errors"] = [str(exc)]
        write_report(report, result)
        print(f"HOST_STATE_RESTORE: FAIL: {exc}", file=sys.stderr)
        print("HOST_STATE_VERIFY: FAIL", file=sys.stderr)
        return 1
    errors = prepare_ranges(files)
    for item in sorted(files, key=restore_order):
        try:
            write_bytes(Path(item["path"]), base64.b64decode(item["value_base64"]))
        except OSError as exc:
            errors.append(f"{item['path']}: {exc}")
    mismatches = compare(files)
    result["write_errors"] = errors
    result["byte_mismatches"] = mismatches
    result["restore_status"] = "PASS" if not errors else "FAIL"
    result["verify_status"] = "PASS" if not mismatches else "FAIL"
    result["verified_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_report(report, result)
    if errors:
        print("HOST_STATE_RESTORE: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    else:
        print(f"HOST_STATE_RESTORE: PASS ({len(files)} controls written)")
    if mismatches:
        print("HOST_STATE_VERIFY: FAIL", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
    else:
        print(f"HOST_STATE_VERIFY: PASS ({len(files)} controls byte-identical)")
    return 0 if not errors and not mismatches else 2


def verify(snapshot: Path) -> int:
    require_root()
    try:
        _, files = load_snapshot(snapshot)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"HOST_STATE_VERIFY: FAIL: {exc}", file=sys.stderr)
        return 1
    mismatches = compare(files)
    if mismatches:
        print("HOST_STATE_VERIFY: FAIL", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return 2
    print(f"HOST_STATE_VERIFY: PASS ({len(files)} controls byte-identical)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--output", type=Path, required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--snapshot", type=Path, required=True)
    restore_parser.add_argument("--report", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--snapshot", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "capture":
        return capture(args.output)
    if args.command == "restore":
        return restore(args.snapshot, args.report)
    if args.command == "verify":
        return verify(args.snapshot)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
