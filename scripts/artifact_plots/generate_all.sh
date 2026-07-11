#!/usr/bin/env bash

script_dir="$(cd "$(dirname "$0")" && pwd)"
source "$script_dir/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
status=0

for plot in 1 2 3 4 5 6 7; do
  echo "=== Generating plot $plot ==="
  if ! "$script_dir/generate_plot_${plot}.sh" "$output_dir"; then
    echo "Plot $plot could not be generated; see artifact/PAPER_CLAIMS_AND_PLOTS.md." >&2
    status=1
  fi
done

echo "=== Reusing latency table ==="
if ! "$script_dir/reuse_latency_table.sh" "$output_dir"; then
  status=1
fi

validate_all_outputs "$output_dir" || status=1

exit "$status"
