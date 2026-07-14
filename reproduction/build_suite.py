#!/usr/bin/env python3
"""Build the compact, one-rerun paper reproduction suite from archived histories.

The archived histories contain the effective configuration used by each paper
run.  This program extracts one complete configuration per required result
series, removes credential fields, and writes a manifest that records both the
source history and every plot that reuses the run.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = REPO_ROOT / "all_results" / "paper_evaluation"
OUTPUT_ROOT = REPO_ROOT / "reproduction"
LOCK_PATH = Path("/tmp/sematune-os-param-tuning-reproduction.lock")

RETRY_ROOTS = {
    "dcperf_spark_tput": "results_config_full_param_dcperf_spark_tput_retry",
    "masstree_hi_p99": "results_config_full_param_masstree_hi_p99_20260306_051050_retry",
    "mutilate_high": "results_config_full_param_mutilate_high_retry",
    "sibench_hi_p99": "results_config_full_param_sibench_hi_p99_20260305_231127_retry",
    "silo_hi_p99": "results_config_full_param_silo_hi_p99_20260305_234901_retry",
    "sphinx_tput_max": "results_config_full_param_sphinx_tput_max_20260306_112505_retry",
    "sysbench_cpu_tput": "results_config_full_param_sysbench_cpu_tput_20260305_203042_retry",
    "sysbench_oltp_rw_hi_p99": "results_config_full_param_sysbench_oltp_rw_hi_p99_20260305_203050_retry",
    "tpcc_hi_p99": "results_config_full_param_tpcc_hi_p99_20260306_080130_retry",
    "twitter_p99": "results_config_full_param_twitter_p99_retry",
    "wikipedia_p99": "results_config_full_param_wikipedia_p99_retry",
    "xapian_hi_p99": "results_config_full_param_xapian_hi_p99_20260306_023417_retry",
    "ycsb_hi_p99": "results_config_full_param_ycsb_hi_p99_20260306_020958_retry",
}

# These are exactly the fixed roots consulted by artifact_plots/common.sh.
FIXED_ROOTS = {
    "masstree_hi_p99": "results_config_full_param_masstree_hi_p99_new",
    "sibench_hi_p99": "results_config_full_param_sibench_hi_p99_new",
    "silo_hi_p99": "results_config_full_param_silo_hi_p99_new",
    "sphinx_tput_max": "results_config_full_param_sphinx_tput_max_new",
    "sysbench_cpu_tput": "results_config_full_param_sysbench_cpu_tput_new",
    "sysbench_oltp_rw_hi_p99": "results_config_full_param_sysbench_oltp_rw_hi_p99_new",
    "tpcc_hi_p99": "results_config_full_param_tpcc_hi_p99_new",
    "twitter_p99": "results_config_full_param_twitter_hi_p99_new",
    "xapian_hi_p99": "results_config_full_param_xapian_hi_p99_new",
    "ycsb_hi_p99": "results_config_full_param_ycsb_hi_p99_new",
}

COMMON_METHODS = {
    "sematune_app": (["llm_dual_app_metrics_final_actor"], {1, 2, 3, 4}),
    "sematune_system": (
        ["llm_dual_system_metrics_plain_final_actor_recovered_20260313", "llm_dual_system_metrics_plain_final_actor"],
        {2},
    ),
    "sematune_ipc": (["llm_dual_ipc_final_actor"], {2}),
    "sematune_trim_app": (["mlos_trimming_aggressive", "mlos_trimming"], {1, 2, 3, 4}),
    "sematune_trim_ipc": (["mlos_trimming_aggressive_ipc", "mlos_trimming_ipc"], {2}),
    "sematune_trim_cache": (
        ["mlos_trimming_aggressive_cache_misses_max", "mlos_trimming_cache_misses_max"],
        {2},
    ),
    "mlos_app": (["mlos_50_tuning_only", "mlos"], {1, 2, 3, 4}),
    "mlos_ipc": (["mlos_ipc_50_tuning_only", "mlos_ipc"], {2}),
    "mlos_cache": (["mlos_cache_misses_50_tuning_only", "mlos_cache_misses"], {2}),
    "bayesian": (["bayesian"], {1}),
    "dqn": (["dqn"], {1}),
    "qlearning": (["qlearning"], {1}),
    "single_reasoning": (["llm_reasoning_app_metrics_final_actor"], {3}),
    "single_instant": (["llm_app_metrics_final_actor"], {3}),
}

CANONICAL_TARGET_NAMES = {
    key: candidates[-1] if key == "sematune_system" else candidates[0]
    for key, (candidates, _) in COMMON_METHODS.items()
}

PARAM_WORKLOADS = {"silo_hi_p99", "tpcc_hi_p99", "sysbench_oltp_rw_hi_p99"}
PARAM_METHODS = {"llm_dual_app_metrics_final_actor", "llm_trimming", "mlos"}
RAG_ROOT_TO_COMMON = {
    "silo": "silo_hi_p99",
    "tpcc": "tpcc_hi_p99",
    "sysbench_oltp": "sysbench_oltp_rw_hi_p99",
}
RAG_MEMORY_METHODS = {
    "rag_llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor",
    "rag_llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor",
    "rag_llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor",
    "rag_llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor",
}

PLOT_INFO = {
    "6": {"claim": "end-to-end performance and catastrophic-region avoidance", "wrapper": "scripts/artifact_plots/generate_plot_1.sh"},
    "7": {"claim": "application metrics versus indirect system signals", "wrapper": "scripts/artifact_plots/generate_plot_2.sh"},
    "8": {"claim": "dual-loop versus single-loop quality and cost", "wrapper": "scripts/artifact_plots/generate_plot_3.sh"},
    "9": {"claim": "tuning-phase robustness", "wrapper": "scripts/artifact_plots/generate_plot_4.sh"},
    "10": {"claim": "parameter-count scaling", "wrapper": "scripts/artifact_plots/generate_plot_5.sh"},
    "11": {"claim": "cross-run memory on unseen workloads", "wrapper": "scripts/artifact_plots/generate_plot_6.sh"},
    "12": {"claim": "motivation examples", "wrapper": "scripts/artifact_plots/generate_plot_7.sh"},
}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def history_score(path: Path, data: dict[str, Any]) -> tuple[int, int, str]:
    history = data.get("history")
    entries = len(history) if isinstance(history, list) else 0
    completed = str(data.get("terminated_reason") or data.get("reason") or "").lower()
    success = int(not completed or completed in {"complete", "completed", "max_iterations", "converged"})
    return success, entries, path.name


def choose_history(directory: Path) -> tuple[Path, dict[str, Any]]:
    choices: list[tuple[tuple[int, int, str], Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        data = load_json(path)
        if data is None or not isinstance(data.get("config"), dict):
            continue
        choices.append((history_score(path, data), path, data))
    if not choices:
        raise RuntimeError(f"no history with an embedded config in {directory}")
    _, path, data = max(choices, key=lambda item: item[0])
    return path, data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_config(config: dict[str, Any], target: str) -> dict[str, Any]:
    cleaned = dict(config)
    for key in ("llm_api_key", "openrouter_api_key"):
        if key in cleaned:
            cleaned[key] = None
    if "sysbench_password" in cleaned:
        cleaned["sysbench_password"] = ""
    cleaned["results_dir"] = f"results/reproduced/raw/{target}"
    return cleaned


def find_method_dir(workload: str, candidates: Iterable[str]) -> Path:
    retry = ARCHIVE_ROOT / RETRY_ROOTS[workload] / workload
    roots = [retry]
    fixed_name = FIXED_ROOTS.get(workload)
    if fixed_name:
        roots.append(ARCHIVE_ROOT / fixed_name / workload)
    for root in roots:
        for candidate in candidates:
            path = root / candidate
            if path.is_dir():
                return path
    raise RuntimeError(f"missing {workload} method candidates: {', '.join(candidates)}")


def find_fixed_dir(workload: str) -> tuple[Path, str]:
    fixed_name = FIXED_ROOTS.get(workload)
    if fixed_name:
        target = f"{fixed_name}/{workload}/fixed"
        source = ARCHIVE_ROOT / target
        if source.is_dir():
            return source, target
    retry_root = ARCHIVE_ROOT / RETRY_ROOTS[workload]
    for source in (retry_root / workload / "fixed", retry_root / "fixed"):
        if source.is_dir():
            target = f"{RETRY_ROOTS[workload]}/{workload}/fixed"
            return source, target
    raise RuntimeError(f"missing fixed history for {workload}")


def config_relpath(kind: str, *parts: str) -> str:
    return (Path("configs") / kind / Path(*parts)).with_suffix(".json").as_posix()


def add_job(
    jobs: dict[str, dict[str, Any]],
    *,
    job_id: str,
    source_dir: Path,
    target: str,
    plots: set[int],
    config_path: str,
    kind: str,
) -> None:
    # The construction rules retain their original zero-context evaluation
    # indices (1-7); the public manifest uses the paper's Plot 6-12 numbering.
    plots = {plot + 5 for plot in plots}
    if job_id in jobs:
        jobs[job_id]["plots"] = sorted(set(jobs[job_id]["plots"]) | plots)
        return
    source, data = choose_history(source_dir)
    config = clean_config(data["config"], target)
    output = OUTPUT_ROOT / config_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    history = data.get("history")
    jobs[job_id] = {
        "id": job_id,
        "kind": kind,
        "config": config_path,
        "target_results_dir": target,
        "source_history": source.relative_to(REPO_ROOT).as_posix(),
        "source_history_sha256": sha256(source),
        "source_windows": len(history) if isinstance(history, list) else 0,
        "plots": sorted(plots),
    }


def add_common_jobs(jobs: dict[str, dict[str, Any]]) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for workload in RETRY_ROOTS:
        source_fixed, fixed_target = find_fixed_dir(workload)
        fixed_plots = {1, 2, 3}
        if workload != "mutilate_high":
            fixed_plots.add(4)
        if workload in PARAM_WORKLOADS:
            fixed_plots.add(5)
            fixed_plots.add(6)
        if workload in {"wikipedia_p99", "tpcc_hi_p99"}:
            fixed_plots.add(7)
        job_id = f"common:{workload}:fixed"
        add_job(
            jobs,
            job_id=job_id,
            source_dir=source_fixed,
            target=fixed_target,
            plots=fixed_plots,
            config_path=config_relpath("common", workload, "fixed"),
            kind="common",
        )
        lookup[(workload, "fixed")] = job_id

        for method, (candidates, base_plots) in COMMON_METHODS.items():
            plots = set(base_plots)
            if workload == "mutilate_high":
                plots.discard(4)
            if workload in PARAM_WORKLOADS and method in {"sematune_app", "sematune_trim_app", "mlos_app"}:
                plots.add(5)
            if workload in PARAM_WORKLOADS and method == "sematune_app":
                plots.add(6)
            if workload == "wikipedia_p99" and method in {"mlos_app", "mlos_ipc", "mlos_cache"}:
                plots.add(7)
            if workload == "tpcc_hi_p99" and method == "mlos_app":
                plots.add(7)
            source_dir = find_method_dir(workload, candidates)
            target_name = CANONICAL_TARGET_NAMES[method]
            target = f"{RETRY_ROOTS[workload]}/{workload}/{target_name}"
            job_id = f"common:{workload}:{method}"
            add_job(
                jobs,
                job_id=job_id,
                source_dir=source_dir,
                target=target,
                plots=plots,
                config_path=config_relpath("common", workload, method),
                kind="common",
            )
            lookup[(workload, method)] = job_id

    # Plot 11's System/No-Memory series is a distinct indirect-all configuration.
    for workload in sorted(PARAM_WORKLOADS):
        method = "sematune_indirect_all"
        source_dir = find_method_dir(workload, ["llm_dual_indirect_all_mode3_final_actor"])
        target = f"{RETRY_ROOTS[workload]}/{workload}/llm_dual_indirect_all_mode3_final_actor"
        job_id = f"common:{workload}:{method}"
        add_job(
            jobs,
            job_id=job_id,
            source_dir=source_dir,
            target=target,
            plots={6},
            config_path=config_relpath("common", workload, method),
            kind="common",
        )
        lookup[(workload, method)] = job_id
    return lookup


def add_parameter_jobs(jobs: dict[str, dict[str, Any]]) -> None:
    root = ARCHIVE_ROOT / "results_params"
    for source_dir in sorted(path for path in root.rglob("*") if path.is_dir() and path.name in PARAM_METHODS):
        relative = source_dir.relative_to(ARCHIVE_ROOT).as_posix()
        parts = source_dir.relative_to(root).parts
        if "ablation_params" not in parts:
            continue
        marker = parts.index("ablation_params")
        if len(parts) < marker + 4:
            continue
        root_name, count_name, workload, method = parts[0], parts[marker + 1], parts[marker + 2], parts[marker + 3]
        plots = {5}
        if root_name == "tpcc_p99_final" and count_name in {"1_param", "2_param", "32_param"} and method == "mlos":
            plots.add(7)
        job_id = f"parameter:{root_name}:{count_name}:{workload}:{method}"
        add_job(
            jobs,
            job_id=job_id,
            source_dir=source_dir,
            target=relative,
            plots=plots,
            config_path=config_relpath("parameter_count", root_name, count_name, workload, method),
            kind="parameter_count",
        )


def add_memory_jobs_and_aliases(
    jobs: dict[str, dict[str, Any]], common: dict[tuple[str, str], str]
) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    rag_root = ARCHIVE_ROOT / "results_rag"
    for rag_name, workload in RAG_ROOT_TO_COMMON.items():
        base = rag_root / rag_name
        aliases.extend(
            [
                {
                    "target_results_dir": f"results_rag/{rag_name}/fixed",
                    "source_job": common[(workload, "fixed")],
                    "reason": "Plot 11 reuses the regular Fixed run.",
                },
                {
                    "target_results_dir": f"results_rag/{rag_name}/llm_dual_app_metrics_final_actor",
                    "source_job": common[(workload, "sematune_app")],
                    "reason": "Plot 11 reuses the regular App/No-Memory run.",
                },
                {
                    "target_results_dir": f"results_rag/{rag_name}/llm_dual_indirect_all_mode3_final_actor",
                    "source_job": common[(workload, "sematune_indirect_all")],
                    "reason": "Plot 11 reuses the regular System/No-Memory run.",
                },
            ]
        )
        for method in sorted(RAG_MEMORY_METHODS):
            source_dir = base / method
            if not source_dir.is_dir():
                raise RuntimeError(f"missing Plot 11 memory directory: {source_dir}")
            target = source_dir.relative_to(ARCHIVE_ROOT).as_posix()
            job_id = f"memory:{rag_name}:{method}"
            add_job(
                jobs,
                job_id=job_id,
                source_dir=source_dir,
                target=target,
                plots={6},
                config_path=config_relpath("memory", rag_name, method),
                kind="memory",
            )
    return aliases


def build() -> dict[str, Any]:
    configs = OUTPUT_ROOT / "configs"
    if configs.exists():
        shutil.rmtree(configs)
    jobs: dict[str, dict[str, Any]] = {}
    common = add_common_jobs(jobs)
    add_parameter_jobs(jobs)
    aliases = add_memory_jobs_and_aliases(jobs, common)

    plot_map: dict[str, dict[str, Any]] = {}
    for plot, info in PLOT_INFO.items():
        plot_jobs = sorted(job_id for job_id, job in jobs.items() if int(plot) in job["plots"])
        plot_aliases = [alias["target_results_dir"] for alias in aliases if int(plot) == 11]
        plot_map[plot] = {**info, "jobs": plot_jobs, "reused_result_aliases": plot_aliases}

    manifest = {
        "schema_version": 1,
        "reruns_per_configuration": 1,
        "archive_root": ARCHIVE_ROOT.relative_to(REPO_ROOT).as_posix(),
        "description": "One fresh rerun per unique paper configuration; shared results are consumed by every applicable plot.",
        "plots": plot_map,
        "jobs": [jobs[key] for key in sorted(jobs)],
        "aliases": aliases,
        "archived_reuse": [
            {
                "plots": [8],
                "path": "artifact/reference_data/dual_vs_single_costs.csv",
                "reason": "The sampled-session cost CSV was not retained; the accepted-paper displayed costs are used.",
            },
            {
                "plots": [10],
                "path": "paper_evaluation_plots/latency_by_params.csv",
                "reason": "Provider latency is time-varying and the submitted latency table is explicitly reused.",
            },
        ],
    }
    (OUTPUT_ROOT / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Rebuild and print only suite counts.")
    parser.parse_args()
    with LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        manifest = build()
    print(
        f"wrote {len(manifest['jobs'])} unique one-rerun configs, "
        f"{len(manifest['aliases'])} reuse aliases, and mappings for {len(manifest['plots'])} plots"
    )
    for plot, data in manifest["plots"].items():
        print(f"plot {plot}: {len(data['jobs'])} configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
