#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DCPERF_DIR="$REPO_ROOT/deps/DCPerf"
DATASET="$DCPERF_DIR/benchmarks/spark_standalone/dataset/bpc_t93586_s2_synthetic"
if [[ -z "${SEMATUNE_SPARKBENCH_DATA_ROOT:-}" && -f "$REPO_ROOT/config/sparkbench.env" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/config/sparkbench.env"
fi
DATA_ROOT="${SEMATUNE_SPARKBENCH_DATA_ROOT:-/mydata/TuxBot-sparkbench}"
OUTPUT_DIR="$REPO_ROOT/results/sparkbench_smoke"
TIMEOUT_SECONDS="${SEMATUNE_SPARKBENCH_SMOKE_TIMEOUT_SECONDS:-3600}"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
JAVA_HOME="${SEMATUNE_SPARKBENCH_JAVA_HOME:-/usr/lib/jvm/java-8-openjdk-amd64}"
[[ -x "$JAVA_HOME/bin/java" ]] || {
  echo "SparkBench requires Java 8 at $JAVA_HOME; rerun scripts/setup_sparkbench.sh." >&2
  exit 1
}
export PYTHONPATH="$REPO_ROOT/src:$REPO_ROOT/functional_example"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    -h|--help) echo 'Usage: scripts/run_sparkbench_smoke.sh [--data-root DIR] [--output-dir DIR]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
DATA_ROOT="$(realpath -m "$DATA_ROOT")"
[[ -x "$DCPERF_DIR/benchmarks/spark_standalone/spark-2.4.5-bin-hadoop2.7/bin/spark-sql" ]] || {
  echo 'SparkBench runtime is absent; run scripts/setup_sparkbench.sh.' >&2; exit 1;
}
[[ -d "$DATASET" ]] || {
  echo "SparkBench dataset is absent; run scripts/setup_sparkbench.sh --with-dataset (default: $DATA_ROOT)." >&2; exit 1;
}
for path in warehouse spark_local_dir; do
  [[ -d "$DATA_ROOT/$path" ]] || {
    echo "SparkBench $path storage is absent under $DATA_ROOT; rerun dataset setup." >&2; exit 1;
  }
done
[[ -f "$DATA_ROOT/warehouse/.sematune-sparkbench-warehouse-complete.json" ]] || {
  echo "SparkBench warehouse is incomplete; rerun scripts/setup_sparkbench.sh --with-dataset." >&2
  exit 1
}
[[ "$(readlink -f "$DCPERF_DIR/benchmarks/spark_standalone/warehouse")" == "$DATA_ROOT/warehouse" ]] || {
  echo "Spark warehouse is not mapped to $DATA_ROOT/warehouse; rerun dataset setup." >&2; exit 1;
}
[[ "$(readlink -f "$DCPERF_DIR/benchmarks/spark_standalone/tmp")" == "$DATA_ROOT/spark_local_dir" ]] || {
  echo "Spark shuffle storage is not mapped to $DATA_ROOT/spark_local_dir; rerun dataset setup." >&2; exit 1;
}
[[ -x "$DCPERF_DIR/benchpress_cli.py" ]] || { echo 'DCPerf benchpress CLI is absent.' >&2; exit 1; }
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR"
"$JAVA_HOME/bin/java" -version >"$OUTPUT_DIR/java_version.txt" 2>&1
LOG="$OUTPUT_DIR/sparkbench.log"
SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
CONFIG="$OUTPUT_DIR/sparkbench_fixed_smoke.json"
RAW_DIR="$OUTPUT_DIR/raw"
mkdir -p "$RAW_DIR"
python3 "$SCRIPT_DIR/validate_sparkbench_smoke.py" materialize \
  --output "$CONFIG" --results-dir "$RAW_DIR" --max-runtime "$TIMEOUT_SECONDS"

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=PYTHONPATH,OS_PARAM_TUNING_ROOT)
fi
LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another TuxBot suite owns $LOCK_FILE" >&2; exit 1; }

RESTORE_NEEDED=0
ACTIVE_PGID=''
restore_host() {
  local rc=0
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    sleep 2
    "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  ACTIVE_PGID=''
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" \
      "$REPO_ROOT/functional_example/host_state_guard.py" restore \
      --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
    RESTORE_NEEDED=0
  fi
  return "$rc"
}
cleanup() {
  local rc=$? restore_rc=0
  trap - EXIT INT TERM HUP
  restore_host || restore_rc=$?
  [[ "$rc" -eq 0 && "$restore_rc" -ne 0 ]] && rc=$restore_rc
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

"${ROOT[@]}" "$PYTHON" \
  "$REPO_ROOT/functional_example/host_state_guard.py" capture --output "$SNAPSHOT"
RESTORE_NEEDED=1
echo 'RUNNING: DCPerf SparkBench local smoke (release_test_93586)'
PGID_FILE="$OUTPUT_DIR/.sparkbench.pgid"
"${ROOT[@]}" env JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" setsid --wait sh -c \
  'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=30s "$@"' \
  sh "$PGID_FILE" "$((TIMEOUT_SECONDS + 120))s" "$PYTHON" \
  -m optimizer.main --config "$CONFIG" \
  >"$LOG" 2>&1 &
RUN_PID=$!
for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
[[ -s "$PGID_FILE" ]] || { echo 'Could not establish SparkBench process group.' >&2; exit 1; }
ACTIVE_PGID="$(<"$PGID_FILE")"
wait "$RUN_PID"
ACTIVE_PGID=''
rm -f "$PGID_FILE"
restore_host
"${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
metrics_dir="$(find "$DCPERF_DIR" -maxdepth 1 -type d -name 'benchmark_metrics_*' \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "$metrics_dir" && -d "$metrics_dir/work" ]] || {
  echo 'DCPerf did not preserve its SparkBench work logs.' >&2
  exit 1
}
cp -a "$metrics_dir/work" "$OUTPUT_DIR/dcperf_work"
python3 "$SCRIPT_DIR/validate_sparkbench_smoke.py" validate \
  --history-dir "$RAW_DIR" --log "$LOG" --restoration "$REPORT" \
  --dcperf-work "$OUTPUT_DIR/dcperf_work" \
  --java-version "$OUTPUT_DIR/java_version.txt" \
  --output "$OUTPUT_DIR/smoke_summary.json"
