#!/usr/bin/env bash
# Paper fig:retry_robustness — online exploration safety.

source "$(dirname "$0")/common.sh"
require_glob_results
output_dir="$(prepare_output_dir "${1:-}")"

python3 "$REPO_ROOT/scripts/plot_retry_robustness_aggregate_single.py" \
  --result-paths "${retry_paths[@]}" \
  --fallback-fixed-paths "${fixed_paths[@]}" \
  --experiments masstree_hi_p99 sibench_hi_p99 silo_hi_p99 sphinx_tput_max sysbench_cpu_tput sysbench_oltp_rw_hi_p99 tpcc_hi_p99 twitter_p99 wikipedia_p99 xapian_hi_p99 ycsb_hi_p99 dcperf_spark_tput \
  --tuning-window 1-30 --ytick-step 20 --ymax 120 \
  --plot-output "$output_dir/retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf" \
  --csv-output "$output_dir/retry_robustness_memory_tuxbot_mlos_1_30_aggregate.csv"

validate_plot "$output_dir" 4
