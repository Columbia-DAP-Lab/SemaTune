#!/usr/bin/env bash
# Paper fig:retry_aggregate_improvement — end-to-end performance and safety.

source "$(dirname "$0")/common.sh"
require_glob_results
output_dir="$(prepare_output_dir "${1:-}")"

python3 "$REPO_ROOT/scripts/plot_retry_aggregate_improvement.py" \
  --result-paths "${retry_paths[@]}" \
  --fallback-fixed-paths "${fixed_paths[@]}" \
  --fallback-tuner-paths "${fixed_paths[@]}" \
  --workloads "$WORKLOADS" \
  --custom-columns 'Tuxbot App Metrics Dual Loop:llm_dual_app_metrics_final_actor,Tuxbot App Metrics Dual Loop no Catastrophic:llm_dual_app_metrics_final_actor,MLOS + Tuxbot:mlos_trimming_aggressive|mlos_trimming,MLOS + Tuxbot no Catastrophic:mlos_trimming_aggressive|mlos_trimming,MLOS:mlos_50_tuning_only|mlos,MLOS no Catastrophic:mlos_50_tuning_only|mlos,Bayesian:bayesian,Bayesian no Catastrophic:bayesian,DQN:dqn,DQN no Catastrophic:dqn,Q-Learning:qlearning,Q-Learning no Catastrophic:qlearning' \
  --aggregate-stat geomean --subset union \
  --tuning-window 1-30 --stable-window 31-50 --error-bars --right-trim-pts 0 \
  --plot-output "$output_dir/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf" \
  --csv-output "$output_dir/retry_aggregate_improvement_geomean_with_and_without_xapian.csv"

validate_plot "$output_dir" 1
