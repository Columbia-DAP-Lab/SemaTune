#!/bin/bash
# Regenerate violin and parameter (comparison) plots for all batch results from the
# sysbench + benchbase run, with correct titles. Run from repo root.
#
# Usage: ./scripts/plot_all_batch_results.sh
# Or pass result directory names: ./scripts/plot_all_batch_results.sh results_20260130_192433 results_20260130_202846 ...

set -e
cd "$(dirname "$0")/.."

# Default: all result dirs from the terminal run (sysbench + benchbase batches)
if [ $# -eq 0 ]; then
    RESULT_DIRS=(
        results_20260130_192433
        results_20260130_202846
        results_20260130_213253
        results_20260130_223901
        results_20260130_234707
        results_20260131_005752
        results_20260131_015943
        results_20260131_032137
    )
else
    RESULT_DIRS=("$@")
fi

# Benchmark subdir -> human-readable title
declare -A TITLES=(
    [sysbench_cpu_p99]="Sysbench CPU (P99 Latency)"
    [sysbench_cpu_tput]="Sysbench CPU (Throughput)"
    [sysbench_oltp_rw_hi_p99]="Sysbench OLTP RW (P99 Latency)"
    [sysbench_oltp_rw_lo_pwr_wrt_p99]="Sysbench OLTP RW (Low Power vs P99)"
    [tpcc_hi_p99]="TPC-C (P99 Latency)"
    [tpcc_lo_pwr_wrt_p99]="TPC-C (Low Power vs P99)"
    [ycsb_hi_p99]="YCSB (P99 Latency)"
    [ycsb_lo_pwr_wrt_p99]="YCSB (Low Power vs P99)"
)

for RESULT_DIR in "${RESULT_DIRS[@]}"; do
    [ -d "$RESULT_DIR" ] || { echo "Skip (not found): $RESULT_DIR"; continue; }
    PLOTS_DIR="$RESULT_DIR/plots"
    mkdir -p "$PLOTS_DIR"

    for BENCH_DIR in "$RESULT_DIR"/*/; do
        [ -d "$BENCH_DIR" ] || continue
        BENCH_NAME=$(basename "$BENCH_DIR")
        [[ "$BENCH_NAME" == "logs" || "$BENCH_NAME" == "plots" ]] && continue

        HISTORIES=("$BENCH_DIR"optimization_history_*.json)
        [ -f "${HISTORIES[0]}" ] || { echo "  No history files in $BENCH_DIR"; continue; }

        TITLE="${TITLES[$BENCH_NAME]:-$BENCH_NAME}"
        echo "  Plotting: $BENCH_NAME -> \"$TITLE\""

        python3 scripts/plot_comparison.py -f "${HISTORIES[@]}" -p min_granularity_ns \
            -o "$PLOTS_DIR/${BENCH_NAME}_comparison.png" -t "$TITLE" 2>/dev/null || true
        python3 scripts/plot_violin.py -f "${HISTORIES[@]}" \
            -o "$PLOTS_DIR/${BENCH_NAME}_violin.png" -t "$TITLE" 2>/dev/null || true
    done
    echo "  Plots -> $PLOTS_DIR"
done
echo "Done."
