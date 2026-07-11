#!/usr/bin/env bash
# Paper fig:retry_indirect_aggregate_improvement — direct vs indirect signals.

source "$(dirname "$0")/common.sh"
require_glob_results
output_dir="$(prepare_output_dir "${1:-}")"
recovered_twitter_dir="llm_dual_system_metrics_plain_final_actor_recovered_20260313|llm_dual_system_metrics_plain_final_actor"

python3 "$REPO_ROOT/scripts/plot_retry_aggregate_improvement.py" \
  --result-paths "${retry_paths[@]}" \
  --fallback-fixed-paths "${fixed_paths[@]}" \
  --fallback-tuner-paths "${fixed_paths[@]}" \
  --workloads "$WORKLOADS" \
  --custom-columns "Tuxbot App Only Dual:llm_dual_app_metrics_final_actor,Tuxbot App Only Dual no Catastrophic:llm_dual_app_metrics_final_actor,Tuxbot Indirect Dump Dual:$recovered_twitter_dir,Tuxbot Indirect Dump Dual no Catastrophic:$recovered_twitter_dir,Tuxbot IPC Dual:llm_dual_ipc_final_actor,Tuxbot IPC Dual no Catastrophic:llm_dual_ipc_final_actor,TuxBot Trim App:mlos_trimming_aggressive|mlos_trimming,TuxBot Trim App no Catastrophic:mlos_trimming_aggressive|mlos_trimming,TuxBot Trim IPC:mlos_trimming_aggressive_ipc|mlos_trimming_ipc,TuxBot Trim IPC no Catastrophic:mlos_trimming_aggressive_ipc|mlos_trimming_ipc,TuxBot Trim Cache:mlos_trimming_aggressive_cache_misses_max|mlos_trimming_cache_misses_max,TuxBot Trim Cache no Catastrophic:mlos_trimming_aggressive_cache_misses_max|mlos_trimming_cache_misses_max,MLOS App Metrics:mlos_50_tuning_only|mlos,MLOS App Metrics no Catastrophic:mlos_50_tuning_only|mlos,MLOS IPC:mlos_ipc_50_tuning_only|mlos_ipc,MLOS IPC no Catastrophic:mlos_ipc_50_tuning_only|mlos_ipc,MLOS Cache Misses:mlos_cache_misses_50_tuning_only|mlos_cache_misses,MLOS Cache Misses no Catastrophic:mlos_cache_misses_50_tuning_only|mlos_cache_misses" \
  --aggregate-stat geomean --subset union \
  --tuning-window 1-30 --stable-window 31-50 --error-bars --ymin -75 --ymax 100 \
  --x-label-map 'Tuxbot App Only Dual:App,Tuxbot Indirect Dump Dual:System,Tuxbot IPC Dual:IPC,TuxBot Trim App:App,TuxBot Trim IPC:IPC,TuxBot Trim Cache:Cache,MLOS App Metrics:App,MLOS IPC:IPC,MLOS Cache Misses:Cache' \
  --x-group-map 'Tuxbot App Only Dual:TuxBot,Tuxbot Indirect Dump Dual:TuxBot,Tuxbot IPC Dual:TuxBot,TuxBot Trim App:TuxBot-trim,TuxBot Trim IPC:TuxBot-trim,TuxBot Trim Cache:TuxBot-trim,MLOS App Metrics:MLOS,MLOS IPC:IPC,MLOS Cache Misses:MLOS' \
  --x-group-y-shift-pts 3 \
  --plot-output "$output_dir/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf" \
  --csv-output "$output_dir/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.csv"

validate_plot "$output_dir" 2
