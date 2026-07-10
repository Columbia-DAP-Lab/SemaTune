#!/usr/bin/env bash
# Generate all plots for results/ycsb_hi_p99 per EXPERIMENTS_QUICKSTART.md
# Run from repo root: ./scripts/plot_ycsb_hi_p99_results.sh [results_dir] [output_dir]
# Example: ./scripts/plot_ycsb_hi_p99_results.sh results plots/ycsb_hi_p99

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
RESULTS="${1:-results}"
OUT_DIR="${2:-plots/ycsb_hi_p99}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

echo "Results dir: $RESULTS"
echo "Output dir:  $OUT_DIR"

# 1) Cross-benchmark relative improvement plots (discovers ycsb_hi_p99 from results/)
echo ""
echo "=== 1. Relative improvement plots (vs Fixed) ==="
(cd "$OUT_DIR" && python3 "$REPO_ROOT/plot_all_benchmark_results_relative.py" "$REPO_ROOT/$RESULTS")
# Writes: relative_objective_improvement.png, relative_improvement_throughput.png,
#         relative_improvement_p99_latency.png, relative_improvement_power.png,
#         relative_improvement_avg_latency.png

# 2) Parameter trajectory comparison (min_granularity_ns for ycsb_hi_p99)
echo ""
echo "=== 2. Parameter trajectory (min_granularity_ns) ==="
python3 scripts/plot_comparison.py \
  -d "$RESULTS/ycsb_hi_p99/fixed" \
  -d "$RESULTS/ycsb_hi_p99/bayesian" \
  -p min_granularity_ns \
  -o "$OUT_DIR/ycsb_hi_p99_min_granularity_comparison.png"

# 3) Violin plots: throughput and P99 latency
echo ""
echo "=== 3. Violin plot - Throughput ==="
python3 scripts/plot_violin.py \
  -f "$RESULTS/ycsb_hi_p99/"*/optimization_history_ycsb_*.json \
  -m throughput \
  -o "$OUT_DIR/ycsb_hi_p99_violin_throughput.png"

echo ""
echo "=== 4. Violin plot - P99 Latency ==="
python3 scripts/plot_violin.py \
  -f "$RESULTS/ycsb_hi_p99/"*/optimization_history_ycsb_*.json \
  -m p_99_latency \
  -o "$OUT_DIR/ycsb_hi_p99_violin_p99.png"

echo ""
echo "=== All YCSB hi_p99 plots generated in $OUT_DIR ==="
