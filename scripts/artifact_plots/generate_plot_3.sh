#!/usr/bin/env bash
# Paper fig:dual_vs_single — quality/cost comparison.

source "$(dirname "$0")/common.sh"
require_glob_results
output_dir="$(prepare_output_dir "${1:-}")"
cost_summary_csv="${SEMATUNE_COST_SUMMARY_CSV:-$REPO_ROOT/artifact/reference_data/dual_vs_single_costs.csv}"
cost_args=(--cost-summary-csv "$cost_summary_csv")

python3 "$REPO_ROOT/scripts/plot_dual_vs_single_cost.py" \
  --result-paths "${retry_paths[@]}" \
  --fallback-fixed-paths "${fixed_paths[@]}" \
  --workloads "$WORKLOADS" \
  --custom-columns 'TuxBot:llm_dual_app_metrics_final_actor,TuxBot no Catastrophic:llm_dual_app_metrics_final_actor,MLOS + TuxBot:mlos_trimming_aggressive|mlos_trimming,MLOS + TuxBot no Catastrophic:mlos_trimming_aggressive|mlos_trimming,Single-Reasoning:llm_reasoning_app_metrics_final_actor,Single-Reasoning no Catastrophic:llm_reasoning_app_metrics_final_actor,Single-Instant:llm_app_metrics_final_actor,Single-Instant no Catastrophic:llm_app_metrics_final_actor,MLOS:mlos_50_tuning_only|mlos,MLOS no Catastrophic:mlos_50_tuning_only|mlos' \
  --aggregate-stat geomean --subset union \
  --tuning-window 1-30 --stable-window 31-50 --error-bars \
  "${cost_args[@]}" \
  --plot-output "$output_dir/dual_vs_single_cost_geomean_error_bars.pdf" \
  --csv-output "$output_dir/dual_vs_single_cost_geomean_error_bars.csv"

validate_plot "$output_dir" 8
