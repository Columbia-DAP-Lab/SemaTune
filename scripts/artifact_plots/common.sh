#!/usr/bin/env bash

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_ROOT="${SEMATUNE_RESULTS_ROOT:-$REPO_ROOT/all_results/paper_evaluation}"
REFERENCE_ROOT="${SEMATUNE_REFERENCE_PLOTS_ROOT:-$REPO_ROOT/paper_evaluation_plots}"
DEFAULT_OUTPUT_DIR="${SEMATUNE_PLOT_OUTPUT_DIR:-$REPO_ROOT/artifact/generated_plots}"
WORKLOADS="masstree_hi_p99,sibench_hi_p99,silo_hi_p99,sphinx_tput_max,sysbench_cpu_tput,sysbench_oltp_rw_hi_p99,tpcc_hi_p99,twitter_p99,wikipedia_p99,xapian_hi_p99,ycsb_hi_p99,mutilate_high,dcperf_spark_tput"

retry_names=(
  results_config_full_param_dcperf_spark_tput_retry
  results_config_full_param_masstree_hi_p99_20260306_051050_retry
  results_config_full_param_mutilate_high_retry
  results_config_full_param_sibench_hi_p99_20260305_231127_retry
  results_config_full_param_silo_hi_p99_20260305_234901_retry
  results_config_full_param_sphinx_tput_max_20260306_112505_retry
  results_config_full_param_sysbench_cpu_tput_20260305_203042_retry
  results_config_full_param_sysbench_oltp_rw_hi_p99_20260305_203050_retry
  results_config_full_param_tpcc_hi_p99_20260306_080130_retry
  results_config_full_param_twitter_p99_retry
  results_config_full_param_wikipedia_p99_retry
  results_config_full_param_xapian_hi_p99_20260306_023417_retry
  results_config_full_param_ycsb_hi_p99_20260306_020958_retry
)
retry_paths=()
for name in "${retry_names[@]}"; do
  [[ -d "$RESULTS_ROOT/$name" ]] && retry_paths+=("$RESULTS_ROOT/$name")
done

fixed_names=(
  results_config_full_param_masstree_hi_p99_new
  results_config_full_param_sibench_hi_p99_new
  results_config_full_param_silo_hi_p99_new
  results_config_full_param_sphinx_tput_max_new
  results_config_full_param_sysbench_cpu_tput_new
  results_config_full_param_sysbench_oltp_rw_hi_p99_new
  results_config_full_param_tpcc_hi_p99_new
  results_config_full_param_twitter_hi_p99_new
  results_config_full_param_xapian_hi_p99_new
  results_config_full_param_ycsb_hi_p99_new
)
fixed_paths=()
for name in "${fixed_names[@]}"; do
  [[ -d "$RESULTS_ROOT/$name" ]] && fixed_paths+=("$RESULTS_ROOT/$name")
done
[[ -d "$RESULTS_ROOT/results_old/results_config_full_param_dcperf_spark_tput" ]] && \
  fixed_paths+=("$RESULTS_ROOT/results_old/results_config_full_param_dcperf_spark_tput")

prepare_output_dir() {
  local output_dir="${1:-$DEFAULT_OUTPUT_DIR}"
  mkdir -p "$output_dir"
  printf '%s\n' "$output_dir"
}

require_glob_results() {
  if (( ${#retry_paths[@]} == 0 || ${#fixed_paths[@]} == 0 )); then
    echo "Missing retry or fixed result roots below: $RESULTS_ROOT" >&2
    exit 2
  fi
}

validate_plot() {
  local output_dir="$1"
  local plot="$2"
  case "${SEMATUNE_VALIDATION_MODE:-paper}" in
    paper)
      python3 "$SCRIPT_DIR/validate_claim_outputs.py" "$output_dir" --plot "$plot" --profile paper
      ;;
    measured)
      python3 "$SCRIPT_DIR/validate_claim_outputs.py" "$output_dir" --plot "$plot" --profile measured
      ;;
    fresh)
      python3 "$REPO_ROOT/reproduction/validate_fresh_outputs.py" "$output_dir" --plot "$plot"
      ;;
    none)
      ;;
    *)
      echo "Unknown SEMATUNE_VALIDATION_MODE: ${SEMATUNE_VALIDATION_MODE}" >&2
      return 2
      ;;
  esac
}

validate_all_outputs() {
  local output_dir="$1"
  case "${SEMATUNE_VALIDATION_MODE:-paper}" in
    paper)
      python3 "$SCRIPT_DIR/validate_claim_outputs.py" "$output_dir" --profile paper
      ;;
    measured)
      python3 "$SCRIPT_DIR/validate_claim_outputs.py" "$output_dir" --profile measured
      ;;
    fresh)
      python3 "$REPO_ROOT/reproduction/validate_fresh_outputs.py" "$output_dir"
      ;;
    none)
      ;;
    *)
      echo "Unknown SEMATUNE_VALIDATION_MODE: ${SEMATUNE_VALIDATION_MODE}" >&2
      return 2
      ;;
  esac
}
