#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
WORKLOAD=''
OUTPUT_DIR=''
TIMEOUT_SECONDS="${SEMATUNE_COMPARISON_METHOD_TIMEOUT_SECONDS:-3600}"

usage() {
  echo 'Usage: functional_example/run_fixed_dual_comparison.sh --workload sysbench_cpu_tput|tpcc_hi_p99 [--output-dir DIR]'
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workload) WORKLOAD="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$WORKLOAD" in
  sysbench_cpu_tput|tpcc_hi_p99) ;;
  *) usage >&2; exit 2 ;;
esac
[[ -n "$OUTPUT_DIR" ]] || OUTPUT_DIR="$REPO_ROOT/results/${WORKLOAD}_fixed_dual_real"
[[ -n "${GEMINI_API_KEY:-}" ]] || { echo 'Export GEMINI_API_KEY before running.' >&2; exit 1; }
[[ -f "$SCRIPT_DIR/site.env" ]] || { echo 'Run scripts/setup.sh --base first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"

"$PYTHON" "$SCRIPT_DIR/fixed_dual_comparison.py" validate --workload "$WORKLOAD"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight --live --real-llm
if [[ "$WORKLOAD" == tpcc_hi_p99 ]]; then
  command -v java >/dev/null || { echo 'Java is required; run scripts/setup.sh --base.' >&2; exit 1; }
  [[ -f "$REPO_ROOT/deps/benchbase/target/benchbase-postgres/benchbase.jar" ]] || {
    echo 'BenchBase is not built; run scripts/setup.sh --base.' >&2; exit 1;
  }
fi
PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" psql \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --dbname "$SEMATUNE_SYSBENCH_DB" --username "$SEMATUNE_SYSBENCH_USER" \
  --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --command 'SELECT 1' >/dev/null

echo "WARNING: $WORKLOAD changes scheduler, busy-poll, P-state, C-state, and IRQ controls."
echo 'Use only a dedicated/disposable bare-metal host. Each method is restored and byte-verified.'
LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another SemaTune suite owns $LOCK_FILE" >&2; exit 1; }

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs" "$OUTPUT_DIR/state" "$OUTPUT_DIR/comparison"
for method in fixed sematune_app; do
  "$PYTHON" "$SCRIPT_DIR/fixed_dual_comparison.py" materialize \
    --workload "$WORKLOAD" --method "$method" \
    --output "$OUTPUT_DIR/configs/$method.json" --results-dir "$OUTPUT_DIR/raw/$method"
done

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=GEMINI_API_KEY,SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

RESTORE_NEEDED=0
ACTIVE_PGID=''
SNAPSHOT=''
REPORT=''
restore_host() {
  local rc=0
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    sleep 1
    "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" restore \
      --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
    RESTORE_NEEDED=0
  fi
  ACTIVE_PGID=''
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

for method in fixed sematune_app; do
  if "$PYTHON" "$SCRIPT_DIR/fixed_dual_comparison.py" completed \
      --results-dir "$OUTPUT_DIR" --method "$method" >/dev/null 2>&1; then
    echo "SKIPPING: $method (completed 30+20 history already present)"
    continue
  fi
  SNAPSHOT="$OUTPUT_DIR/state/${method}_before.json"
  REPORT="$OUTPUT_DIR/state/${method}_restoration.json"
  "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" capture --output "$SNAPSHOT"
  RESTORE_NEEDED=1
  echo "RUNNING: $WORKLOAD/$method (30 tuning + 20 stable windows)"
  PGID_FILE="$OUTPUT_DIR/.${method}.pgid"
  "${ROOT[@]}" setsid --wait sh -c \
    'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
    sh "$PGID_FILE" "${TIMEOUT_SECONDS}s" "$PYTHON" \
    -m optimizer.main --config "$OUTPUT_DIR/configs/$method.json" \
    >"$OUTPUT_DIR/logs/$method.log" 2>&1 &
  RUN_PID=$!
  for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
  [[ -s "$PGID_FILE" ]] || { echo "Could not establish $method process group." >&2; exit 1; }
  ACTIVE_PGID="$(<"$PGID_FILE")"
  wait "$RUN_PID"
  ACTIVE_PGID=''
  rm -f "$PGID_FILE"
  restore_host
  "${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
  "$PYTHON" "$SCRIPT_DIR/fixed_dual_comparison.py" completed \
    --results-dir "$OUTPUT_DIR" --method "$method" >/dev/null
  echo "METHOD_COMPLETE: $method"
done

"$PYTHON" "$SCRIPT_DIR/fixed_dual_comparison.py" compare \
  --workload "$WORKLOAD" --results-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR/comparison"
echo "FIXED_DUAL_RUN: PASS ($OUTPUT_DIR)"
