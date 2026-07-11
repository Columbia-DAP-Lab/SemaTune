#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
SOURCE="$REPO_ROOT/reproduction/configs/common/sysbench_oltp_rw_hi_p99/sematune_app.json"
OUTPUT_DIR="$REPO_ROOT/results/functional_sysbench_original_real"
TIMEOUT_SECONDS="${SEMATUNE_ORIGINAL_SYSBENCH_TIMEOUT_SECONDS:-1800}"

if [[ "${1:-}" == "--output-dir" && -n "${2:-}" ]]; then
  OUTPUT_DIR="$2"
  shift 2
fi
[[ $# -eq 0 ]] || { echo "Usage: $0 [--output-dir DIR]" >&2; exit 2; }
[[ -n "${GEMINI_API_KEY:-}" ]] || { echo 'Export GEMINI_API_KEY before running.' >&2; exit 1; }
[[ -f "$SCRIPT_DIR/site.env" ]] || { echo 'Run functional_example/install.sh first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"

echo 'WARNING: the canonical Sysbench run changes scheduler, busy-poll, P-state, and C-state controls.'
echo 'Use only a dedicated/disposable bare-metal machine. Captured controls are restored and byte-verified.'
"$PYTHON" "$SCRIPT_DIR/verify_sysbench_original.py" --config "$SOURCE"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight --live --real-llm

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another Sysbench suite owns $LOCK_FILE" >&2; exit 1; }

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
if [[ -e "$OUTPUT_DIR" && -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "Output directory must be absent or empty: $OUTPUT_DIR" >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/logs" "$OUTPUT_DIR/configs"
CONFIG="$OUTPUT_DIR/configs/sysbench_sematune_dual_original.json"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" materialize \
  --source "$SOURCE" --output "$CONFIG" --results-dir "$OUTPUT_DIR/raw"
"$PYTHON" "$SCRIPT_DIR/verify_sysbench_original.py" --config "$CONFIG" \
  --report "$OUTPUT_DIR/config_validation.json"

PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" psql \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --dbname "$SEMATUNE_SYSBENCH_DB" --username "$SEMATUNE_SYSBENCH_USER" \
  --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --command 'SELECT 1' >/dev/null

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=GEMINI_API_KEY,SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
RESTORE_NEEDED=0
ACTIVE_PGID=''
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

"${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" capture --output "$SNAPSHOT"
RESTORE_NEEDED=1
echo 'RUNNING: canonical Sysbench OLTP-RW dual-loop (30 tuning + 20 stable; real Gemini)'
PGID_FILE="$OUTPUT_DIR/.run.pgid"
"${ROOT[@]}" setsid --wait sh -c \
  'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
  sh "$PGID_FILE" "${TIMEOUT_SECONDS}s" "$PYTHON" \
  "$REPO_ROOT/src/barebones_optimizer/main.py" --config "$CONFIG" \
  >"$OUTPUT_DIR/logs/sematune_dual.log" 2>&1 &
RUN_PID=$!
for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
[[ -s "$PGID_FILE" ]] || { echo 'Could not establish benchmark process group.' >&2; exit 1; }
ACTIVE_PGID="$(<"$PGID_FILE")"
wait "$RUN_PID"
ACTIVE_PGID=''
rm -f "$PGID_FILE"
restore_host
"${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true

mapfile -t HISTORIES < <(find "$OUTPUT_DIR/raw" -maxdepth 1 -type f \
  -name 'dual_loop_actor_speculator_sysbench_oltp_*.json' -print | sort)
[[ "${#HISTORIES[@]}" -eq 1 ]] || { echo "Expected one history, found ${#HISTORIES[@]}" >&2; exit 1; }
"$PYTHON" "$SCRIPT_DIR/verify_sysbench_original.py" --config "$CONFIG" \
  --history "${HISTORIES[0]}" --report "$OUTPUT_DIR/llm_application_validation.json"
echo "SYSBENCH_ORIGINAL_REAL_RUN: PASS ($OUTPUT_DIR)"
