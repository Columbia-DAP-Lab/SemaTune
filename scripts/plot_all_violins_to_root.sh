#!/bin/bash
# Generate violin plots for all results directories and save them to repo root.
# Discovers: results (if present) and all all_results/results_* directories.
# Output: repo_root/violin_<result_dir>_<bench_name>.png
#
# Usage: ./scripts/plot_all_violins_to_root.sh
# Or pass specific dirs: ./scripts/plot_all_violins_to_root.sh results all_results/results_20260130_192433

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Collect result dirs: optional "results" + any passed args or all_results/results_*
if [ $# -gt 0 ]; then
    RESULT_DIRS=("$@")
else
    RESULT_DIRS=()
    [ -d "results" ] && RESULT_DIRS+=("results")
    for d in all_results/results_*/; do
        [ -d "$d" ] && RESULT_DIRS+=("${d%/}")
    done
    if [ ${#RESULT_DIRS[@]} -eq 0 ]; then
        echo "No results or all_results/results_* directories found."
        exit 0
    fi
fi
echo "Result dir(s): ${RESULT_DIRS[*]}"
echo "Saving violin plots to: $REPO_ROOT"
echo ""

# Human-readable title for benchmark subdir
title_for_bench() {
    local name="$1"
    case "$name" in
        sysbench_cpu_p99)              echo "Sysbench CPU (P99 Latency)" ;;
        sysbench_cpu_tput)             echo "Sysbench CPU (Throughput)" ;;
        sysbench_cpu_p99_wrt_power)    echo "Sysbench CPU (P99 vs Power)" ;;
        sysbench_oltp_rw_hi_p99)       echo "Sysbench OLTP RW (P99 Latency)" ;;
        sysbench_oltp_rw_lo_pwr_wrt_p99) echo "Sysbench OLTP RW (Low Power vs P99)" ;;
        tpcc_hi_p99)                   echo "TPC-C (P99 Latency)" ;;
        tpcc_lo_pwr_wrt_p99)           echo "TPC-C (Low Power vs P99)" ;;
        ycsb_hi_p99)                   echo "YCSB (P99 Latency)" ;;
        ycsb_lo_pwr_wrt_p99)           echo "YCSB (Low Power vs P99)" ;;
        masstree_hi_p99)               echo "Masstree (P99 Latency)" ;;
        masstree_lo_pwr_wrt_p99)       echo "Masstree (Low Power vs P99)" ;;
        silo_hi_p99)                   echo "Silo (P99 Latency)" ;;
        silo_lo_pwr_wrt_p99)           echo "Silo (Low Power vs P99)" ;;
        sphinx_hi_p99)                 echo "Sphinx (P99 Latency)" ;;
        sphinx_lo_pwr_wrt_p99)         echo "Sphinx (Low Power vs P99)" ;;
        sphinx_tput_max)               echo "Sphinx (Throughput)" ;;
        sphinx_lo_pwr_wrt_tput)        echo "Sphinx (Low Power vs Tput)" ;;
        xapian_hi_p99)                 echo "Xapian (P99 Latency)" ;;
        xapian_lo_pwr_wrt_p99)         echo "Xapian (Low Power vs P99)" ;;
        sibench_hi_p99)                echo "Sibench (P99 Latency)" ;;
        sibench_lo_pwr_wrt_p99)        echo "Sibench (Low Power vs P99)" ;;
        *) echo "$name" | tr '_' ' ' ;;
    esac
}

for RESULT_DIR in "${RESULT_DIRS[@]}"; do
    [ -d "$RESULT_DIR" ] || { echo "Skip (not found): $RESULT_DIR"; continue; }
    RESULT_DIR_NAME=$(basename "$RESULT_DIR")
    echo "=== $RESULT_DIR ==="

    for BENCH_DIR in "$RESULT_DIR"/*/; do
        [ -d "$BENCH_DIR" ] || continue
        BENCH_NAME=$(basename "$BENCH_DIR")
        [[ "$BENCH_NAME" == "logs" || "$BENCH_NAME" == "plots" ]] && continue

        if ! compgen -G "$BENCH_DIR/optimization_history_"*.json >/dev/null 2>&1 && \
           ! compgen -G "$BENCH_DIR"/*/optimization_history_*.json >/dev/null 2>&1; then
            echo "  Skip (no history): $BENCH_NAME"
            continue
        fi

        TITLE=$(title_for_bench "$BENCH_NAME")
        OUT_NAME="violin_${RESULT_DIR_NAME}_${BENCH_NAME}.png"
        OUT_PATH="$REPO_ROOT/$OUT_NAME"
        echo "  $BENCH_NAME -> $OUT_NAME"
        python3 scripts/plot_violin.py -d "$BENCH_DIR" \
            -o "$OUT_PATH" -t "$TITLE" 2>/dev/null || true
    done
    echo ""
done
echo "Done. Violin plots saved to $REPO_ROOT"
