#!/usr/bin/env python3
"""Validate, inspect, or execute the one-rerun paper reproduction manifest."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REPRO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPRO_ROOT / "experiment_manifest.json"
LOCK_PATH = Path("/tmp/sematune-os-param-tuning-reproduction.lock")
HISTORY_PATTERNS = ("optimization_history_*.json", "dual_loop_actor_speculator_*.json")
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-or-[0-9A-Za-z_-]{16,}"),
)


def _interrupt_on_term(signum: int, frame: Any) -> None:
    """Route SIGTERM through the process-group cleanup used for Ctrl-C."""

    raise KeyboardInterrupt


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    base_name = value.get("base_manifest")
    if base_name:
        base_path = (path.parent / str(base_name)).resolve()
        base = json.loads(base_path.read_text(encoding="utf-8"))
        base_jobs = {job["id"]: job for job in base["jobs"]}
        expanded: list[dict[str, Any]] = []
        for override in value.get("jobs", []):
            job_id = override.get("id")
            if job_id not in base_jobs:
                raise ValueError(f"scoped manifest references unknown base job: {job_id!r}")
            job = dict(base_jobs[job_id])
            job.update(override)
            expanded.append(job)
        value["jobs"] = expanded
    if value.get("reruns_per_configuration") != 1:
        raise ValueError("the reproduction manifest must request exactly one rerun")
    return value


def parse_plots(raw: str, manifest: dict[str, Any]) -> set[int]:
    available = {int(key) for key in manifest["plots"]}
    if raw.strip().lower() == "all":
        return available
    selected: set[int] = set()
    for item in raw.split(","):
        try:
            selected.add(int(item.strip()))
        except ValueError as exc:
            raise ValueError(f"invalid plot number: {item!r}") from exc
    unknown = selected - available
    if unknown:
        raise ValueError(f"unknown plots: {sorted(unknown)}")
    if not selected:
        raise ValueError("select at least one plot")
    return selected


def select_jobs(manifest: dict[str, Any], plots: set[int]) -> list[dict[str, Any]]:
    return [job for job in manifest["jobs"] if plots.intersection(job["plots"])]


def config_path(job: dict[str, Any]) -> Path:
    return REPRO_ROOT / job["config"]


def materialized_config(job: dict[str, Any]) -> dict[str, Any]:
    """Load a checked-in config and apply declared, reviewable run overrides."""

    payload = json.loads(config_path(job).read_text(encoding="utf-8"))
    overrides = job.get("runtime_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"{job.get('id')}: runtime_overrides must be an object")
    payload.update(overrides)
    return payload


def history_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in HISTORY_PATTERNS:
        files.extend(path for path in directory.glob(pattern) if path.is_file())
    return sorted(set(files))


def result_satisfies_completion(data: dict[str, Any], completion: dict[str, Any]) -> bool:
    """Return whether a history satisfies a scoped job's exact completion contract."""

    marker = str(data.get("terminated_reason") or data.get("reason") or "").lower()
    if marker not in {"complete", "completed", "max_iterations", "converged"}:
        return False
    history = data.get("history")
    if not isinstance(history, list):
        return False

    baseline_rows = [row for row in history if isinstance(row, dict) and row.get("iteration") == 0]
    if baseline_rows and not completion.get("allow_iteration_zero", False):
        return False
    if len(baseline_rows) > 1:
        return False
    rows = [row for row in history if isinstance(row, dict) and row.get("iteration") != 0]
    expected = int(completion["measurement_windows"])
    if len(rows) != expected:
        return False
    if [row.get("iteration") for row in rows] != list(range(1, expected + 1)):
        return False

    tuning = int(completion["tuning_windows"])
    post = int(completion["post_tuning_windows"])
    if tuning + post != expected:
        return False
    if any(bool(row.get("post_tuning_phase")) for row in rows[:tuning]):
        return False
    if any(not bool(row.get("post_tuning_phase")) for row in rows[tuning:]):
        return False

    metric = str(completion["optimization_metric"])
    for row in rows:
        parameters = row.get("parameters")
        metrics = row.get("metrics")
        if not isinstance(parameters, dict) or not parameters:
            return False
        if not isinstance(metrics, dict):
            return False
        try:
            value = float(metrics[metric])
            reward = float(row["reward"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(value) or not math.isfinite(reward):
            return False

    required_mode = completion.get("required_mode")
    if required_mode and data.get("mode") != required_mode:
        return False
    if completion.get("require_optimizer_gist") and not str(data.get("optimizer_gist", "")).strip():
        return False
    return True


def completed_result(directory: Path, job: dict[str, Any] | None = None) -> Path | None:
    candidates = history_files(directory)
    for path in reversed(candidates):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        completion = job.get("completion") if job else None
        if completion is not None:
            if isinstance(completion, dict) and result_satisfies_completion(data, completion):
                return path
            continue
        history = data.get("history")
        if not isinstance(history, list) or not history:
            continue
        marker = str(data.get("terminated_reason") or data.get("reason") or "").lower()
        if marker and marker not in {"complete", "completed", "max_iterations", "converged"}:
            continue
        return path
    return None


def validate_manifest(manifest: dict[str, Any], *, verify_sources: bool) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    targets: set[str] = set()
    for job in manifest["jobs"]:
        job_id = job.get("id")
        target = job.get("target_results_dir")
        if not job_id or job_id in ids:
            errors.append(f"duplicate or empty job id: {job_id!r}")
        ids.add(job_id)
        if not target or target in targets:
            errors.append(f"duplicate or empty result target: {target!r}")
        targets.add(target)
        path = config_path(job)
        if not path.is_file():
            errors.append(f"{job_id}: missing config {path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{job_id}: invalid config: {exc}")
            continue
        try:
            effective_payload = materialized_config(job)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{job_id}: invalid runtime overrides: {exc}")
            continue
        serialized = json.dumps(effective_payload)
        if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
            errors.append(f"{job_id}: config contains a credential-like value")
        if payload.get("results_dir") != f"results/reproduced/raw/{target}":
            errors.append(f"{job_id}: unexpected checked-in results_dir")
        completion = job.get("completion")
        if completion is not None:
            try:
                expected_windows = int(completion["measurement_windows"])
                configured_windows = int(effective_payload.get("max_iterations", 0)) + int(
                    effective_payload.get("post_tuning_windows", 0)
                )
                if expected_windows != configured_windows:
                    errors.append(
                        f"{job_id}: completion expects {expected_windows} windows, config runs {configured_windows}"
                    )
                if str(completion["optimization_metric"]) != str(effective_payload.get("optimization_metric")):
                    errors.append(f"{job_id}: completion metric does not match config")
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{job_id}: invalid completion contract: {exc}")
        if verify_sources:
            source = REPO_ROOT / job["source_history"]
            if not source.is_file():
                errors.append(f"{job_id}: missing source history")
            else:
                import hashlib

                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if digest != job["source_history_sha256"]:
                    errors.append(f"{job_id}: source history checksum mismatch")
    for alias in manifest["aliases"]:
        if alias.get("source_job") not in ids:
            errors.append(f"alias references unknown job: {alias.get('source_job')}")
        if alias.get("target_results_dir") in targets:
            errors.append(f"alias collides with a job target: {alias.get('target_results_dir')}")
    for plot, info in manifest["plots"].items():
        unknown = set(info["jobs"]) - ids
        if unknown:
            errors.append(f"plot {plot} references unknown jobs: {sorted(unknown)}")
    return errors


def command_plan(args: argparse.Namespace, manifest: dict[str, Any]) -> int:
    plots = parse_plots(args.plots, manifest)
    selection_label = str(manifest.get("selection_label", "plot"))
    jobs = select_jobs(manifest, plots)
    by_kind = Counter(job["kind"] for job in jobs)
    windows = 0
    llm_jobs = 0
    benchmarks: Counter[str] = Counter()
    for job in jobs:
        cfg = materialized_config(job)
        windows += int(cfg.get("max_iterations", 0)) + int(cfg.get("post_tuning_windows", 0))
        benchmarks[str(cfg.get("benchmark", "unknown"))] += 1
        if str(cfg.get("tuner_type", "")).startswith("llm") or cfg.get("llm_actor_model"):
            llm_jobs += 1
    print(f"{selection_label}s: {','.join(str(x) for x in sorted(plots))}")
    print(f"unique one-rerun configurations: {len(jobs)}")
    print(f"LLM configurations: {llm_jobs}")
    print(f"total benchmark windows: {windows}")
    print("kinds: " + ", ".join(f"{key}={value}" for key, value in sorted(by_kind.items())))
    print("benchmarks: " + ", ".join(f"{key}={value}" for key, value in sorted(benchmarks.items())))
    for plot in sorted(plots):
        info = manifest["plots"][str(plot)]
        selected_count = sum(1 for job in jobs if plot in job["plots"])
        print(f"{selection_label} {plot}: {selected_count} configs — {info['claim']}")
    if args.verbose:
        for job in jobs:
            print(f"{job['id']}\t{job['config']}\t{job['target_results_dir']}")
    return 0


def live_preflight(jobs: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    commands = {"perf", "taskset"}
    benchmarks: set[str] = set()
    needs_llm = False
    for job in jobs:
        cfg = json.loads(config_path(job).read_text(encoding="utf-8"))
        benchmark = str(cfg.get("benchmark", ""))
        benchmarks.add(benchmark)
        needs_llm |= str(cfg.get("tuner_type", "")).startswith("llm") or bool(cfg.get("llm_actor_model"))
        if benchmark.startswith("sysbench"):
            commands.add("sysbench")
        if benchmark in {"tpcc", "ycsb", "sibench", "wikipedia", "twitter", "auctionmark", "otmetrics"}:
            commands.add("java")
            for key in ("benchbase_jar_path", "benchbase_config_file"):
                path = REPO_ROOT / str(cfg.get(key, ""))
                if not path.is_file():
                    errors.append(f"{job['id']}: missing {key}: {path}")
        if benchmark == "tailbench":
            for key in ("tailbench_root", "tailbench_data_root"):
                path = (REPO_ROOT / str(cfg.get(key, ""))).resolve()
                if not path.exists():
                    errors.append(f"{job['id']}: missing {key}: {path}")
        if benchmark == "dcperf_spark":
            path = (REPO_ROOT / str(cfg.get("dcperf_path") or "deps/DCPerf")).resolve()
            if not path.exists():
                errors.append(f"{job['id']}: missing DCPerf tree: {path}")
        if benchmark == "mutilate":
            binary = Path(str(cfg.get("mutilate_bin_path", "~/mutilate/mutilate"))).expanduser()
            if not binary.exists():
                errors.append(f"{job['id']}: missing mutilate binary: {binary}")
    if os.geteuid() != 0:
        commands.add("sudo")
    for command in sorted(commands):
        if shutil.which(command) is None:
            errors.append(f"missing command: {command}")
    if needs_llm and not os.environ.get("GEMINI_API_KEY"):
        errors.append("GEMINI_API_KEY is required for the selected live one-rerun suite")
    if (os.cpu_count() or 0) < 20:
        errors.append("the paper configurations require at least CPUs 0-19")
    # Deduplicate repeated dependency errors from many configs.
    return list(dict.fromkeys(errors))


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def command_for(config: Path) -> list[str]:
    python = sys.executable
    base = [python, "-m", "optimizer.main", "--config", str(config)]
    if os.geteuid() == 0:
        return base
    return ["sudo", "-E", "env", f"PYTHONPATH={REPO_ROOT / 'src'}", f"OS_PARAM_TUNING_ROOT={REPO_ROOT}", *base]


def terminate_group(process: subprocess.Popen[Any], sig: int = signal.SIGTERM) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def materialize_aliases(manifest: dict[str, Any], raw_root: Path, selected_jobs: set[str]) -> None:
    jobs = {job["id"]: job for job in manifest["jobs"]}
    for alias in manifest["aliases"]:
        source_job = alias["source_job"]
        if source_job not in selected_jobs:
            continue
        source = raw_root / jobs[source_job]["target_results_dir"]
        target = raw_root / alias["target_results_dir"]
        if not source.is_dir():
            raise RuntimeError(f"cannot materialize alias; source result is missing: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        relative = os.path.relpath(source, target.parent)
        if target.is_symlink():
            if os.readlink(target) == relative:
                continue
            raise RuntimeError(f"alias points to the wrong source: {target}")
        if target.exists():
            raise RuntimeError(f"alias target already exists and is not a symlink: {target}")
        target.symlink_to(relative, target_is_directory=True)


def command_run(args: argparse.Namespace, manifest: dict[str, Any]) -> int:
    suite_started = time.time()
    plots = parse_plots(args.plots, manifest)
    jobs = select_jobs(manifest, plots)
    if args.limit is not None:
        jobs = jobs[: args.limit]
    if args.dry_run:
        print("DRY_RUN: no output, root, API, benchmark, or kernel operations")
        plan_args = argparse.Namespace(plots=args.plots, verbose=args.verbose)
        return command_plan(plan_args, manifest)

    errors = live_preflight(jobs)
    if errors:
        for error in errors:
            print(f"PREFLIGHT_ERROR: {error}", file=sys.stderr)
        return 2

    output = Path(args.output_dir).expanduser().resolve()
    raw_root = output / "raw"
    logs_root = output / "logs"
    run_configs = output / "run_configs"
    for path in (raw_root, logs_root, run_configs):
        path.mkdir(parents=True, exist_ok=True)
    status_path = output / "run_status.json"
    status: dict[str, Any] = {
        "schema_version": 1,
        "plots": sorted(plots),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reruns_per_configuration": 1,
        "jobs": {},
    }
    if status_path.is_file():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(previous.get("jobs"), dict):
                status["jobs"].update(previous["jobs"])
        except (OSError, json.JSONDecodeError):
            pass

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["OS_PARAM_TUNING_ROOT"] = str(REPO_ROOT)
    failures = 0
    selected_ids = {job["id"] for job in jobs}
    for index, job in enumerate(jobs, start=1):
        target = raw_root / job["target_results_dir"]
        existing = completed_result(target, job)
        if existing is not None and not args.rerun_existing:
            print(f"[{index}/{len(jobs)}] RESUME {job['id']} -> {existing.name}")
            previous_job = status["jobs"].get(job["id"], {})
            status["jobs"][job["id"]] = {
                "status": "reused_existing",
                "history": str(existing),
                **(
                    {"previous_duration_seconds": previous_job["duration_seconds"]}
                    if "duration_seconds" in previous_job
                    else {}
                ),
            }
            write_json_atomic(status_path, status)
            continue

        cfg = materialized_config(job)
        cfg["results_dir"] = str(target)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "__", job["id"])
        runtime_config = run_configs / f"{safe_name}.json"
        write_json_atomic(runtime_config, cfg)
        log_path = logs_root / f"{safe_name}.log"
        command = command_for(runtime_config)
        print(f"[{index}/{len(jobs)}] RUN {job['id']}")
        started = time.time()
        process: subprocess.Popen[Any] | None = None
        try:
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=True,
                )
                returncode = process.wait()
        except KeyboardInterrupt:
            if process is not None:
                terminate_group(process, signal.SIGINT)
            raise
        except BaseException:
            if process is not None:
                terminate_group(process)
            raise
        duration = time.time() - started
        produced = completed_result(target, job)
        if returncode == 0 and produced is not None:
            print(f"[{index}/{len(jobs)}] PASS {job['id']} ({duration:.0f}s)")
            status["jobs"][job["id"]] = {
                "status": "passed",
                "duration_seconds": round(duration, 3),
                "history": str(produced),
                "log": str(log_path),
            }
        else:
            failures += 1
            print(f"[{index}/{len(jobs)}] FAIL {job['id']} (exit={returncode}; log={log_path})", file=sys.stderr)
            status["jobs"][job["id"]] = {
                "status": "failed",
                "duration_seconds": round(duration, 3),
                "exit_code": returncode,
                "log": str(log_path),
            }
            write_json_atomic(status_path, status)
            if not args.keep_going:
                break
        write_json_atomic(status_path, status)

    if failures == 0:
        materialize_aliases(manifest, raw_root, selected_ids)
    status["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    status["elapsed_seconds_this_invocation"] = round(time.time() - suite_started, 3)
    status["failures"] = failures
    write_json_atomic(status_path, status)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help="Manifest to validate or execute (default: the complete paper manifest).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate configs, mappings, and source checksums.")
    validate.add_argument("--skip-source-checksums", action="store_true")

    plan = subparsers.add_parser("plan", help="Print the deduplicated plan.")
    plan.add_argument("--plots", default="all", help="all or a comma-separated subset of 1-7")
    plan.add_argument("--verbose", action="store_true")

    run = subparsers.add_parser("run", help="Execute exactly one run of each selected unique config.")
    run.add_argument("--plots", default="all", help="all or a comma-separated subset of 1-7")
    run.add_argument("--output-dir", default="results/reproduced")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--keep-going", action="store_true")
    run.add_argument("--rerun-existing", action="store_true", help="Add another result even when a completed history exists.")
    run.add_argument("--limit", type=int, help="Developer-only limit for smoke testing the selected job list.")
    return parser


def main() -> int:
    signal.signal(signal.SIGTERM, _interrupt_on_term)
    parser = build_parser()
    args = parser.parse_args()
    try:
        with LOCK_PATH.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            manifest = load_manifest(args.manifest.resolve())
            if args.command == "validate":
                errors = validate_manifest(manifest, verify_sources=not args.skip_source_checksums)
                for error in errors:
                    print(f"FAIL: {error}")
                if errors:
                    return 1
                print(f"PASS: {len(manifest['jobs'])} configs and {len(manifest['aliases'])} reuse aliases validated")
                return 0
            if args.command == "plan":
                return command_plan(args, manifest)
            if args.command == "run":
                return command_run(args, manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
