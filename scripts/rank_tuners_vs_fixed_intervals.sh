#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Run rank_tuners_vs_fixed.py across start/end intervals.

Usage:
  ./scripts/rank_tuners_vs_fixed_intervals.sh --start <n> --end <n> [--interval <n>] [-o <output_dir>] [--median] [results_dir...]
  ./scripts/rank_tuners_vs_fixed_intervals.sh --start <n> --end <n> [--interval <n>] [-o <output_dir>] [--median] [results_dir...] -- ...extra args for rank_tuners_vs_fixed.py

Examples:
  ./scripts/rank_tuners_vs_fixed_intervals.sh --start 0 --end 100 --interval 10 all_results/results_* -o reports
  ./scripts/rank_tuners_vs_fixed_intervals.sh --start 0 --end 100 --median results_dir1 results_dir2

Output files:
  <output_dir>/rank_tuners_vs_fixed_<start>_<end>.md
  <output_dir>/rank_tuners_vs_fixed_<start>_<end>.json
  <output_dir>/rank_tuners_vs_fixed_<start>_<end>_median.md   (when --median is used)
  <output_dir>/rank_tuners_vs_fixed_<start>_<end>_median.json (when --median is used)
  <output_dir>/rank_tuners_vs_fixed_20_<end>.md               (auto-generated when 20 is within [start, end))
  <output_dir>/rank_tuners_vs_fixed_20_<end>.json             (auto-generated when 20 is within [start, end))
  <output_dir>/rank_tuners_vs_fixed_bump_<start>_<end>.png
  <output_dir>/rank_tuners_vs_fixed_bump_<start>_<end>_median.png (when --median is used)

Behavior:
  - Generates interval reports: [start, start+interval], [start+interval, ...], ...
  - Also generates one full-range report [start, end] if not already produced by interval stepping.
  - Also generates one extra full-range report [20, end] when 20 lies in the requested range.
  - Generates a bump chart over interval rankings, plus a separate final global column.
  - If no results_dir args are provided, auto-discovers all_results/results_* directories.
EOF
}

START_ITER=""
END_ITER=""
INTERVAL=10
OUTPUT_DIR="."
USE_MEDIAN=0
EXTRA_ARGS=()
RESULT_DIRS=()
EXTRA_WINDOW_START=20

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --median)
            USE_MEDIAN=1
            shift
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
        -*)
            echo "Unknown argument: $1"
            usage
            exit 1
            ;;
        *)
            RESULT_DIRS+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$START_ITER" || -z "$END_ITER" ]]; then
    echo "Error: --start and --end are required."
    usage
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

if [[ -z "$OUTPUT_DIR" ]]; then
    echo "Error: output directory cannot be empty."
    exit 1
fi

