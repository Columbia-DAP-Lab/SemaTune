#!/usr/bin/env python3
"""Materialize, preflight, and validate the reduced Mutilate Functional run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SOURCE = ROOT / "reproduction/configs/common/mutilate_high/sematune_app.json"
KNOBS = (
    "min_granularity_ns",
    "latency_ns",
    "cstate_max",
    "napi_busy_poll",
    "wakeup_granularity_ns",
    "migration_cost_ns",
    "max_perf_pct",
    "min_perf_pct",
)
TUNING_WINDOWS = 3
STABLE_WINDOWS = 2
WINDOW_SECONDS = 5
EXPECTED_ITERATIONS = tuple(range(TUNING_WINDOWS + STABLE_WINDOWS + 1))
FUNCTIONAL_MODEL = "gemini-2.5-flash-lite"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("benchmark") != "mutilate":
        errors.append("canonical source is not the Mutilate benchmark")
    if payload.get("tuner_type") != "llm":
        errors.append("canonical source is not an LLM tuner")
    if not payload.get("llm_actor_model") or not payload.get("llm_speculator_model"):
        errors.append("canonical source is not an explicit dual-loop configuration")
    if tuple(payload.get("parameters_to_tune") or ()) != KNOBS:
        errors.append("canonical source does not retain the ordered eight-knob search space")
    serialized = json.dumps(payload)
    if "AIza" in serialized or "sk-or-" in serialized:
        errors.append("canonical source contains credential-like material")
    return errors


def validate_source() -> int:
    payload = load(SOURCE)
    errors = source_errors(payload)
    if errors:
        raise ValueError("; ".join(errors))
    print("MUTILATE_FUNCTIONAL_SOURCE: PASS (canonical App dual loop; eight knobs)")
    return 0


def materialize(
    output: Path,
    results_dir: Path,
    server_ip: str,
    client_ip: str,
    control_port: int,
) -> int:
    payload = load(SOURCE)
    errors = source_errors(payload)
    if errors:
        raise ValueError("; ".join(errors))
    payload.update(
        {
            "max_iterations": TUNING_WINDOWS,
            "post_tuning_windows": STABLE_WINDOWS,
            "window_duration": WINDOW_SECONDS,
            "experiment_profile": None,
            "mutilate_client_host": client_ip,
            "mutilate_target": f"{server_ip}:11211",
            "mutilate_control_port": control_port,
            # The service advertises its absolute local binary during handshake.
            "mutilate_bin_path": "deps/mutilate/mutilate",
            "llm_actor_model": FUNCTIONAL_MODEL,
            "llm_speculator_model": FUNCTIONAL_MODEL,
            "llm_model_name": FUNCTIONAL_MODEL,
            "llm_secondary_model": FUNCTIONAL_MODEL,
            "llm_api_key": None,
            "openrouter_api_key": None,
            "llm_replay_file": None,
            "previous_run_gist": None,
            "results_dir": str(results_dir.resolve()),
        }
    )
    dump(output, payload)
    print(f"MUTILATE_FUNCTIONAL_CONFIG: PASS ({output})")
    return 0


def assigned_ipv4() -> set[str]:
    result = subprocess.run(
        ["ip", "-o", "-4", "address", "show", "up"],
        text=True,
        capture_output=True,
        check=False,
    )
    addresses: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            addresses.add(fields[3].split("/", 1)[0])
    return addresses


def port_is_free(address: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((address, port))
    except OSError:
        return False
    return True


def preflight(server_ip: str, client_ip: str, control_port: int, real_llm: bool) -> int:
    errors: list[str] = []
    for command in ("ip", "memcached", "perf", "ping", "setsid", "taskset", "timeout"):
        if shutil.which(command) is None:
            errors.append(f"missing command: {command} (run scripts/setup.sh --memcached-server)")
    if shutil.which("perf") is not None:
        perf = subprocess.run(
            ["perf", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if perf.returncode != 0:
            errors.append("perf is installed but unusable for the running kernel (rerun server setup)")
    if server_ip not in assigned_ipv4():
        errors.append(f"server IP {server_ip} is not assigned to this host")
    affinity = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set()
    missing_cpus = sorted(set(range(10)) - affinity)
    if missing_cpus:
        errors.append(f"CPUs 0-9 are unavailable: {missing_cpus}")
    if real_llm and not os.environ.get("GEMINI_API_KEY"):
        errors.append("real mode requires the caller's exported GEMINI_API_KEY")
    if shutil.which("ping") is not None:
        ping = subprocess.run(
            ["ping", "-c", "1", "-W", "2", client_ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ping.returncode != 0:
            errors.append(f"load-generator IP {client_ip} does not answer on the internal network")
    for port in (11211, control_port):
        if server_ip in assigned_ipv4() and not port_is_free(server_ip, port):
            errors.append(f"server port {server_ip}:{port} is already in use")
    if errors:
        raise ValueError("; ".join(errors))
    print(
        "MUTILATE_FUNCTIONAL_PREFLIGHT: PASS "
        f"(server={server_ip}; client={client_ip}; {'real Gemini' if real_llm else 'read-only'})"
    )
    return 0


def positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def nested_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nested_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_dicts(item)


def api_roles(payload: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for item in nested_dicts(payload.get("history") or []):
        tuner_type = str(item.get("tuner_type") or "")
        token_metrics = item.get("token_metrics")
        if not isinstance(token_metrics, dict) or not positive(token_metrics.get("api_total_tokens")):
            continue
        if tuner_type == "actor_reasoning":
            roles.add("actor")
        elif tuner_type == "speculator_quick":
            roles.add("speculator")
    return roles


def find_history(raw_dir: Path) -> tuple[Path, dict[str, Any]]:
    candidates = sorted(
        raw_dir.glob("dual_loop_actor_speculator_mutilate_*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    completed: list[tuple[Path, dict[str, Any]]] = []
    for path in candidates:
        payload = load(path)
        if str(payload.get("reason") or "").lower() == "completed":
            completed.append((path, payload))
    if len(completed) != 1:
        raise ValueError(f"expected exactly one completed Mutilate history, found {len(completed)}")
    return completed[0]


def validate_result(output_dir: Path, *, write_summary: bool) -> dict[str, Any]:
    history_path, payload = find_history(output_dir / "raw")
    config = payload.get("config") or {}
    expected_config = {
        "benchmark": "mutilate",
        "max_iterations": TUNING_WINDOWS,
        "post_tuning_windows": STABLE_WINDOWS,
        "window_duration": WINDOW_SECONDS,
        "llm_actor_model": FUNCTIONAL_MODEL,
        "llm_speculator_model": FUNCTIONAL_MODEL,
    }
    mismatches = [name for name, value in expected_config.items() if config.get(name) != value]
    if mismatches:
        raise ValueError(f"history config differs for: {', '.join(mismatches)}")
    if config.get("llm_replay_file"):
        raise ValueError("history used an LLM replay file instead of the real provider")

    indexed: dict[int, dict[str, Any]] = {}
    for position, row in enumerate(payload.get("history") or []):
        if not isinstance(row, dict):
            continue
        iteration = int(row.get("iteration", position))
        indexed[iteration] = row
    if tuple(sorted(indexed)) != EXPECTED_ITERATIONS:
        raise ValueError(
            f"expected iterations {EXPECTED_ITERATIONS}, found {tuple(sorted(indexed))}"
        )

    rows: list[dict[str, Any]] = []
    for iteration in EXPECTED_ITERATIONS:
        metrics = indexed[iteration].get("metrics") or {}
        required = ("throughput", "goodput", "latency_avg", "latency_p95", "latency_p99")
        invalid = [name for name in required if not positive(metrics.get(name))]
        if invalid:
            raise ValueError(f"iteration {iteration} has invalid metrics: {', '.join(invalid)}")
        samples = metrics.get("num_samples")
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 2:
            raise ValueError(f"iteration {iteration} has fewer than two valid Mutilate samples")
        rows.append(
            {
                "iteration": iteration,
                "phase": "baseline" if iteration == 0 else ("tuning" if iteration <= TUNING_WINDOWS else "stable"),
                "throughput": float(metrics["throughput"]),
                "goodput": float(metrics["goodput"]),
                "latency_avg_ms": float(metrics["latency_avg"]),
                "latency_p95_ms": float(metrics["latency_p95"]),
                "latency_p99_ms": float(metrics["latency_p99"]),
                "valid_samples": samples,
                "failed_samples": int(metrics.get("num_failed_samples", 0)),
            }
        )

    roles = api_roles(payload)
    if roles != {"actor", "speculator"}:
        raise ValueError(f"missing real API token evidence for roles: {sorted({'actor', 'speculator'} - roles)}")
    restoration = load(output_dir / "restoration_report.json")
    if (
        restoration.get("restore_status") != "PASS"
        or restoration.get("verify_status") != "PASS"
        or restoration.get("byte_mismatches")
    ):
        raise ValueError("host restoration did not pass byte verification")
    serialized = json.dumps(payload)
    if "AIza" in serialized or "GEMINI_API_KEY" in serialized:
        raise ValueError("history contains credential-like material")

    report = {
        "schema_version": 1,
        "status": "PASS",
        "history": str(history_path),
        "schedule": {
            "baseline_windows": 1,
            "tuning_windows": TUNING_WINDOWS,
            "stable_windows": STABLE_WINDOWS,
            "seconds_per_window": WINDOW_SECONDS,
        },
        "api_roles_verified": sorted(roles),
        "restoration": "PASS",
        "windows": rows,
    }
    if write_summary:
        dump(output_dir / "mutilate_summary.json", report)
        with (output_dir / "mutilate_summary.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = tuple(rows[0])
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return report


def check(output_dir: Path, write_summary: bool) -> int:
    report = validate_result(output_dir.resolve(), write_summary=write_summary)
    for row in report["windows"]:
        print(
            f"WINDOW {row['iteration']} ({row['phase']}): "
            f"throughput={row['throughput']:.1f} ops/s "
            f"p99={row['latency_p99_ms']:.3f} ms samples={row['valid_samples']}"
        )
    print(f"MUTILATE_FUNCTIONAL_RUN: PASS ({output_dir.resolve()})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-source")
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--output", type=Path, required=True)
    materialize_parser.add_argument("--results-dir", type=Path, required=True)
    for child in (materialize_parser,):
        child.add_argument("--server-ip", required=True)
        child.add_argument("--client-ip", required=True)
        child.add_argument("--control-port", type=int, default=19876)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--server-ip", required=True)
    preflight_parser.add_argument("--client-ip", required=True)
    preflight_parser.add_argument("--control-port", type=int, default=19876)
    preflight_parser.add_argument("--real-llm", action="store_true")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--output-dir", type=Path, required=True)
    check_parser.add_argument("--write-summary", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "validate-source":
            return validate_source()
        if args.command == "materialize":
            return materialize(args.output, args.results_dir, args.server_ip, args.client_ip, args.control_port)
        if args.command == "preflight":
            return preflight(args.server_ip, args.client_ip, args.control_port, args.real_llm)
        return check(args.output_dir, args.write_summary)
    except Exception as exc:
        print(f"MUTILATE_FUNCTIONAL: FAIL ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
