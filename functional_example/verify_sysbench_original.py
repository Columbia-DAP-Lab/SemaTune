#!/usr/bin/env python3
"""Verify the canonical Sysbench config and a completed real-LLM history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "reproduction/configs/common/sysbench_oltp_rw_hi_p99/sematune_app.json"
ARCHIVED_DIR = ROOT / (
    "all_results/paper_evaluation/"
    "results_config_full_param_sysbench_oltp_rw_hi_p99_20260305_203050_retry/"
    "sysbench_oltp_rw_hi_p99/llm_dual_app_metrics_final_actor"
)
CONFIG_KEYS = (
    "benchmark", "tuner_type", "max_iterations", "post_tuning_windows",
    "window_duration", "respect_config_window_duration", "sysbench_threads",
    "sysbench_tables", "sysbench_table_size", "sysbench_rate", "pin_to_cores",
    "parameters_to_tune", "parameter_ranges", "fixed_parameters",
    "llm_actor_model", "llm_speculator_model",
    "dual_loop_force_final_actor_before_stable", "llm_explore_until_last_iteration",
)
EXPECTED_DEFAULTS = {
    "min_granularity_ns": 3_000_000,
    "latency_ns": 24_000_000,
    "cstate_max": "unlimited",
    "napi_busy_poll": 0,
    "wakeup_granularity_ns": 4_000_000,
    "migration_cost_ns": 500_000,
    "max_perf_pct": 100,
    "min_perf_pct": 0,
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def plain(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value.get("value") if isinstance(value, dict) and "value" in value else value
        for name, value in parameters.items()
    }


def in_range(ranges: dict[str, Any], name: str, value: Any) -> bool:
    allowed = ranges[name]
    if len(allowed) == 2 and all(isinstance(item, (int, float)) for item in allowed):
        return allowed[0] <= value <= allowed[1]
    return value in allowed


def verify_config(path: Path) -> dict[str, Any]:
    candidate = load(path)
    canonical = load(CANONICAL)
    archived_files = sorted(ARCHIVED_DIR.glob("*.json"))
    if not archived_files:
        raise ValueError(f"archived reference is missing under {ARCHIVED_DIR}")
    archived = load(archived_files[0])["config"]
    mismatches = []
    for key in CONFIG_KEYS:
        if candidate.get(key) != canonical.get(key) or candidate.get(key) != archived.get(key):
            mismatches.append(key)
    if mismatches:
        raise ValueError(f"config differs from canonical/archive for: {', '.join(mismatches)}")
    from optimizer.config import SimpleConfig
    from optimizer.parameter_manager import get_selected_default_parameters

    parsed = SimpleConfig.load(str(path))
    actual_defaults = get_selected_default_parameters(set(parsed.parameters_to_tune or ()))
    if actual_defaults != EXPECTED_DEFAULTS:
        raise ValueError(f"eight-knob defaults changed: {actual_defaults!r}")
    return {
        "config": str(path),
        "canonical_match": True,
        "archive_match": True,
        "parameters": candidate["parameters_to_tune"],
        "parameter_ranges": candidate["parameter_ranges"],
        "defaults": actual_defaults,
        "schedule": {"tuning": 30, "stable": 20, "configured_window_seconds": 5},
    }


def verify_history(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    payload = load(path)
    history = payload.get("history") or []
    effective_config = payload.get("config") or config
    baseline_windows = 1 if effective_config.get("llm_measure_default_before_tuning") else 0
    expected_windows = 50 + baseline_windows
    if len(history) != expected_windows:
        raise ValueError(f"expected {expected_windows} rows, found {len(history)}")
    ranges = config["parameter_ranges"]
    responses = []
    final_seen = False
    stable_rows = [row for row in history if row.get("post_tuning_phase")]
    if len(stable_rows) != 20:
        raise ValueError(f"expected 20 stable windows, found {len(stable_rows)}")
    first_stable_parameters = plain(stable_rows[0].get("parameters") or {})
    for row in history:
        timing = row.get("tuner_timing") or {}
        if not isinstance(timing, dict):
            continue
        for role, item in timing.items():
            if not isinstance(item, dict) or not item.get("proposed_parameters"):
                continue
            proposed = plain(item["proposed_parameters"])
            for name, value in proposed.items():
                if name not in ranges or not in_range(ranges, name, value):
                    raise ValueError(f"{role} proposed invalid {name}={value!r}")
            if item.get("parameters_applied") is not True:
                raise ValueError(f"{role} response was recorded but not applied")
            if not isinstance(item.get("parameters_applied_timestamp"), (int, float)):
                raise ValueError(f"{role} response lacks an application timestamp")
            is_final = role in {
                "reasoning_final", "reasoning_final_before_stable", "final_freeze_before_stable"
            }
            # An asynchronous quick response can be applied and then superseded
            # by an Actor response in the same window, so the row snapshot need
            # not equal every intermediate proposal. The optimizer sets
            # parameters_applied only after ParameterManager succeeds. The
            # final gate, however, must exactly equal every frozen stable row.
            if is_final and first_stable_parameters != proposed:
                raise ValueError("final Actor proposal does not match the frozen stable configuration")
            if not str(item.get("justification") or "").strip():
                raise ValueError(f"{role} response lacks a justification")
            final_seen |= role in {"reasoning_final", "reasoning_final_before_stable", "final_freeze_before_stable"}
            responses.append({
                "iteration": row.get("iteration"),
                "role": role,
                "parameters_applied": True,
                "application_timestamp": item.get("parameters_applied_timestamp"),
                "matches_row_snapshot": plain(row.get("parameters") or {}) == proposed,
                "parameters": proposed,
            })
    if not responses:
        raise ValueError("history contains no inspectable real-LLM responses")
    if not any(item["role"] in {"quick", "speculator"} for item in responses):
        raise ValueError("history contains no applied Speculator response")
    if not any(item["role"] in {"reasoning", "actor"} for item in responses):
        raise ValueError("history contains no applied Actor response")
    if not final_seen:
        raise ValueError("history contains no applied final Actor gate before stable windows")
    serialized = json.dumps(payload)
    if "GEMINI_API_KEY" in serialized or "AIza" in serialized:
        raise ValueError("history contains credential material")
    return {
        "history": str(path),
        "windows": len(history),
        "default_baseline_windows": baseline_windows,
        "tuning_windows": 30,
        "stable_windows": len(stable_rows),
        "inspectable_applied_llm_responses": len(responses),
        "final_actor_gate_applied": final_seen,
        "responses": responses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CANONICAL)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config_report = verify_config(args.config.resolve())
    report: dict[str, Any] = {"status": "PASS", "config_validation": config_report}
    if args.history:
        report["history_validation"] = verify_history(
            args.history.resolve(), load(args.config.resolve())
        )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "SYSBENCH_ORIGINAL_VALIDATION: PASS "
        f"(canonical config/ranges/defaults; responses={report.get('history_validation', {}).get('inspectable_applied_llm_responses', 'not-run')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
