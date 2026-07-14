#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
OUTPUT_DIR="$REPO_ROOT/results/benchbase_fixed_smoke"
TIMEOUT_SECONDS="${SEMATUNE_BENCHBASE_SMOKE_TIMEOUT_SECONDS:-900}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) echo 'Usage: functional_example/run_benchbase_smoke.sh [--output-dir DIR]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$SCRIPT_DIR/site.env" ]] || { echo 'Run scripts/setup.sh --base first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"

"$PYTHON" "$SCRIPT_DIR/benchbase_smoke.py" validate-sources
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight --live
command -v java >/dev/null || { echo 'Java is required; run scripts/setup.sh --base.' >&2; exit 1; }

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another TuxBot suite owns $LOCK_FILE" >&2; exit 1; }
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs" "$OUTPUT_DIR/state"
for workload in wikipedia twitter ycsb; do
  "$PYTHON" "$SCRIPT_DIR/benchbase_smoke.py" materialize \
    --workload "$workload" --output "$OUTPUT_DIR/configs/$workload.json" \
    --results-dir "$OUTPUT_DIR/raw/$workload"
done

ROOT=()
POSTGRES=(runuser -u postgres --)
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=SEMATUNE_BENCHBASE_DB,SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
  POSTGRES=(sudo -n -u postgres)
fi

RESTORE_NEEDED=0
DROP_NEEDED=0
ACTIVE_PGID=''
SNAPSHOT=''
REPORT=''
SMOKE_DB=''
restore_and_drop() {
  local rc=0
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    sleep 1
    "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  ACTIVE_PGID=''
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" restore \
      --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
    RESTORE_NEEDED=0
  fi
  if [[ "$DROP_NEEDED" -eq 1 ]]; then
    "${POSTGRES[@]}" dropdb --if-exists --force "$SMOKE_DB" || rc=$?
    DROP_NEEDED=0
  fi
  return "$rc"
}
cleanup() {
  local rc=$? cleanup_rc=0
  trap - EXIT INT TERM HUP
  restore_and_drop || cleanup_rc=$?
  [[ "$rc" -eq 0 && "$cleanup_rc" -ne 0 ]] && rc=$cleanup_rc
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

failures=()
for workload in wikipedia twitter ycsb; do
  if "$PYTHON" "$SCRIPT_DIR/benchbase_smoke.py" check \
      --workload "$workload" --output-dir "$OUTPUT_DIR" >/dev/null 2>&1; then
    echo "SKIPPING: $workload (validated one-window result already present)"
    continue
  fi
  SMOKE_DB="sematune_smoke_${workload}"
  "${POSTGRES[@]}" dropdb --if-exists --force "$SMOKE_DB"
  "${POSTGRES[@]}" createdb --owner="$SEMATUNE_SYSBENCH_USER" "$SMOKE_DB"
  DROP_NEEDED=1
  export SEMATUNE_BENCHBASE_DB="$SMOKE_DB"
  SNAPSHOT="$OUTPUT_DIR/state/${workload}_before.json"
  REPORT="$OUTPUT_DIR/state/${workload}_restoration.json"
  "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" capture --output "$SNAPSHOT"
  RESTORE_NEEDED=1
  echo "RUNNING: $workload Fixed smoke (one 5-second measured window)"
  PGID_FILE="$OUTPUT_DIR/.${workload}.pgid"
  "${ROOT[@]}" setsid --wait sh -c \
    'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
    sh "$PGID_FILE" "${TIMEOUT_SECONDS}s" "$PYTHON" \
    -m optimizer.main --config "$OUTPUT_DIR/configs/$workload.json" \
    >"$OUTPUT_DIR/logs/$workload.log" 2>&1 &
  RUN_PID=$!
  for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
  if [[ ! -s "$PGID_FILE" ]]; then
    echo "Could not establish $workload process group." >&2
    failures+=("$workload")
    restore_and_drop || true
    continue
  fi
  ACTIVE_PGID="$(<"$PGID_FILE")"
  run_rc=0
  wait "$RUN_PID" || run_rc=$?
  ACTIVE_PGID=''
  rm -f "$PGID_FILE"
  restore_rc=0
  restore_and_drop || restore_rc=$?
  "${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
  if [[ "$run_rc" -ne 0 || "$restore_rc" -ne 0 ]] || ! \
      "$PYTHON" "$SCRIPT_DIR/benchbase_smoke.py" check \
        --workload "$workload" --output-dir "$OUTPUT_DIR"; then
    echo "FAILED: $workload (inspect $OUTPUT_DIR/logs/$workload.log)" >&2
    failures+=("$workload")
    continue
  fi
  echo "WORKLOAD_COMPLETE: $workload"
done

if [[ "${#failures[@]}" -ne 0 ]]; then
  echo "BENCHBASE_SMOKE: FAIL (${failures[*]})" >&2
  exit 1
fi
"$PYTHON" "$SCRIPT_DIR/benchbase_smoke.py" summarize --output-dir "$OUTPUT_DIR"
