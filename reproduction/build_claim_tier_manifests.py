#!/usr/bin/env python3
"""Build the reviewer-facing extended and full C1--C4 manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPRO_ROOT = Path(__file__).resolve().parent
BASE_MANIFEST = REPRO_ROOT / "experiment_manifest.json"

THREE_WORKLOADS = [
    "silo_hi_p99",
    "tpcc_hi_p99",
    "sysbench_oltp_rw_hi_p99",
]
FULL_WORKLOADS = [
    "silo_hi_p99",
    "tpcc_hi_p99",
    "sysbench_oltp_rw_hi_p99",
    "sibench_hi_p99",
    "twitter_p99",
    "wikipedia_p99",
    "ycsb_hi_p99",
    "masstree_hi_p99",
    "sphinx_tput_max",
    "xapian_hi_p99",
    "sysbench_cpu_tput",
]
PLOT6_METHODS = [
    "fixed",
    "sematune_app",
    "sematune_trim_app",
    "mlos_app",
    "bayesian",
    "dqn",
    "qlearning",
]
PLOT7_METHODS = [
    "fixed",
    "sematune_app",
    "sematune_system",
    "sematune_ipc",
    "sematune_trim_app",
    "sematune_trim_ipc",
    "sematune_trim_cache",
    "mlos_app",
    "mlos_ipc",
    "mlos_cache",
]
PARAMETER_FAMILIES = {
    "silo_hi_p99": "silo_hi_p99_final",
    "tpcc_hi_p99": "tpcc_p99_final",
    "sysbench_oltp_rw_hi_p99": "sysbench_oltp_rw_final",
}
PARAMETER_METHODS = {
    "sematune_app": "llm_dual_app_metrics_final_actor",
    "sematune_trim_app": "llm_trimming",
    "mlos_app": "mlos",
}
METHOD_KNOB_COUNTS = {
    "sematune_app": [2, 8, 16, 41],
    "sematune_trim_app": [2, 8, 16],
    "mlos_app": [2, 8, 16],
}


def completion(config: dict[str, Any]) -> dict[str, Any]:
    tuning = int(config.get("max_iterations", 0))
    stable = int(config.get("post_tuning_windows", 0))
    value: dict[str, Any] = {
        "measurement_windows": tuning + stable,
        "tuning_windows": tuning,
        "post_tuning_windows": stable,
        "optimization_metric": str(config["optimization_metric"]),
        "allow_iteration_zero": True,
    }
    if config.get("llm_actor_model") and config.get("llm_speculator_model"):
        value.update({"required_mode": "actor-speculator", "require_optimizer_gist": True})
    return value


def common_id(workload: str, method: str) -> str:
    return f"common:{workload}:{method}"


def parameter_id(workload: str, count: int, method: str) -> str:
    return (
        f"parameter:{PARAMETER_FAMILIES[workload]}:{count}_param:{workload}:"
        f"{PARAMETER_METHODS[method]}"
    )


def build_manifest(base: dict[str, Any], workloads: list[str], tier: str) -> dict[str, Any]:
    base_jobs = {job["id"]: job for job in base["jobs"]}
    plot_jobs: dict[int, set[str]] = {6: set(), 7: set(), 10: set()}
    for workload in workloads:
        plot_jobs[6].update(common_id(workload, method) for method in PLOT6_METHODS)
        plot_jobs[7].update(common_id(workload, method) for method in PLOT7_METHODS)

    # C4 remains the paper's three-workload sweep even in --full. The 8-knob
    # points reuse the corresponding common App runs.
    for workload in THREE_WORKLOADS:
        plot_jobs[10].add(common_id(workload, "fixed"))
        for method, counts in METHOD_KNOB_COUNTS.items():
            plot_jobs[10].add(common_id(workload, method))
            for count in counts:
                if count != 8:
                    plot_jobs[10].add(parameter_id(workload, count, method))

    selected = set().union(*plot_jobs.values())
    unknown = sorted(selected - base_jobs.keys())
    if unknown:
        raise ValueError(f"base manifest is missing required jobs: {unknown}")

    jobs: list[dict[str, Any]] = []
    total_windows = 0
    for job_id in sorted(selected):
        source = base_jobs[job_id]
        config = json.loads((REPRO_ROOT / source["config"]).read_text(encoding="utf-8"))
        runtime_overrides: dict[str, Any] = {}
        if job_id.startswith("common:") and job_id.endswith(":fixed"):
            runtime_overrides = {"max_iterations": 30, "post_tuning_windows": 20}
            config.update(runtime_overrides)
        contract = completion(config)
        total_windows += int(contract["measurement_windows"])
        override: dict[str, Any] = {
            "id": job_id,
            "plots": [plot for plot in (6, 7, 10) if job_id in plot_jobs[plot]],
            "completion": contract,
        }
        if runtime_overrides:
            override["runtime_overrides"] = runtime_overrides
        jobs.append(override)

    return {
        "schema_version": 1,
        "workflow": f"claims-{tier}",
        "selection_label": "plot",
        "base_manifest": BASE_MANIFEST.name,
        "reruns_per_configuration": 1,
        "tier": tier,
        "claims": ["C1", "C2", "C3", "C4"],
        "workloads": workloads,
        "parameter_workloads": THREE_WORKLOADS,
        "knob_counts": [2, 8, 16, 41],
        "method_knob_counts": METHOD_KNOB_COUNTS,
        "expected_unique_configurations": len(jobs),
        "expected_measurement_windows": total_windows,
        "plots": {
            str(plot): {
                "claim": {
                    6: "C1/C2 with all requested end-to-end baselines",
                    7: "C3 with all requested App/System/IPC/Cache variants",
                    10: "C4 at 2/8/16/41 knobs; Trim and MLOS omit 41",
                }[plot],
                "paper_plot": plot,
                "jobs": sorted(plot_jobs[plot]),
            }
            for plot in (6, 7, 10)
        },
        "jobs": jobs,
        "aliases": [],
    }


def main() -> int:
    base = json.loads(BASE_MANIFEST.read_text(encoding="utf-8"))
    outputs = {
        "extended": (THREE_WORKLOADS, REPRO_ROOT / "extended_claim_manifest.json"),
        "full": (FULL_WORKLOADS, REPRO_ROOT / "full_claim_manifest.json"),
    }
    for tier, (workloads, path) in outputs.items():
        manifest = build_manifest(base, workloads, tier)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"{tier}: {manifest['expected_unique_configurations']} configurations, "
            f"{manifest['expected_measurement_windows']} windows -> {path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
