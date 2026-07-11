#!/usr/bin/env python3
"""Derive the reduced TPC-C Functional configs from paper-effective configs."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / "reproduction" / "configs" / "common" / "tpcc_hi_p99"

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


def main() -> int:
    manifest = {"schema_version": 1, "workload": "TPC-C", "tuning_windows": 10, "stable_windows": 5, "window_duration_seconds": 5, "methods": []}
    for method, source_name, label, color in METHODS:
        source = SOURCE / f"{source_name}.json"
        config = copy.deepcopy(json.loads(source.read_text(encoding="utf-8")))
        # Experiment profiles encode the original 30+20/50-window campaigns and
        # would override the reduced Functional budget during config loading.
        config.pop("experiment_profile", None)
        config["max_iterations"] = 10
        config["post_tuning_windows"] = 5
        config["window_duration"] = 5
        config["respect_config_window_duration"] = True
        config["benchbase_timeout_retries"] = 1
        config["results_dir"] = f"results/functional_tpcc/{method}"
        config["llm_api_key"] = None
        config["openrouter_api_key"] = None
        config["previous_run_gist"] = None
        config["llm_api_log_enabled"] = False
        config["llm_replay_file"] = None
        if method == "bayesian":
            config["bayesian_n_trials"] = 10
        elif method == "dqn":
            config["dqn_max_actions"] = 1000
            config["dqn_batch_size"] = 8
        elif method == "qlearning":
            config["qlearning_max_actions"] = 1000
        elif method == "mlos":
            config["mlos_max_trials"] = 10
            config["mlos_n_random_init"] = 3
        elif method == "sematune_trim":
            config["trimming_cycles"] = 5
            config["mlos_max_trials"] = 10
            config["mlos_n_random_init"] = 3
        output = HERE / f"tpcc_{method}.json"
        output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["methods"].append(
            {
                "id": method,
                "label": label,
                "color": color,
                "config": output.name,
                "paper_config": source.relative_to(ROOT).as_posix(),
                "results_subdir": method,
            }
        )
    (HERE / "tpcc_suite.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace = json.loads((HERE / "mock_replay.json").read_text(encoding="utf-8"))
    trace["description"] = (
        "Deterministic offline trace for the reduced TPC-C Single, Dual, and "
        "SemaTune-Trim Functional runs; no provider request is made."
    )
    range_actions = {
        0: {},
        1: {"min_granularity_ns": {"min": 100000, "max": 500000}},
        2: {"latency_ns": {"min": 1000000, "max": 6000000}},
        3: {"migration_cost_ns": {"min": 100000, "max": 180000}},
        4: {"min_perf_pct": {"min": 45, "max": 100}},
    }
    for entry in trace["history"]:
        iteration = int(entry["iteration"])
        reasoning = (entry.get("responses") or {}).get("reasoning")
        if reasoning is not None and iteration in range_actions:
            reasoning["suggested_ranges"] = range_actions[iteration]
            reasoning["eliminated_params"] = []
    (HERE / "tpcc_trace_replay.json").write_text(
        json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(METHODS)} reduced TPC-C configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
