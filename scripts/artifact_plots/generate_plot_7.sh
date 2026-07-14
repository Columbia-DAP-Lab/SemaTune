#!/usr/bin/env bash
# Paper fig:mlos_motivation_examples — proxy and dimensionality motivation.

source "$(dirname "$0")/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
wikipedia_root="${SEMATUNE_WIKIPEDIA_MOTIVATION_ROOT:-$RESULTS_ROOT/results_config_full_param_wikipedia_p99_retry/wikipedia_p99}"
tpcc_param_root="${SEMATUNE_TPCC_PARAM_RESULTS_ROOT:-$RESULTS_ROOT/results_params/tpcc_p99_final}"

python3 "$REPO_ROOT/scripts/plot_mlos_motivation_examples_combined.py" \
  --wikipedia-results-root "$wikipedia_root" \
  --tpcc-default-dir "$RESULTS_ROOT/results_config_full_param_tpcc_hi_p99_new/tpcc_hi_p99/fixed" \
  --tpcc-param-results-root "$tpcc_param_root" \
  --tpcc-eight-param-dir "$RESULTS_ROOT/results_config_full_param_tpcc_hi_p99_20260306_080130_retry/tpcc_hi_p99/mlos_50_tuning_only" \
  --output-pdf "$output_dir/mlos_motivation_examples_combined.pdf" \
  --output-csv "$output_dir/mlos_motivation_examples_combined.csv"

validate_plot "$output_dir" 12
