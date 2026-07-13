#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
if [[ "$PYTHON" != python3 ]]; then
  export PATH="$(dirname "$PYTHON"):$PATH"
fi

RESULTS_DIR=''
OUTPUT_DIR=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --results-dir) RESULTS_DIR="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) echo 'Usage: functional_example/plot.sh --results-dir DIR --output-dir DIR'; exit 0 ;;
    *) echo "Unknown plot option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$RESULTS_DIR" && -n "$OUTPUT_DIR" ]] || { echo '--results-dir and --output-dir are required.' >&2; exit 2; }
RESULTS_DIR="$(realpath -m "$RESULTS_DIR")"
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/paper_evaluation_plots/"*|"$REPO_ROOT/all_results/paper_evaluation/"*)
    echo "Refusing to overwrite checked-in evidence: $OUTPUT_DIR" >&2; exit 2 ;;
esac

mkdir -p "$OUTPUT_DIR"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" summarize --results-dir "$RESULTS_DIR"
"$PYTHON" "$SCRIPT_DIR/plot_tpcc_suite.py" \
  --summary "$RESULTS_DIR/sysbench_summary.json" --output-dir "$OUTPUT_DIR"
"$PYTHON" "$SCRIPT_DIR/plot_paper_style_equivalents.py" \
  --results-dir "$RESULTS_DIR" --output-dir "$OUTPUT_DIR"

archive_work="$OUTPUT_DIR/.archived_headline_work"
rm -rf "$archive_work"
SEMATUNE_RESULTS_ROOT="$REPO_ROOT/all_results/paper_evaluation" \
SEMATUNE_VALIDATION_MODE=measured \
  "$REPO_ROOT/scripts/artifact_plots/generate_plot_1.sh" "$archive_work" \
  >"$OUTPUT_DIR/archived_headline.log" 2>&1
cp "$archive_work/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf" "$OUTPUT_DIR/archived_headline.pdf"
cp "$archive_work/retry_aggregate_improvement_geomean_with_and_without_xapian.csv" "$OUTPUT_DIR/archived_headline.csv"
rm -rf "$archive_work"

echo "FUNCTIONAL_SYSBENCH_PLOTS: PASS ($OUTPUT_DIR)"
