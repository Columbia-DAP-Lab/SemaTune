#!/usr/bin/env bash
# Paper tab:latency_by_params — reuse submitted samples by maintainer direction.

source "$(dirname "$0")/common.sh"
output_dir="$(prepare_output_dir "${1:-}")"
reference="$REFERENCE_ROOT/latency_by_params.csv"

if [[ ! -f "$reference" ]]; then
  echo "Missing submitted latency CSV: $reference" >&2
  exit 2
fi

cp -p "$reference" "$output_dir/latency_by_params.csv"
python3 "$SCRIPT_DIR/validate_latency_table.py" "$output_dir/latency_by_params.csv"