if (( ${#RESULT_DIRS[@]} == 0 )); then
    for d in all_results/results_*/; do
        [[ -d "$d" ]] && RESULT_DIRS+=("${d%/}")
    done
fi

if (( ${#RESULT_DIRS[@]} == 0 )); then
    echo "Error: no results directories were provided or discovered."
    exit 1
fi

for dir in "${RESULT_DIRS[@]}"; do
    if [[ ! -d "$dir" ]]; then
        echo "Error: results directory not found: $dir"
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RANK_SCRIPT="$REPO_ROOT/scripts/rank_tuners_vs_fixed.py"
BUMP_SCRIPT="$REPO_ROOT/scripts/plot_tuner_bump_chart.py"

if [[ ! -f "$RANK_SCRIPT" ]]; then
    echo "Error: script not found: $RANK_SCRIPT"
    exit 1
fi
if [[ ! -f "$BUMP_SCRIPT" ]]; then
    echo "Error: script not found: $BUMP_SCRIPT"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

MEDIAN_SUFFIX=""
if (( USE_MEDIAN == 1 )); then
    MEDIAN_SUFFIX="_median"
fi

LAST_REPORT_FILE=""
LAST_JSON_FILE=""
INTERVAL_ENTRIES=()
HAS_20_TO_END_INTERVAL=0

run_one_window() {
    local wstart="$1"
    local wend="$2"
    local out_file="$OUTPUT_DIR/rank_tuners_vs_fixed_${wstart}_${wend}${MEDIAN_SUFFIX}.md"
    local json_file="$OUTPUT_DIR/rank_tuners_vs_fixed_${wstart}_${wend}${MEDIAN_SUFFIX}.json"

    echo "Ranking ${wstart} -> ${wend}  =>  ${out_file}"

    local cmd=(
        python3 "$RANK_SCRIPT"
        "${RESULT_DIRS[@]}"
        --start "$wstart"
        --end "$wend"
        -o "$out_file"
        --json-output "$json_file"
    )

    if (( USE_MEDIAN == 1 )); then
        cmd+=(--median)
    fi

    if (( ${#EXTRA_ARGS[@]} > 0 )); then
        cmd+=("${EXTRA_ARGS[@]}")
    fi

    "${cmd[@]}" > /dev/null
    LAST_REPORT_FILE="$out_file"
    LAST_JSON_FILE="$json_file"
}

print_global_section() {
    local report_file="$1"
    if [[ ! -f "$report_file" ]]; then
        echo "Warning: cannot print global section, missing report: $report_file"
        return
    fi

    echo ""
    echo "Global ranking for window ${START_ITER} -> ${END_ITER}:"
    awk '
        /^# Global Tuner Ranking vs Fixed$/ {in_global=1}
        in_global {
            if ($0 ~ /^## /) exit
            print
        }
    ' "$report_file"
}

echo "Results dirs: ${RESULT_DIRS[*]}"
echo "Start: $START_ITER  End: $END_ITER  Interval: $INTERVAL"
echo "Output dir: $OUTPUT_DIR"
if (( USE_MEDIAN == 1 )); then
    echo "Sorting mode: median"
else
    echo "Sorting mode: mean"
fi
echo ""

WINDOW_START=$START_ITER
GENERATED_FULL_RANGE=0
while (( WINDOW_START < END_ITER )); do
    WINDOW_END=$((WINDOW_START + INTERVAL))
    if (( WINDOW_END > END_ITER )); then
        WINDOW_END=$END_ITER
    fi

    run_one_window "$WINDOW_START" "$WINDOW_END"
    INTERVAL_ENTRIES+=("${WINDOW_START}-${WINDOW_END}=${LAST_JSON_FILE}")
    if (( WINDOW_START == EXTRA_WINDOW_START && WINDOW_END == END_ITER )); then
        HAS_20_TO_END_INTERVAL=1
    fi

    if (( WINDOW_START == START_ITER && WINDOW_END == END_ITER )); then
        GENERATED_FULL_RANGE=1
    fi

    WINDOW_START=$((WINDOW_START + INTERVAL))
done

if (( GENERATED_FULL_RANGE == 0 )); then
    run_one_window "$START_ITER" "$END_ITER"
fi

# Also produce one report over [20, end] when 20 is inside the requested range.
if (( START_ITER <= EXTRA_WINDOW_START && EXTRA_WINDOW_START < END_ITER && START_ITER != EXTRA_WINDOW_START && HAS_20_TO_END_INTERVAL == 0 )); then
    run_one_window "$EXTRA_WINDOW_START" "$END_ITER"
fi

FULL_REPORT_FILE="$OUTPUT_DIR/rank_tuners_vs_fixed_${START_ITER}_${END_ITER}${MEDIAN_SUFFIX}.md"
FULL_JSON_FILE="$OUTPUT_DIR/rank_tuners_vs_fixed_${START_ITER}_${END_ITER}${MEDIAN_SUFFIX}.json"
print_global_section "$FULL_REPORT_FILE"

BUMP_OUTPUT="$OUTPUT_DIR/rank_tuners_vs_fixed_bump_${START_ITER}_${END_ITER}${MEDIAN_SUFFIX}.png"
SORT_MODE="mean"
if (( USE_MEDIAN == 1 )); then
    SORT_MODE="median"
fi

PLOT_CMD=(
    python3 "$BUMP_SCRIPT"
    --global-json "$FULL_JSON_FILE"
    --global-label "Global"
    --sort-mode "$SORT_MODE"
    --output "$BUMP_OUTPUT"
)
for entry in "${INTERVAL_ENTRIES[@]}"; do
    PLOT_CMD+=(--interval "$entry")
done
"${PLOT_CMD[@]}" > /dev/null
echo "Bump chart: $BUMP_OUTPUT"

echo ""
echo "Done."
