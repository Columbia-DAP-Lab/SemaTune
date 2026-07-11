#!/usr/bin/env python3
"""Materialize, validate, summarize, and plot the canonical Sysbench suite."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_ROOT = ROOT / "reproduction/configs/common/sysbench_oltp_rw_hi_p99"
MANIFEST = HERE / "sysbench_paper_suite.json"
KNOBS = (
    "min_granularity_ns", "latency_ns", "cstate_max", "napi_busy_poll",
    "wakeup_granularity_ns", "migration_cost_ns", "max_perf_pct", "min_perf_pct",
)
DEFAULTS = {
    "min_granularity_ns": 3_000_000, "latency_ns": 24_000_000,
    "cstate_max": "unlimited", "napi_busy_poll": 0,
    "wakeup_granularity_ns": 4_000_000, "migration_cost_ns": 500_000,
    "max_perf_pct": 100, "min_perf_pct": 0,
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def manifest() -> dict[str, Any]:
    return load(MANIFEST)


def config_path(method: dict[str, Any]) -> Path:
    return CONFIG_ROOT / method["config"]


def history_files(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.glob("*.json")
        if path.name.startswith(("optimization_history_", "dual_loop_actor_speculator_"))
    )


def completed_history(directory: Path) -> tuple[Path, dict[str, Any]]:
    for path in sorted(history_files(directory), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        payload = load(path)
        reason = str(payload.get("reason") or payload.get("terminated_reason") or "").lower()
        if reason in {"complete", "completed", "completed_early"}:
            iterations = {int(row.get("iteration", -1)) for row in payload.get("history") or []}
            if set(range(1, 51)).issubset(iterations):
                return path, payload
    raise ValueError(f"no completed history containing iterations 1-50 under {directory}")


def validate_configs() -> int:
    from barebones_optimizer.config import SimpleConfig
    from barebones_optimizer.parameter_manager import get_selected_default_parameters

    errors: list[str] = []
    methods = manifest()["methods"]
    in_window_dual = {"sematune_app", "sematune_system", "sematune_ipc"}
    reference = load(config_path(methods[0]))
    common_keys = (
        "benchmark", "sysbench_threads", "sysbench_tables", "sysbench_table_size",
        "sysbench_rate", "pin_to_cores", "parameters_to_tune", "parameter_ranges",
    )
    report = []
    for method in methods:
        path = config_path(method)
        try:
            raw = load(path)
            config = SimpleConfig.load(str(path))
        except Exception as exc:
            errors.append(f"{method['id']}: {exc}")
            continue
        for key in common_keys:
            if raw.get(key) != reference.get(key):
                errors.append(f"{method['id']}: {key} differs from Fixed")
        if tuple(raw.get("parameters_to_tune") or ()) != KNOBS:
            errors.append(f"{method['id']}: canonical knob order changed")
        expected_continuous_apply = method["id"] in in_window_dual
        if raw.get("continuous_apply") is not expected_continuous_apply:
            mode = "in-window" if expected_continuous_apply else "outside-window"
            errors.append(f"{method['id']}: must use {mode} application mode")
        if raw.get("window_duration") != 5:
            errors.append(f"{method['id']}: canonical paper window duration must remain 5 seconds")
        if method["id"] in in_window_dual and raw.get("bind_network_irqs") is not False:
            errors.append(f"{method['id']}: short in-window run must not rebind NIC IRQs")
        defaults = get_selected_default_parameters(set(config.parameters_to_tune or ()))
        if defaults != DEFAULTS:
            errors.append(f"{method['id']}: defaults changed: {defaults!r}")
        report.append({
            "id": method["id"], "source": str(path.relative_to(ROOT)),
            "tuner_type": config.tuner_type, "optimization_metric": config.optimization_metric,
            "optimization_goal": config.optimization_goal, "max_iterations": config.max_iterations,
            "post_tuning_windows": config.post_tuning_windows,
            "window_duration": config.window_duration,
            "continuous_apply": config.continuous_apply,
        })
    if errors:
        raise ValueError("; ".join(errors))
    print(f"SYSBENCH_PAPER_CONFIGS: PASS ({len(report)} methods; identical workload, knobs, ranges, defaults)")
    return 0


def materialize(method_id: str, output: Path, results_dir: Path) -> int:
    method = next((item for item in manifest()["methods"] if item["id"] == method_id), None)
    if method is None:
        raise ValueError(f"unknown method: {method_id}")
    payload = load(config_path(method))
    payload.update({
        "results_dir": str(results_dir.resolve()), "llm_api_key": None,
        "openrouter_api_key": None, "llm_replay_file": None,
        "previous_run_gist": None, "llm_api_log_enabled": False,
    })
    dump(output, payload)
    return 0


def completed_methods(results_dir: Path) -> int:
    for method in manifest()["methods"]:
        try:
            completed_history(results_dir / "raw" / method["id"])
        except ValueError:
            continue
        print(method["id"])
    return 0


def phase_values(payload: dict[str, Any], start: int, end: int) -> list[float]:
    values = []
    for row in payload.get("history") or []:
        iteration = int(row.get("iteration", -1))
        if start <= iteration <= end:
            value = (row.get("metrics") or {}).get("latency_p99")
            if not isinstance(value, (int, float)) or value <= 0 or not math.isfinite(value):
                raise ValueError(f"invalid latency_p99 at iteration {iteration}: {value!r}")
            values.append(float(value))
    if len(values) != end - start + 1:
        raise ValueError(f"expected {end-start+1} values for {start}-{end}, found {len(values)}")
    return values


def bootstrap_improvement(fixed: list[float], candidate: list[float], seed: int) -> tuple[float, float]:
    if fixed == candidate:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(4000):
        f = rng.choice(fixed, size=len(fixed), replace=True).mean()
        c = rng.choice(candidate, size=len(candidate), replace=True).mean()
        samples.append((f / c - 1.0) * 100.0)
    lo, hi = np.percentile(samples, [2.5, 97.5])
    return float(lo), float(hi)


def summarize_and_plot(results_dir: Path, output_dir: Path) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = manifest()
    histories: dict[str, tuple[Path, dict[str, Any]]] = {}
    for method in spec["methods"]:
        histories[method["id"]] = completed_history(results_dir / "raw" / method["id"])
    windows = spec["comparison_windows"]
    fixed_payload = histories["fixed"][1]
    fixed_phases = {
        phase: phase_values(fixed_payload, bounds[0], bounds[1])
        for phase, bounds in windows.items()
    }
    rows = []
    for method_index, method in enumerate(spec["methods"]):
        path, payload = histories[method["id"]]
        for phase_index, (phase, bounds) in enumerate(windows.items()):
            values = phase_values(payload, bounds[0], bounds[1])
            fixed = fixed_phases[phase]
            fixed_mean = float(np.mean(fixed))
            candidate_mean = float(np.mean(values))
            improvement = (fixed_mean / candidate_mean - 1.0) * 100.0
            latency_reduction = (fixed_mean - candidate_mean) / fixed_mean * 100.0
            lo, hi = bootstrap_improvement(fixed, values, 7300 + method_index * 10 + phase_index)
            rows.append({
                "method": method["id"], "label": method["label"], "phase": phase,
                "window": f"{bounds[0]}-{bounds[1]}", "history": str(path),
                "fixed_p99_ms": fixed_mean, "candidate_p99_ms": candidate_mean,
                "improvement_pct": improvement, "latency_reduction_pct": latency_reduction,
                "ci95_low_pct": lo, "ci95_high_pct": hi,
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "sysbench_improvement_over_fixed.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    dump(output_dir / "sysbench_improvement_over_fixed.json", {
        "schema_version": 1,
        "plot_1_improvement_formula": "(fixed_p99_ms / candidate_p99_ms - 1) * 100",
        "latency_reduction_formula": "(fixed_p99_ms - candidate_p99_ms) / fixed_p99_ms * 100",
        "rows": rows,
    })

    plotted_methods = [item for item in spec["methods"] if item["id"] != "fixed"]
    labels = [item["label"] for item in plotted_methods]
    x = np.arange(len(labels)); width = 0.36
    fig, ax = plt.subplots(figsize=(12.8, 5.8))
    for offset, phase, hatch, color in [(-width/2, "tuning", "", "#4C78A8"), (width/2, "stable", "//", "#72B7B2")]:
        selected = [next(row for row in rows if row["method"] == item["id"] and row["phase"] == phase) for item in plotted_methods]
        values = np.array([row["improvement_pct"] for row in selected])
        low = np.array([row["ci95_low_pct"] for row in selected])
        high = np.array([row["ci95_high_pct"] for row in selected])
        errors = np.vstack((np.maximum(0, values-low), np.maximum(0, high-values)))
        ax.bar(x+offset, values, width, label=phase.capitalize(), color=color,
               edgecolor="black", linewidth=0.7, hatch=hatch, yerr=errors,
               error_kw={"elinewidth": 0.8, "capsize": 2})
    ax.axhline(0, color="black", linewidth=0.9)
    ax.set_ylabel("Relative performance vs Fixed (%)\n(Fixed/Candidate - 1) x 100; higher is better")
    ax.set_title("Sysbench OLTP-RW — improvement/decrease relative to Fixed")
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=28, ha="right")
    ax.grid(axis="y", linestyle="--", alpha=0.35); ax.legend(frameon=False, ncol=2)
    fig.tight_layout()
    for suffix in ("pdf", "png"):
        fig.savefig(output_dir / f"sysbench_improvement_over_fixed.{suffix}", dpi=220)
    plt.close(fig)
    print(f"SYSBENCH_PAPER_PLOT: PASS ({output_dir})")
    return 0


def audit(results_dir: Path, output: Path) -> int:
    methods = manifest()["methods"]
    report: dict[str, Any] = {"schema_version": 1, "status": "PASS", "methods": {}, "restoration": {}}
    errors: list[str] = []
    llm_methods = {"sematune_single", "sematune_app", "sematune_system", "sematune_ipc", "sematune_trim"}
    final_gate_methods = {"sematune_single", "sematune_app", "sematune_system", "sematune_ipc"}

    for method in methods:
        method_id = method["id"]
        try:
            path, payload = completed_history(results_dir / "raw" / method_id)
            phase_values(payload, 1, 30); stable_values = phase_values(payload, 31, 50)
        except Exception as exc:
            errors.append(f"{method_id}: {exc}")
            continue
        item: dict[str, Any] = {
            "history": str(path), "windows_1_50_valid": True,
            "comparison_p99_mean_ms": float(np.mean(stable_values)),
        }
        if method_id in llm_methods:
            applied = 0; model_responses = 0; final_gate = None
            stable_rows = [row for row in payload.get("history") or [] if 31 <= int(row.get("iteration", -1)) <= 50]
            stable_configs = [plain_parameters(row.get("parameters") or {}) for row in stable_rows]
            for row in payload.get("history") or []:
                timing = row.get("tuner_timing") or {}
                if not isinstance(timing, dict):
                    continue
                candidates: list[tuple[str, dict[str, Any]]] = []
                if timing.get("proposed_parameters"):
                    candidates.append(("single", timing))
                candidates.extend(
                    (key, value) for key, value in timing.items()
                    if isinstance(value, dict) and (
                        value.get("proposed_parameters") or key == "final_freeze_before_stable"
                    )
                )
                for role, response in candidates:
                    if response.get("parameters_applied") is True:
                        applied += 1
                    if response.get("token_metrics"):
                        model_responses += 1
                    if role in {"reasoning_final", "reasoning_final_before_stable", "final_freeze_before_stable"}:
                        proposed = response.get("proposed_parameters")
                        final_gate = {
                            "role": role, "parameters_applied": response.get("parameters_applied") is True,
                            "proposal_recorded": bool(proposed),
                            "matches_all_stable_rows": (
                                bool(proposed) and all(plain_parameters(proposed) == config for config in stable_configs)
                            ) if proposed else len({json.dumps(config, sort_keys=True) for config in stable_configs}) == 1,
                        }
            item.update({"applied_responses": applied, "provider_responses_with_tokens": model_responses})
            if applied == 0 or model_responses == 0:
                errors.append(f"{method_id}: no applied real-model responses")
            if method_id in final_gate_methods:
                item["final_gate"] = final_gate
                if not final_gate or not final_gate["parameters_applied"] or not final_gate["matches_all_stable_rows"]:
                    errors.append(f"{method_id}: final gate was not applied/frozen")
        report["methods"][method_id] = item

    restoration_errors = []
    for method in methods:
        path = results_dir / "state" / f"{method['id']}_restoration.json"
        if not path.is_file():
            restoration_errors.append(f"missing {path.name}"); continue
        payload = load(path)
        if payload.get("restore_status") != "PASS" or payload.get("verify_status") != "PASS" or payload.get("byte_mismatches"):
            restoration_errors.append(path.name)
    report["restoration"] = {
        "reports": len(methods) - len(restoration_errors), "expected": len(methods),
        "all_byte_identical": not restoration_errors, "errors": restoration_errors,
    }
    errors.extend(f"restoration: {error}" for error in restoration_errors)
    serialized = json.dumps(report)
    if "AIza" in serialized or "sk-or-" in serialized:
        errors.append("validation report contains credential-like material")
    if errors:
        report["status"] = "FAIL"; report["errors"] = errors
    dump(output, report)
    if errors:
        raise ValueError("; ".join(errors))
    print(f"SYSBENCH_PAPER_AUDIT: PASS ({len(methods)} methods, {len(methods)} restoration reports)")
    return 0


def plain_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value.get("value") if isinstance(value, dict) and "value" in value else value
        for name, value in parameters.items()
    }


def build_dual_replay(history_path: Path, output: Path) -> int:
    """Convert one completed dual-loop history into a role/timing-aware replay."""
    payload = load(history_path)
    history = payload.get("history") or []
    entries = {iteration: {"iteration": iteration, "responses": {}} for iteration in range(31)}
    for row in history:
        row_iteration = int(row.get("iteration", -1))
        timing = row.get("tuner_timing") or {}
        if not isinstance(timing, dict):
            continue
        quick = timing.get("quick")
        if isinstance(quick, dict) and quick.get("proposed_parameters") and row_iteration >= 1:
            entries[row_iteration - 1]["responses"]["quick"] = replay_response(quick)
        reasoning = timing.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("proposed_parameters"):
            start_iteration = int(reasoning.get("tuner_start_iteration", row_iteration))
            call_iteration = max(0, start_iteration - 1)
            entries[call_iteration]["responses"]["reasoning"] = replay_response(reasoning)
        final = timing.get("reasoning_final_before_stable") or timing.get("reasoning_final")
        if isinstance(final, dict) and final.get("proposed_parameters"):
            entries[30]["responses"]["reasoning_final"] = replay_response(final, converged=True)
    missing_quick = [i for i in range(30) if "quick" not in entries[i]["responses"]]
    if missing_quick:
        raise ValueError(f"source history lacks Speculator actions for calls: {missing_quick}")
    if "reasoning_final" not in entries[30]["responses"]:
        raise ValueError("source history lacks the final Actor action")
    replay = {
        "schema_version": 1,
        "description": f"Exact role actions and response delays extracted from {history_path.name}",
        "provider_requests": 0,
        "source_history": str(history_path.resolve()),
        "history": [entries[i] for i in range(31)],
    }
    dump(output, replay)
    print(f"SYSBENCH_DUAL_REPLAY_BUILD: PASS ({output})")
    return 0


def replay_response(timing: dict[str, Any], converged: bool | None = None) -> dict[str, Any]:
    response = {
        "parameters": plain_parameters(timing["proposed_parameters"]),
        "justification": timing.get("justification") or "Recorded dual-loop action replay.",
        "confidence": float(timing.get("confidence") or 1.0),
        "converged": timing.get("converged") if converged is None else converged,
        "response_time_seconds": max(0.0, float(timing.get("tuner_duration") or 0.0)),
        "token_metrics": timing.get("token_metrics"),
    }
    return response


def materialize_replay(method_id: str, replay: Path, output: Path, results_dir: Path) -> int:
    if method_id not in {"sematune_app", "sematune_system", "sematune_ipc"}:
        raise ValueError(f"not a dual-loop replay method: {method_id}")
    materialize(method_id, output, results_dir)
    payload = load(output)
    payload["llm_replay_file"] = str(replay.resolve())
    payload["llm_api_key"] = None
    dump(output, payload)
    return 0


def plot_real_replay(real_dir: Path, replay_dir: Path, output_dir: Path) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = [
        ("sematune_single", "SemaTune Single", False),
        ("sematune_app", "SemaTune App", True),
        ("sematune_system", "SemaTune Sys", True),
        ("sematune_ipc", "SemaTune IPC", True),
    ]
    _, fixed_payload = completed_history(real_dir / "raw" / "fixed")
    phases = {"Tuning (1-30)": (1, 30), "Stable (31-50)": (31, 50)}
    rows = []
    for method_id, label, has_replay in methods:
        _, real_payload = completed_history(real_dir / "raw" / method_id)
        replay_payload = completed_history(replay_dir / "raw" / method_id)[1] if has_replay else None
        for phase, (start, end) in phases.items():
            fixed_mean = float(np.mean(phase_values(fixed_payload, start, end)))
            mode_payloads = [("Real LLM", real_payload)]
            if replay_payload is not None:
                mode_payloads.append(("Action replay", replay_payload))
            for mode, payload in mode_payloads:
                candidate = float(np.mean(phase_values(payload, start, end)))
                rows.append({
                    "method": method_id, "label": label, "phase": phase, "mode": mode,
                    "fixed_p99_ms": fixed_mean, "candidate_p99_ms": candidate,
                    "improvement_pct": (fixed_mean / candidate - 1.0) * 100.0,
                    "latency_reduction_pct": (fixed_mean - candidate) / fixed_mean * 100.0,
                })
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "sysbench_real_vs_action_replay.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    dump(output_dir / "sysbench_real_vs_action_replay.json", {
        "schema_version": 1,
        "formula": "(fixed_p99_ms / candidate_p99_ms - 1) * 100",
        "rows": rows,
    })
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.8), sharey=True)
    x = np.arange(len(methods)); width = 0.34
    for ax, (phase, _) in zip(axes, phases.items()):
        for offset, mode, color, hatch in [(-width/2, "Real LLM", "#4C78A8", ""), (width/2, "Action replay", "#72B7B2", "//")]:
            selected = [next((row for row in rows if row["method"] == method_id and row["phase"] == phase and row["mode"] == mode), None) for method_id, _, _ in methods]
            ax.bar(x + offset, [row["improvement_pct"] if row else np.nan for row in selected], width,
                   label=mode, color=color, edgecolor="black", linewidth=0.7, hatch=hatch)
        ax.axhline(0, color="black", linewidth=0.9); ax.grid(axis="y", linestyle="--", alpha=0.35)
        ax.set_title(phase); ax.set_xticks(x); ax.set_xticklabels([label for _, label, _ in methods], rotation=20, ha="right")
    axes[0].set_ylabel("Relative performance vs Fixed (%)\n(Fixed/Candidate - 1) x 100; higher is better")
    axes[1].legend(frameon=False)
    fig.suptitle("Sysbench OLTP-RW — real model decisions vs exact action replay")
    fig.tight_layout()
    for suffix in ("pdf", "png"):
        fig.savefig(output_dir / f"sysbench_real_vs_action_replay.{suffix}", dpi=220)
    plt.close(fig)
    print(f"SYSBENCH_REAL_REPLAY_PLOT: PASS ({output_dir})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-configs")
    make = sub.add_parser("materialize"); make.add_argument("--method", required=True); make.add_argument("--output", type=Path, required=True); make.add_argument("--results-dir", type=Path, required=True)
    done = sub.add_parser("completed-methods"); done.add_argument("--results-dir", type=Path, required=True)
    plot = sub.add_parser("plot"); plot.add_argument("--results-dir", type=Path, required=True); plot.add_argument("--output-dir", type=Path, required=True)
    check = sub.add_parser("audit"); check.add_argument("--results-dir", type=Path, required=True); check.add_argument("--output", type=Path, required=True)
    build = sub.add_parser("build-replay"); build.add_argument("--history", type=Path, required=True); build.add_argument("--output", type=Path, required=True)
    replay_config = sub.add_parser("materialize-replay"); replay_config.add_argument("--method", required=True); replay_config.add_argument("--replay", type=Path, required=True); replay_config.add_argument("--output", type=Path, required=True); replay_config.add_argument("--results-dir", type=Path, required=True)
    compare = sub.add_parser("plot-real-replay"); compare.add_argument("--real-dir", type=Path, required=True); compare.add_argument("--replay-dir", type=Path, required=True); compare.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-configs": return validate_configs()
        if args.command == "materialize": return materialize(args.method, args.output, args.results_dir)
        if args.command == "completed-methods": return completed_methods(args.results_dir)
        if args.command == "audit": return audit(args.results_dir, args.output)
        if args.command == "build-replay": return build_dual_replay(args.history, args.output)
        if args.command == "materialize-replay": return materialize_replay(args.method, args.replay, args.output, args.results_dir)
        if args.command == "plot-real-replay": return plot_real_replay(args.real_dir, args.replay_dir, args.output_dir)
        return summarize_and_plot(args.results_dir, args.output_dir)
    except Exception as exc:
        print(f"SYSBENCH_PAPER_SUITE: FAIL ({exc})", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
