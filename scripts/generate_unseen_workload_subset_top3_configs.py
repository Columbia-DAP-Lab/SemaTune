#!/usr/bin/env python3
"""Generate cross-workload top-3 smoke configs for the subset batch stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.generate_rag_prior_configs import (  # noqa: E402
    APP_FAMILY,
    INDIRECT_FAMILY,
    GoogleGenAIPriorTextBackend,
    synthesize_prior_text,
)


SUBSET_WORKLOADS = [
    "silo_hi_p99",
    "tpcc_hi_p99",
    "sysbench_oltp_rw_hi_p99",
    "wikipedia_p99",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-artifacts-root",
        default=str(REPO_ROOT / "generated" / "rag_prior_configs"),
        help="Root containing the full retrieval manifests from generate_rag_prior_configs.py.",
    )
    parser.add_argument(
        "--subset-root",
        default=str(REPO_ROOT / "generated" / "rag_prior_smoke"),
        help="Root containing batch_stage_subset and the generated smoke runner scripts.",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=5,
        help="Run count baked into the generated command scripts.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _family_name_map() -> Dict[str, Any]:
    return {
        "app": APP_FAMILY,
        "indirect": INDIRECT_FAMILY,
    }


def _nonmatching_top3(ranked_candidates: Sequence[Mapping[str, Any]], workload: str) -> List[Dict[str, Any]]:
    filtered = [
        dict(candidate)
        for candidate in ranked_candidates
        if candidate.get("benchmark_family_label") != workload
    ]
    if len(filtered) < 3:
        raise ValueError(f"{workload} has only {len(filtered)} non-matching candidates")
    return filtered[:3]


def _find_stage_config(stage_dir: Path, workload: str, suffix: str) -> Path:
    matches = sorted((stage_dir / workload).glob(f"*{suffix}.json"))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one stage config for {workload} with suffix {suffix}, found {len(matches)}"
        )
    return matches[0]


def _unseen_filename(source_path: Path) -> str:
    name = source_path.name
    if not name.endswith("_final_actor.json"):
        raise ValueError(f"Unexpected source filename: {source_path}")
    return name.replace("_final_actor.json", "_unseen_workload_final_actor.json")


def _unseen_results_leaf(config_payload: Mapping[str, Any]) -> str:
    results_dir = str(config_payload.get("results_dir") or "").strip().rstrip("/")
    leaf = results_dir.split("/")[-1]
    if not leaf.endswith("_final_actor"):
        raise ValueError(f"Unexpected results_dir leaf: {leaf}")
    return leaf.replace("_final_actor", "_unseen_workload_final_actor")


def build_requested_commands(stage_dir: Path, *, num_runs: int) -> List[str]:
    base_commands = [
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "fixed"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_app_metrics_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_indirect_all_mode3_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"',
        f'./run_configs_batch.sh {stage_dir} {num_runs} --tuners "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"',
    ]
    return base_commands


def _group_script_lines(workload: str) -> List[str]:
    return [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'NUM_RUNS="${1:-5}"',
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"',
        'RUNNER="${REPO_ROOT}/run_configs_batch.sh"',
        f'CONFIG_DIR="${{REPO_ROOT}}/generated/rag_prior_smoke/batch_stage_subset/{workload}"',
        "",
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "fixed"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"',
        '"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"',
    ]


def write_requested_commands(path: Path, commands: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    lines.extend(commands)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)


def write_subset_launcher(path: Path, stage_dir: Path, *, num_runs: int) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f'STAGE_DIR="${{STAGE_DIR:-{stage_dir}}}"',
        f'NUM_RUNS="${{NUM_RUNS:-{num_runs}}}"',
        'RUNNER="${RUNNER:-/mydata/os-param-tuning/run_configs_batch.sh}"',
        "",
        "usage() {",
        "  cat <<'EOF'",
        "Usage:",
        "  run_requested_tuner_groups_subset.sh <group|all> [num_runs]",
        "",
        "Groups:",
        "  fixed",
        "  app_nomem",
        "  indirect_nomem",
        "  sys_top1_raw",
        "  app_top1_raw",
        "  sys_top3_raw",
        "  app_top3_raw",
        "  sys_top3_raw_unseen",
        "  app_top3_raw_unseen",
        "  sys_last1_raw",
        "  app_last1_raw",
        "  all",
        "EOF",
        "}",
        "",
        'if [[ "${1:-}" == "" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then',
        "  usage",
        "  exit 0",
        "fi",
        "",
        'GROUP="$1"',
        'if [[ "${2:-}" != "" ]]; then',
        '  NUM_RUNS="$2"',
        "fi",
        "",
        "run_group() {",
        '  local tuner_suffix="$1"',
        '  "${RUNNER}" "${STAGE_DIR}" "${NUM_RUNS}" --tuners "${tuner_suffix}"',
        "}",
        "",
        'case "${GROUP}" in',
        '  fixed) run_group "fixed" ;;',
        '  app_nomem) run_group "llm_dual_app_metrics_final_actor" ;;',
        '  indirect_nomem) run_group "llm_dual_indirect_all_mode3_final_actor" ;;',
        '  sys_top1_raw) run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor" ;;',
        '  app_top1_raw) run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor" ;;',
        '  sys_top3_raw) run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor" ;;',
        '  app_top3_raw) run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor" ;;',
        '  sys_top3_raw_unseen) run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor" ;;',
        '  app_top3_raw_unseen) run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor" ;;',
        '  sys_last1_raw) run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor" ;;',
        '  app_last1_raw) run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor" ;;',
        "  all)",
        '    run_group "fixed"',
        '    run_group "llm_dual_app_metrics_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"',
        '    run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"',
        '    run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"',
        "    ;;",
        "  *)",
        '    echo "Unknown group: ${GROUP}" >&2',
        "    usage >&2",
        "    exit 1",
        "    ;;",
        "esac",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o755)


def generate_unseen_subset_configs(
    *,
    source_artifacts_root: Path,
    subset_root: Path,
    prior_backend: Any,
    num_runs: int,
) -> Dict[str, Any]:
    stage_dir = subset_root / "batch_stage_subset"
    artifacts_root = subset_root / "unseen_workload_top3"
    artifacts_root.mkdir(parents=True, exist_ok=True)

    created_configs: List[str] = []
    created_entries: List[Dict[str, Any]] = []
    family_name_map = _family_name_map()
    family_specs = {
        "app": {
            "family": APP_FAMILY,
            "source_suffix": "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor",
            "source_artifact_family": "app",
        },
        "indirect": {
            "family": INDIRECT_FAMILY,
            "source_suffix": "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor",
            "source_artifact_family": "indirect",
        },
    }

    for workload in SUBSET_WORKLOADS:
        workload_stage_dir = stage_dir / workload
        if not workload_stage_dir.is_dir():
            raise FileNotFoundError(f"Missing subset workload dir: {workload_stage_dir}")

        for key, spec in family_specs.items():
            family = spec["family"]
            source_config = _find_stage_config(stage_dir, workload, spec["source_suffix"])
            source_config_payload = _load_json(source_config)
            target_manifest = _load_json(
                source_artifacts_root / spec["source_artifact_family"] / workload / "target_run_manifest.json"
            )
            retrieval_manifest = _load_json(
                source_artifacts_root / spec["source_artifact_family"] / workload / "raw_to_summary" / "retrieval_manifest.json"
            )
            selected_hits = _nonmatching_top3(retrieval_manifest["ranked_candidates"], workload)
            target_redacted = {
                "objective": {
                    "optimization_metric": target_manifest["optimization_metric"],
                    "optimization_goal": target_manifest["optimization_goal"],
                }
            }
            prior_text, prior_metadata = synthesize_prior_text(
                prior_backend=prior_backend,
                target_redacted=target_redacted,
                family=family,
                retrieval_mode="raw_to_summary",
                selection="top_3",
                selected_hits=selected_hits,
            )

            new_filename = _unseen_filename(source_config)
            new_leaf = _unseen_results_leaf(source_config_payload)
            new_config_payload = dict(source_config_payload)
            new_config_payload["results_dir"] = f"results/{workload}/{new_leaf}/"
            new_config_payload["previous_run_gist"] = prior_text
            new_config_path = workload_stage_dir / new_filename
            new_config_path.write_text(json.dumps(new_config_payload, indent=2) + "\n", encoding="utf-8")
            created_configs.append(str(new_config_path))

            entry_dir = artifacts_root / workload / key
            entry_dir.mkdir(parents=True, exist_ok=True)
            for index, hit in enumerate(selected_hits, start=1):
                (entry_dir / f"retrieved_summary_{index}.txt").write_text(
                    str(hit.get("run_summary_text") or "") + "\n",
                    encoding="utf-8",
                )
            _write_json(
                entry_dir / "retrieval_manifest.json",
                {
                    "workload": workload,
                    "family": key,
                    "selection": "top_3",
                    "retrieval_mode": "raw_to_summary",
                    "exclude_same_workload": True,
                    "selected_hits": selected_hits,
                    "source_config_path": str(source_config),
                    "generated_config_path": str(new_config_path),
                },
            )
            (entry_dir / "prior.txt").write_text(prior_text.strip() + "\n", encoding="utf-8")
            _write_json(
                entry_dir / "prior_metadata.json",
                {
                    "model_name": prior_metadata.get("model_name"),
                    "attempt_count": prior_metadata.get("attempt_count"),
                    "token_usage": prior_metadata.get("token_usage"),
                },
            )
            created_entries.append(
                {
                    "workload": workload,
                    "family": key,
                    "generated_config_path": str(new_config_path),
                    "results_dir_leaf": new_leaf,
                    "selected_source_paths": [hit["source_json_path"] for hit in selected_hits],
                    "selected_workloads": [hit["benchmark_family_label"] for hit in selected_hits],
                }
            )

    commands = build_requested_commands(stage_dir, num_runs=num_runs)
    write_requested_commands(subset_root / "requested_tuner_commands.sh", commands)
    write_subset_launcher(subset_root / "run_requested_tuner_groups_subset.sh", stage_dir, num_runs=num_runs)
    for workload in SUBSET_WORKLOADS:
        path = subset_root / f"run_{workload}_groups.sh"
        path.write_text("\n".join(_group_script_lines(workload)).rstrip() + "\n", encoding="utf-8")
        path.chmod(0o755)

    manifest = {
        "subset_root": str(subset_root),
        "stage_dir": str(stage_dir),
        "manifest_path": str(artifacts_root / "manifest.json"),
        "created_config_paths": created_configs,
        "created_config_count": len(created_configs),
        "entries": created_entries,
        "requested_tuner_commands": commands,
    }
    _write_json(artifacts_root / "manifest.json", manifest)
    return manifest


def main() -> int:
    args = parse_args()
    source_artifacts_root = Path(args.source_artifacts_root)
    subset_root = Path(args.subset_root)
    prior_backend = GoogleGenAIPriorTextBackend()
    manifest = generate_unseen_subset_configs(
        source_artifacts_root=source_artifacts_root,
        subset_root=subset_root,
        prior_backend=prior_backend,
        num_runs=args.num_runs,
    )
    print(f"Created configs: {manifest['created_config_count']}")
    print(f"Manifest: {manifest['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
