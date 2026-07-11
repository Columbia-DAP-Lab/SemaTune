#!/usr/bin/env python3
"""Derive the reduced Sysbench OLTP-RW Functional suite from paper configs."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / "reproduction/configs/common/sysbench_oltp_rw_hi_p99"
METHODS = [
    ("fixed", "fixed", "Fixed", "#7F8C8D"),
    ("mlos", "mlos_app", "MLOS", "#E67E22"),
    ("bayesian", "bayesian", "Bayesian", "#9467BD"),
    ("dqn", "dqn", "DQN", "#2CA02C"),
    ("qlearning", "qlearning", "Q-learning", "#D62728"),
    ("sematune_single", "single_instant", "SemaTune Single", "#56B4E9"),
    ("sematune_dual", "sematune_app", "SemaTune Dual", "#0072B2"),
    ("sematune_trim", "sematune_trim_app", "SemaTune-Trim", "#009E73"),
]

# This is the configuration selected by the final Actor in the first archived
# Sysbench campaign under ``all_results/paper_evaluation``.  The offline trace
# deliberately replays a Sysbench observation, rather than the old TPC-C toy
# policy.  It is evidence of the control path, not a promise that every host
# will reproduce the paper's speedup in ten windows.
ARCHIVED_SYSBENCH_FINAL = {
    "min_granularity_ns": 500000,
    "latency_ns": 1000000,
    "cstate_max": "POLL",
    "napi_busy_poll": 200,
    "wakeup_granularity_ns": 500000,
    "migration_cost_ns": 200000,
    "max_perf_pct": 100,
    "min_perf_pct": 100,
}


def build_sysbench_trace() -> dict:
    """Build a role-complete deterministic policy from archived Sysbench data."""
    probes = [
        {"min_granularity_ns": 100000, "latency_ns": 10000000, "cstate_max": "C1", "napi_busy_poll": 50, "wakeup_granularity_ns": 100000, "migration_cost_ns": 1000000, "max_perf_pct": 100, "min_perf_pct": 10},
        ARCHIVED_SYSBENCH_FINAL,
        {**ARCHIVED_SYSBENCH_FINAL, "napi_busy_poll": 100},
        {**ARCHIVED_SYSBENCH_FINAL, "min_perf_pct": 80},
        {**ARCHIVED_SYSBENCH_FINAL, "cstate_max": "C1"},
        {**ARCHIVED_SYSBENCH_FINAL, "latency_ns": 2000000},
        {**ARCHIVED_SYSBENCH_FINAL, "migration_cost_ns": 500000},
        {**ARCHIVED_SYSBENCH_FINAL, "wakeup_granularity_ns": 1000000},
        {**ARCHIVED_SYSBENCH_FINAL, "min_granularity_ns": 1000000},
        ARCHIVED_SYSBENCH_FINAL,
    ]
    history = []
    for iteration, probe in enumerate(probes):
        history.append({
            "iteration": iteration,
            "responses": {
                "quick": {
                    "parameters": probe,
                    "confidence": 0.8,
                    "converged": False,
                    "justification": "Replay a bounded Sysbench OLTP-RW probe from the archived eight-knob search region.",
                },
                "reasoning": {
                    "parameters": ARCHIVED_SYSBENCH_FINAL,
                    "confidence": 0.9,
                    "converged": False,
                    "justification": "Retain the archived Actor incumbent while the Speculator checks nearby configurations.",
                    "suggested_ranges": {},
                    "eliminated_params": [],
                },
            },
        })
    history.append({
        "iteration": 10,
        "responses": {
            "reasoning_final": {
                "parameters": ARCHIVED_SYSBENCH_FINAL,
                "confidence": 0.95,
                "converged": True,
                "justification": "Freeze the final Actor configuration recorded by the archived Sysbench OLTP-RW campaign for stable validation.",
                "suggested_ranges": {},
                "eliminated_params": [],
            }
        },
    })
    return {
        "schema_version": 1,
        "description": (
            "Deterministic offline replay derived from the archived Sysbench "
            "OLTP-RW Actor configuration; no provider request is made."
        ),
        "provider_requests": 0,
        "history": history,
    }


def main() -> int:
    manifest = {
        "schema_version": 1,
        "workload": "Sysbench OLTP-RW",
        "tuning_windows": 10,
        "stable_windows": 5,
        "window_duration_seconds": 10,
        "methods": [],
    }
    for method, source_name, label, color in METHODS:
        source = SOURCE / f"{source_name}.json"
        config = copy.deepcopy(json.loads(source.read_text(encoding="utf-8")))
        config.pop("experiment_profile", None)
        config.update({
            "max_iterations": 10,
            "post_tuning_windows": 5,
            "window_duration": 10,
            "respect_config_window_duration": True,
            "results_dir": f"results/functional_sysbench/{method}",
            "llm_api_key": None,
            "openrouter_api_key": None,
            "previous_run_gist": None,
            "llm_api_log_enabled": False,
            "llm_replay_file": None,
            "sysbench_password": "",
        })
        if method == "bayesian":
            config["bayesian_n_trials"] = 10
        elif method == "dqn":
            config.update({"dqn_max_actions": 1000, "dqn_batch_size": 8})
        elif method == "qlearning":
            config["qlearning_max_actions"] = 1000
        elif method == "mlos":
            config.update({"mlos_max_trials": 10, "mlos_n_random_init": 3})
        elif method == "sematune_trim":
            config.update({
                "trimming_cycles": 5,
                "mlos_max_trials": 10,
                "mlos_n_random_init": 3,
            })
        output = HERE / f"sysbench_{method}.json"
        output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["methods"].append({
            "id": method,
            "label": label,
            "color": color,
            "config": output.name,
            "paper_config": source.relative_to(ROOT).as_posix(),
            "results_subdir": method,
        })

    (HERE / "sysbench_suite.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    trace = build_sysbench_trace()
    (HERE / "sysbench_trace_replay.json").write_text(
        json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(METHODS)} reduced Sysbench OLTP-RW configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
