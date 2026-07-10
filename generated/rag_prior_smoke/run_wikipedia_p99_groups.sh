#!/usr/bin/env bash
set -euo pipefail

NUM_RUNS="${1:-5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNNER="${REPO_ROOT}/run_configs_batch.sh"
CONFIG_DIR="${REPO_ROOT}/generated/rag_prior_smoke/batch_stage_subset/wikipedia_p99"

"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "fixed"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"
"${RUNNER}" "${CONFIG_DIR}" "${NUM_RUNS}" --tuners "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"
