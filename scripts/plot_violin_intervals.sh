#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Generate interval-window violin plots for one results directory.

Usage:
  ./scripts/plot_violin_intervals.sh [--results-dir <results_dir>] --start <n> --end <n> [--interval <n>] [-o <output_dir>] [-- ...extra args for plot_violin.py]

Example:
  ./scripts/plot_violin_intervals.sh --results-dir all_results/results_config_full_param_dcperf_spark_tput_20260222_205653 --start 0 --end 100 --interval 10

This creates:
  <results_dir_basename>_0_10.png
  <results_dir_basename>_10_20.png
  ...
  <results_dir_basename>_90_100.png

Any args after `--` are passed to scripts/plot_violin.py.
If --results-dir is omitted, the current working directory must be a results_* directory.
If -o/--output-dir is omitted, files are written to the current directory.
EOF
}

RESULTS_DIR=""
START_ITER=""
END_ITER=""
INTERVAL=10
OUTPUT_DIR="."
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--results-dir)
            RESULTS_DIR="${2:-}"
            shift 2
            ;;
        --start)
            START_ITER="${2:-}"
            shift 2
            ;;
        --end)
            END_ITER="${2:-}"
            shift 2
            ;;
        --interval)
            INTERVAL="${2:-}"
            shift 2
            ;;
        -o|--output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --)
            shift
            EXTRA_ARGS+=("$@")
            break
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ -z "$START_ITER" || -z "$END_ITER" ]]; then
    echo "Error: --start and --end are required."
    usage
    exit 1
fi

if [[ -z "$RESULTS_DIR" ]]; then
    CWD_BASENAME="$(basename "$PWD")"
    if [[ "$CWD_BASENAME" == results_* ]]; then
        RESULTS_DIR="$PWD"
    else
        echo "Error: --results-dir was not provided and current directory is not results_*."
        usage
        exit 1
    fi
fi

if [[ ! -d "$RESULTS_DIR" ]]; then
    echo "Error: results directory not found: $RESULTS_DIR"
    exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    echo "Error: output directory cannot be empty."
    exit 1
fi

if ! [[ "$START_ITER" =~ ^-?[0-9]+$ && "$END_ITER" =~ ^-?[0-9]+$ && "$INTERVAL" =~ ^[0-9]+$ ]]; then
    echo "Error: --start, --end, and --interval must be integers."
    exit 1
fi

if (( INTERVAL <= 0 )); then
    echo "Error: --interval must be > 0."
    exit 1
fi

if (( START_ITER >= END_ITER )); then
    echo "Error: --start must be less than --end."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
mkdir -p "$OUTPUT_DIR"

mapfile -d '' HISTORY_FILES < <(
    find "$RESULTS_DIR" -type f \( -name 'optimization_history_*.json' -o -name 'dual_loop_*.json' \) -print0 | sort -z
)

if (( ${#HISTORY_FILES[@]} == 0 )); then
    echo "Error: no history files found under $RESULTS_DIR"
    exit 1
fi

RESULT_BASENAME="$(basename "${RESULTS_DIR%/}")"
echo "Results dir: $RESULTS_DIR"
echo "Found ${#HISTORY_FILES[@]} history file(s)"
echo "Start: $START_ITER  End: $END_ITER  Interval: $INTERVAL"
echo "Output dir: $OUTPUT_DIR"
echo ""

WINDOW_START=$START_ITER
GENERATED_FULL_RANGE=0
while (( WINDOW_START < END_ITER )); do
    WINDOW_END=$((WINDOW_START + INTERVAL))
    if (( WINDOW_END > END_ITER )); then
        WINDOW_END=$END_ITER
    fi

    OUTPUT_FILE="${OUTPUT_DIR}/${RESULT_BASENAME}_${WINDOW_START}_${WINDOW_END}.png"
    echo "Plotting ${WINDOW_START} -> ${WINDOW_END}  =>  ${OUTPUT_FILE}"

    python3 "$REPO_ROOT/scripts/plot_violin.py" \
        -f "${HISTORY_FILES[@]}" \
        --start "$WINDOW_START" \
        --end "$WINDOW_END" \
        -o "$OUTPUT_FILE" \
        "${EXTRA_ARGS[@]}"

    if (( WINDOW_START == START_ITER && WINDOW_END == END_ITER )); then
        GENERATED_FULL_RANGE=1
    fi

    WINDOW_START=$((WINDOW_START + INTERVAL))
done

# Also produce one combined plot over the full requested window.
if (( GENERATED_FULL_RANGE == 0 )); then
    FULL_OUTPUT_FILE="${OUTPUT_DIR}/${RESULT_BASENAME}_${START_ITER}_${END_ITER}.png"
    echo "Plotting ${START_ITER} -> ${END_ITER}  =>  ${FULL_OUTPUT_FILE}"

    python3 "$REPO_ROOT/scripts/plot_violin.py" \
        -f "${HISTORY_FILES[@]}" \
        --start "$START_ITER" \
        --end "$END_ITER" \
        -o "$FULL_OUTPUT_FILE" \
        "${EXTRA_ARGS[@]}"
fi

echo ""
echo "Done."
