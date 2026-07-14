#!/usr/bin/env python3
"""Derive the reduced Sysbench OLTP-RW Functional suite from paper configs."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FUNCTIONAL_DIR = ROOT / "functional_example"
SOURCE = ROOT / "reproduction/configs/common/sysbench_oltp_rw_hi_p99"
UNRELATED_PREFIXES = ("benchbase_", "dcperf_", "django_", "mutilate_", "tailbench_")
UNRELATED_KEYS = {"respect_config_window_duration", "sysbench_cpu_max_prime"}
METHODS = [
    ("fixed", "fixed", "Fixed", "#7F8C8D"),
    ("mlos", "mlos_app", "MLOS App", "#E67E22"),
    ("mlos_ipc", "mlos_ipc", "MLOS IPC", "#F39C12"),
    ("mlos_cache", "mlos_cache", "MLOS Cache", "#D35400"),
    ("bayesian", "bayesian", "Bayesian", "#9467BD"),
    ("dqn", "dqn", "DQN", "#2CA02C"),
    ("qlearning", "qlearning", "Q-learning", "#D62728"),
    ("sematune_single", "single_instant", "TuxBot Single", "#56B4E9"),
    ("sematune_dual", "sematune_app", "TuxBot App", "#0072B2"),
    ("sematune_system", "sematune_system", "TuxBot System", "#3498DB"),
    ("sematune_ipc", "sematune_ipc", "TuxBot IPC", "#1F618D"),
    ("sematune_trim", "sematune_trim_app", "TuxBot-Trim App", "#009E73"),
    ("sematune_trim_ipc", "sematune_trim_ipc", "TuxBot-Trim IPC", "#27AE60"),
    ("sematune_trim_cache", "sematune_trim_cache", "TuxBot-Trim Cache", "#117A65"),
]

TUNING_WINDOWS = 5
STABLE_WINDOWS = 5
WINDOW_DURATION_SECONDS = 10
FUNCTIONAL_MODEL = "gemini-2.5-flash-lite"

def main() -> int:
    manifest = {
        "schema_version": 1,
        "workload": "Sysbench OLTP-RW",
        "tuning_windows": TUNING_WINDOWS,
        "stable_windows": STABLE_WINDOWS,
        "window_duration_seconds": WINDOW_DURATION_SECONDS,
        "methods": [],
    }
    for method, source_name, label, color in METHODS:
        source = SOURCE / f"{source_name}.json"
        config = copy.deepcopy(json.loads(source.read_text(encoding="utf-8")))
        config.pop("experiment_profile", None)
        config = {
            key: value
            for key, value in config.items()
            if key not in UNRELATED_KEYS
            and not key.startswith(UNRELATED_PREFIXES)
        }
        config.update({
            "max_iterations": TUNING_WINDOWS,
            "post_tuning_windows": STABLE_WINDOWS,
            "window_duration": WINDOW_DURATION_SECONDS,
            "results_dir": f"results/functional_sysbench/{method}",
            "llm_api_key": None,
            "openrouter_api_key": None,
            "previous_run_gist": None,
            "llm_api_log_enabled": False,
            "llm_replay_file": None,
            "sysbench_password": "",
        })
        if config.get("tuner_type") == "llm":
            config["llm_model_name"] = FUNCTIONAL_MODEL
            config["llm_secondary_model"] = FUNCTIONAL_MODEL
            if config.get("llm_actor_model") and config.get("llm_speculator_model"):
                config["llm_actor_model"] = FUNCTIONAL_MODEL
                config["llm_speculator_model"] = FUNCTIONAL_MODEL
        if config.get("trimming_enabled"):
            config["llm_model_name"] = FUNCTIONAL_MODEL
            config["llm_secondary_model"] = FUNCTIONAL_MODEL
            config["trimming_model_name"] = FUNCTIONAL_MODEL

        if method == "bayesian":
            config["bayesian_n_trials"] = TUNING_WINDOWS
        elif method == "dqn":
            config.update({"dqn_max_actions": 1000, "dqn_batch_size": 4})
        elif method == "qlearning":
            config["qlearning_max_actions"] = 1000
        elif method.startswith("mlos"):
            config.update({"mlos_max_trials": TUNING_WINDOWS, "mlos_n_random_init": 2})
        elif method.startswith("sematune_trim"):
            config.update({
                "trimming_cycles": TUNING_WINDOWS - 1,
                "mlos_max_trials": TUNING_WINDOWS,
                "mlos_n_random_init": 2,
            })
        output = FUNCTIONAL_DIR / f"sysbench_{method}.json"
        output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        method_record = {
            "id": method,
            "label": label,
            "color": color,
            "config": output.name,
            "paper_config": source.relative_to(ROOT).as_posix(),
            "results_subdir": method,
        }
        if method.startswith("sematune"):
            method_record["trace"] = f"traces/sysbench_{method}_trace.json"
        manifest["methods"].append(method_record)

    (FUNCTIONAL_DIR / "sysbench_suite.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(METHODS)} reduced Sysbench OLTP-RW configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
