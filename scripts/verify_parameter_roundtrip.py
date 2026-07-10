#!/usr/bin/env python3
"""
Capture and verify OS parameter restoration across optimization runs.

Default scope is the "new parameters" that rely on live system defaults.
This script is intended for initial setup on a new system:
1) snapshot current values
2) run optimization
3) verify post-run values match the snapshot

Examples:
  # 1) Capture baseline
  sudo python3 scripts/verify_parameter_roundtrip.py snapshot \
      --output results/new_params_baseline.json

  # 2) Verify later
  sudo python3 scripts/verify_parameter_roundtrip.py verify \
      --snapshot results/new_params_baseline.json

  # 3) One-shot run + verify (command after --)
  sudo python3 scripts/verify_parameter_roundtrip.py run-and-verify \
      --output results/new_params_baseline.json \
      -- python3 -m src.barebones_optimizer.main -c config/your_config.json
"""

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
from typing import Dict, Set, Tuple, Any


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from barebones_optimizer.parameter_manager import (  # noqa: E402
    NEW_PARAMETER_BOOL_NAMES,
    NEW_PARAMETER_SYSCTL_KEYS,
)


def _read_sysctl(key: str) -> str:
    result = subprocess.run(
        ["sysctl", "-n", key],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _parse_value(param_name: str, raw: str) -> Any:
    if param_name in NEW_PARAMETER_BOOL_NAMES:
        return raw == "1"
    if param_name == "tcp_congestion_control":
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


def _collect_values(params: Set[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    values: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for param_name in sorted(params):
        key = NEW_PARAMETER_SYSCTL_KEYS.get(param_name)
        if not key:
            errors[param_name] = "unsupported parameter for this script"
            continue
        try:
            raw = _read_sysctl(key)
            values[param_name] = _parse_value(param_name, raw)
        except Exception as e:
            errors[param_name] = str(e)
    return values, errors


def _get_params_from_config(config_path: str) -> Set[str]:
    with open(config_path, "r") as f:
        config = json.load(f)
    params = set(config.get("parameter_ranges", {}).keys())
    params.update(set(config.get("fixed_parameters", {}).keys()))
    return params


def _resolve_params(args: argparse.Namespace) -> Set[str]:
    params = set(NEW_PARAMETER_SYSCTL_KEYS.keys())
    if args.config:
        params = _get_params_from_config(args.config)
    if args.parameters:
        params = set(p.strip() for p in args.parameters.split(",") if p.strip())
    # Keep only the script-supported parameter set.
    return params & set(NEW_PARAMETER_SYSCTL_KEYS.keys())


def _write_snapshot(path: str, params: Set[str], values: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "captured_at_utc": dt.datetime.utcnow().isoformat() + "Z",
        "host": platform.node(),
        "kernel": platform.release(),
        "parameters": {k: values[k] for k in sorted(params) if k in values},
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def _compare(expected: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mismatches: Dict[str, Dict[str, Any]] = {}
    all_keys = set(expected.keys()) | set(actual.keys())
    for k in sorted(all_keys):
        if expected.get(k) != actual.get(k):
            mismatches[k] = {"expected": expected.get(k), "actual": actual.get(k)}
    return mismatches


def cmd_snapshot(args: argparse.Namespace) -> int:
    params = _resolve_params(args)
    values, errors = _collect_values(params)
    if errors:
        print("WARN: some parameters could not be read during snapshot:")
        for p, e in sorted(errors.items()):
            print(f"  - {p}: {e}")
    _write_snapshot(args.output, params, values)
    print(f"Snapshot written: {args.output}")
    print(f"Captured parameters: {sorted(values.keys())}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    with open(args.snapshot, "r") as f:
        snapshot = json.load(f)
    snap_params = snapshot.get("parameters", {})

    params = set(snap_params.keys())
    if args.config:
        params &= _get_params_from_config(args.config)
    if args.parameters:
        params &= set(p.strip() for p in args.parameters.split(",") if p.strip())

    expected = {k: snap_params[k] for k in sorted(params)}
    actual, errors = _collect_values(set(expected.keys()))
    mismatches = _compare(expected, actual)

    if errors:
        print("WARN: some parameters could not be read during verify:")
        for p, e in sorted(errors.items()):
            print(f"  - {p}: {e}")

    if mismatches:
        print("FAIL: parameter mismatches detected:")
        for p, diff in mismatches.items():
            print(f"  - {p}: expected={diff['expected']} actual={diff['actual']}")
        return 2

    print("PASS: all checked parameters match snapshot values.")
    return 0


def cmd_run_and_verify(args: argparse.Namespace) -> int:
    params = _resolve_params(args)
    before, errors_before = _collect_values(params)
    if errors_before:
        print("WARN: some parameters could not be read in pre-run snapshot:")
        for p, e in sorted(errors_before.items()):
            print(f"  - {p}: {e}")

    if args.output:
        _write_snapshot(args.output, params, before)
        print(f"Pre-run snapshot written: {args.output}")

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("ERROR: no command provided for run-and-verify")
        return 1

    print(f"Running command: {' '.join(command)}")
    run_rc = subprocess.run(command).returncode
    print(f"Command exit code: {run_rc}")

    after, errors_after = _collect_values(params)
    if errors_after:
        print("WARN: some parameters could not be read in post-run snapshot:")
        for p, e in sorted(errors_after.items()):
            print(f"  - {p}: {e}")

    mismatches = _compare(before, after)
    if mismatches:
        print("FAIL: post-run values differ from pre-run snapshot:")
        for p, diff in mismatches.items():
            print(f"  - {p}: before={diff['expected']} after={diff['actual']}")
        return 2 if run_rc == 0 else run_rc

    print("PASS: post-run values match pre-run snapshot.")
    return run_rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot and verify OS parameter restoration for new parameters."
    )
    sub = parser.add_subparsers(dest="command_name", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        help="Optional config JSON: limit checks to parameter_ranges + fixed_parameters",
    )
    common.add_argument(
        "--parameters",
        help="Optional comma-separated parameter names (overrides config/default set)",
    )

    p_snapshot = sub.add_parser("snapshot", parents=[common], help="Capture parameter snapshot")
    p_snapshot.add_argument("--output", required=True, help="Output snapshot JSON path")
    p_snapshot.set_defaults(func=cmd_snapshot)

    p_verify = sub.add_parser("verify", parents=[common], help="Verify current values against snapshot")
    p_verify.add_argument("--snapshot", required=True, help="Snapshot JSON path")
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser(
        "run-and-verify",
        parents=[common],
        help="Capture pre-run values, execute command, verify post-run values",
    )
    p_run.add_argument(
        "--output",
        help="Optional path to write pre-run snapshot JSON",
    )
    p_run.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute (pass after --)",
    )
    p_run.set_defaults(func=cmd_run_and_verify)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

