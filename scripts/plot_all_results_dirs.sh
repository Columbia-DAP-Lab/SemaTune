#!/bin/bash
# Find all all_results/results_* directories, then for each generate violin + parameter (comparison)
# plots with titles based on the experiment run (benchmark name).
# Run from repo root.
#
# Usage: ./scripts/plot_all_results_dirs.sh
# Or pass specific dirs: ./scripts/plot_all_results_dirs.sh all_results/results_20260130_192433 ...

set -e
cd "$(dirname "$0")/.."

# Discover results_* dirs if none passed
if [ $# -eq 0 ]; then
    RESULT_DIRS=()
    for d in all_results/results_*/; do
        [ -d "$d" ] && RESULT_DIRS+=("${d%/}")
    done
    if [ ${#RESULT_DIRS[@]} -eq 0 ]; then
        echo "No all_results/results_* directories found."
        exit 0
    fi
    echo "Found ${#RESULT_DIRS[@]} result dir(s): ${RESULT_DIRS[*]}"
else
    RESULT_DIRS=("$@")
fi

# Human-readable title for benchmark subdir (experiment name)
title_for_bench() {
    local name="$1"
    case "$name" in
        sysbench_cpu_p99)              echo "Sysbench CPU (P99 Latency)" ;;
        sysbench_cpu_tput)              echo "Sysbench CPU (Throughput)" ;;
        sysbench_oltp_rw_hi_p99)        echo "Sysbench OLTP RW (P99 Latency)" ;;
        sysbench_oltp_rw_lo_pwr_wrt_p99) echo "Sysbench OLTP RW (Low Power vs P99)" ;;
        tpcc_hi_p99)                    echo "TPC-C (P99 Latency)" ;;
        tpcc_lo_pwr_wrt_p99)            echo "TPC-C (Low Power vs P99)" ;;
        ycsb_hi_p99)                    echo "YCSB (P99 Latency)" ;;
        ycsb_lo_pwr_wrt_p99)            echo "YCSB (Low Power vs P99)" ;;
        masstree_hi_p99)                echo "Masstree (P99 Latency)" ;;
        masstree_lo_pwr_wrt_p99)        echo "Masstree (Low Power vs P99)" ;;
        silo_hi_p99)                    echo "Silo (P99 Latency)" ;;
        silo_lo_pwr_wrt_p99)            echo "Silo (Low Power vs P99)" ;;
        sphinx_hi_p99)                  echo "Sphinx (P99 Latency)" ;;
        sphinx_lo_pwr_wrt_p99)          echo "Sphinx (Low Power vs P99)" ;;
        sphinx_tput_max)                echo "Sphinx (Throughput)" ;;
        xapian_hi_p99)                  echo "Xapian (P99 Latency)" ;;
        xapian_lo_pwr_wrt_p99)          echo "Xapian (Low Power vs P99)" ;;
        *) echo "$name" | tr '_' ' ' ;;
    esac
}

for RESULT_DIR in "${RESULT_DIRS[@]}"; do
    [ -d "$RESULT_DIR" ] || { echo "Skip (not found): $RESULT_DIR"; continue; }
    PLOTS_DIR="$RESULT_DIR/plots"
    mkdir -p "$PLOTS_DIR"
    echo ""
    echo "=== $RESULT_DIR ==="

    for BENCH_DIR in "$RESULT_DIR"/*/; do
        [ -d "$BENCH_DIR" ] || continue
        BENCH_NAME=$(basename "$BENCH_DIR")
        [[ "$BENCH_NAME" == "logs" || "$BENCH_NAME" == "plots" ]] && continue

        # Plotters accept -d and search dir + one level down for optimization_history_*.json
        if ! compgen -G "$BENCH_DIR/optimization_history_"*.json >/dev/null 2>&1 && \
           ! compgen -G "$BENCH_DIR"/*/optimization_history_*.json >/dev/null 2>&1; then
            echo "  Skip (no history): $BENCH_NAME"
            continue
        fi

        TITLE=$(title_for_bench "$BENCH_NAME")
        echo "  Plotting: $BENCH_NAME -> \"$TITLE\""

        # Comparison: all parameters in one figure + optional violations plot
        python3 scripts/plot_comparison.py -d "$BENCH_DIR" \
            -o "$PLOTS_DIR/${BENCH_NAME}_comparison.png" -t "$TITLE" \
            --all-params --violations-output "$PLOTS_DIR/${BENCH_NAME}_violations.png" 2>/dev/null || true
        python3 scripts/plot_violin.py -d "$BENCH_DIR" \
            -o "$PLOTS_DIR/${BENCH_NAME}_violin.png" -t "$TITLE" 2>/dev/null || true
    done
    echo "  Plots -> $PLOTS_DIR"
done
echo ""
echo "Done."
