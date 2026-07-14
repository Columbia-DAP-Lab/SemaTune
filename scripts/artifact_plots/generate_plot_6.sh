#!/usr/bin/env bash
# Paper fig:memory_subsection_combined — raw memory runs + regular App-only baseline.

source "$(dirname "$0")/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
sysbench_app_baseline="$RESULTS_ROOT/results_config_full_param_sysbench_oltp_rw_hi_p99_20260305_203050_retry/sysbench_oltp_rw_hi_p99"
sysbench_fixed_baseline="$RESULTS_ROOT/results_config_full_param_sysbench_oltp_rw_hi_p99_new/sysbench_oltp_rw_hi_p99/fixed"

rm -f "$output_dir/PLOT_6_REUSED_REFERENCE.txt" "$output_dir/PLOT_6_BASELINES_REUSED.txt" \
  "$output_dir/PLOT_6_REGULAR_BASELINE.txt"
printf '%s\n' \
  'Top-1/Top-3 use the curated paper_evaluation/results_rag histories; App/No-Memory for Sysbench uses the regular App-only paper result.' \
  > "$output_dir/PLOT_11_REGULAR_BASELINE.txt"

python3 "$REPO_ROOT/scripts/plot_rag_memory_app_system_split.py" \
  --discover-under "$RESULTS_ROOT/results_rag" \
  --output-dir "$output_dir" --write-csv \
  --tuning-window 1-30 --stable-window 31-50 \
  --sysbench-app-no-memory-workload-dir "$sysbench_app_baseline" \
  --sysbench-app-no-memory-fixed-dir "$sysbench_fixed_baseline" \
  --sysbench-oltp-app-vs-indirect-factor 0

validate_plot "$output_dir" 11
