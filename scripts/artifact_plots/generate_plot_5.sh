#!/usr/bin/env bash
# Paper fig:param_ablation — performance as the knob space grows.

source "$(dirname "$0")/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
param_roots=(
  "$RESULTS_ROOT/results_params/silo_hi_p99_final"
  "$RESULTS_ROOT/results_params/tpcc_p99_final"
  "$RESULTS_ROOT/results_params/sysbench_oltp_rw_final"
)

python3 "$REPO_ROOT/scripts/plot_ablation_param_aggregate.py" \
  --results-paths "${param_roots[@]}" \
  --fallback-fixed-paths \
    "$RESULTS_ROOT/results_config_full_param_silo_hi_p99_new" \
    "$RESULTS_ROOT/results_config_full_param_tpcc_hi_p99_new" \
    "$RESULTS_ROOT/results_config_full_param_sysbench_oltp_rw_hi_p99_new" \
  --fallback-full-results-paths \
    "$RESULTS_ROOT/results_config_full_param_silo_hi_p99_20260305_234901_retry" \
    "$RESULTS_ROOT/results_config_full_param_tpcc_hi_p99_20260306_080130_retry" \
    "$RESULTS_ROOT/results_config_full_param_sysbench_oltp_rw_hi_p99_20260305_203050_retry" \
  --llm-trimming-dir-name 'llm_trimming|mlos_trimming_aggressive|mlos_trimming' \
  --aggregate-stat geomean --unit pct \
  --plot-output "$output_dir/ablation_param_geomean.pdf" \
  --csv-output "$output_dir/ablation_param_geomean.csv" \
  --per-workload-csv-output "$output_dir/ablation_param_geomean_per_workload.csv"

validate_plot "$output_dir" 5
