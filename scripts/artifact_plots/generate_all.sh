#!/usr/bin/env bash

script_dir="$(cd "$(dirname "$0")" && pwd)"
source "$script_dir/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
status=0

for paper_plot in 6 7 8 9 10 11 12; do
  legacy_plot=$((paper_plot - 5))
  echo "=== Generating paper Plot $paper_plot ==="
  if ! "$script_dir/generate_plot_${legacy_plot}.sh" "$output_dir"; then
    echo "Paper Plot $paper_plot could not be generated; see artifact/PAPER_CLAIMS_AND_PLOTS.md." >&2
    status=1
  fi
done

echo "=== Reusing latency table ==="
if ! "$script_dir/reuse_latency_table.sh" "$output_dir"; then
  status=1
fi

validate_all_outputs "$output_dir" || status=1

exit "$status"
