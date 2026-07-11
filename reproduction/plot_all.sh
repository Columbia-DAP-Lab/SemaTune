#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  printf '%s\n' \
    'Usage: reproduction/plot_all.sh --results-dir DIR --output-dir DIR [options]' \
    '' \
    'Options:' \
    '  --plots all|1,2,...       Plots to generate (default: all)' \
    '  --validation MODE         fresh, measured, or paper (default: fresh)' \
    '' \
    'Use measured for the checked-in archived histories and fresh for a one-rerun suite.'
}

RESULTS_DIR=""
OUTPUT_DIR=""
PLOTS="all"
VALIDATION="fresh"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --results-dir) RESULTS_DIR="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --plots) PLOTS="${2:-}"; shift 2 ;;
    --validation) VALIDATION="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$RESULTS_DIR" && -n "$OUTPUT_DIR" ]] || { usage >&2; exit 2; }
case "$VALIDATION" in fresh|measured|paper) ;; *) echo "Invalid validation mode: $VALIDATION" >&2; exit 2;; esac
RESULTS_DIR="$(realpath -m "$RESULTS_DIR")"
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
[[ -d "$RESULTS_DIR" ]] || { echo "Results directory does not exist: $RESULTS_DIR" >&2; exit 2; }
case "$OUTPUT_DIR/" in
  "$RESULTS_DIR/"|"$RESULTS_DIR/raw/"*|"$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing to write plots into results or checked-in evidence: $OUTPUT_DIR" >&2
    exit 2
    ;;
esac
mkdir -p "$OUTPUT_DIR"

if [[ "$PLOTS" == "all" ]]; then
  SELECTED=(1 2 3 4 5 6 7)
else
  IFS=',' read -r -a SELECTED <<< "$PLOTS"
fi
for plot in "${SELECTED[@]}"; do
  [[ "$plot" =~ ^[1-7]$ ]] || { echo "Invalid plot number: $plot" >&2; exit 2; }
done

export SEMATUNE_RESULTS_ROOT="$RESULTS_DIR"
export SEMATUNE_VALIDATION_MODE="$VALIDATION"
for plot in "${SELECTED[@]}"; do
  echo "=== Plot $plot ==="
  "$REPO_ROOT/scripts/artifact_plots/generate_plot_${plot}.sh" "$OUTPUT_DIR"
done

if [[ " ${SELECTED[*]} " == *" 5 "* ]]; then
  "$REPO_ROOT/scripts/artifact_plots/reuse_latency_table.sh" "$OUTPUT_DIR"
fi

printf '%s\n' \
  "PLOT_ALL: PASS" \
  "  results: $RESULTS_DIR" \
  "  plots: ${SELECTED[*]}" \
  "  validation: $VALIDATION" \
  "  output: $OUTPUT_DIR"
