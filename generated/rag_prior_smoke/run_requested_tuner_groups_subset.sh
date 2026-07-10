#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="${STAGE_DIR:-/mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset}"
NUM_RUNS="${NUM_RUNS:-5}"
RUNNER="${RUNNER:-/mydata/os-param-tuning/run_configs_batch.sh}"

usage() {
  cat <<'EOF'
Usage:
  run_requested_tuner_groups_subset.sh <group|all> [num_runs]

Groups:
  fixed
  app_nomem
  indirect_nomem
  sys_top1_raw
  app_top1_raw
  sys_top3_raw
  app_top3_raw
  sys_top3_raw_unseen
  app_top3_raw_unseen
  sys_last1_raw
  app_last1_raw
  all
EOF
}

if [[ "${1:-}" == "" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

GROUP="$1"
if [[ "${2:-}" != "" ]]; then
  NUM_RUNS="$2"
fi

run_group() {
  local tuner_suffix="$1"
  "${RUNNER}" "${STAGE_DIR}" "${NUM_RUNS}" --tuners "${tuner_suffix}"
}

case "${GROUP}" in
  fixed) run_group "fixed" ;;
  app_nomem) run_group "llm_dual_app_metrics_final_actor" ;;
  indirect_nomem) run_group "llm_dual_indirect_all_mode3_final_actor" ;;
  sys_top1_raw) run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor" ;;
  app_top1_raw) run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor" ;;
  sys_top3_raw) run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor" ;;
  app_top3_raw) run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor" ;;
  sys_top3_raw_unseen) run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor" ;;
  app_top3_raw_unseen) run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor" ;;
  sys_last1_raw) run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor" ;;
  app_last1_raw) run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor" ;;
  all)
    run_group "fixed"
    run_group "llm_dual_app_metrics_final_actor"
    run_group "llm_dual_indirect_all_mode3_final_actor"
    run_group "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"
    run_group "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"
    run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"
    run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"
    run_group "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"
    run_group "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"
    run_group "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"
    run_group "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"
    ;;
  *)
    echo "Unknown group: ${GROUP}" >&2
    usage >&2
    exit 1
    ;;
esac
