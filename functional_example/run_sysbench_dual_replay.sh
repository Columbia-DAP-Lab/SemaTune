#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"; [[ -x "$PYTHON" ]] || PYTHON=python3
REAL_DIR="$REPO_ROOT/results/functional_sysbench_paper_suite"
OUTPUT_DIR="$REPO_ROOT/results/functional_sysbench_dual_action_replay"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --real-dir) REAL_DIR="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    *) echo "Usage: $0 [--real-dir DIR] [--output-dir DIR]" >&2; exit 2 ;;
  esac
done
[[ -f "$SCRIPT_DIR/site.env" ]] || { echo 'Run scripts/setup.sh --base first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR" OS_PARAM_TUNING_ROOT="$REPO_ROOT"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight --live

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"; exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another Sysbench suite owns $LOCK_FILE" >&2; exit 1; }
REAL_DIR="$(realpath -m "$REAL_DIR")"; OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR"/{raw,configs,replays,logs,state,plots}
ROOT=(); if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

METHODS=(sematune_app sematune_system sematune_ipc)
for method in "${METHODS[@]}"; do
  history="$(find "$REAL_DIR/raw/$method" -maxdepth 1 -name 'dual_loop*.json' -print -quit)"
  [[ -n "$history" ]] || { echo "Missing real source history for $method" >&2; exit 1; }
  "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" build-replay --history "$history" --output "$OUTPUT_DIR/replays/$method.json"
  "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" materialize-replay --method "$method" \
    --replay "$OUTPUT_DIR/replays/$method.json" --output "$OUTPUT_DIR/configs/$method.json" \
    --results-dir "$OUTPUT_DIR/raw/$method"
done

RESTORE_NEEDED=0; ACTIVE_PGID=''; SNAPSHOT=''; REPORT=''
restore_host() {
  local rc=0
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true; sleep 1
    "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" restore --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
    RESTORE_NEEDED=0
  fi
  ACTIVE_PGID=''; "${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
  return "$rc"
}
cleanup() { local rc=$? rr=0; trap - EXIT INT TERM HUP; restore_host || rr=$?; [[ $rc -eq 0 && $rr -ne 0 ]] && rc=$rr; exit "$rc"; }
trap cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM HUP

for method in "${METHODS[@]}"; do
  if "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" completed-methods --results-dir "$OUTPUT_DIR" | grep -Fxq "$method"; then
    echo "SKIPPING: $method replay complete"; continue
  fi
  SNAPSHOT="$OUTPUT_DIR/state/${method}_before.json"; REPORT="$OUTPUT_DIR/state/${method}_restoration.json"
  "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" capture --output "$SNAPSHOT"; RESTORE_NEEDED=1
  echo "RUNNING_REPLAY: $method (recorded actions and response delays; zero provider calls)"
  pgid_file="$OUTPUT_DIR/.${method}.pgid"
  "${ROOT[@]}" setsid --wait sh -c 'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
    sh "$pgid_file" 1800s "$PYTHON" -m optimizer.main --config "$OUTPUT_DIR/configs/$method.json" \
    >"$OUTPUT_DIR/logs/$method.log" 2>&1 & pid=$!
  for _ in {1..40}; do [[ -s "$pgid_file" ]] && break; sleep 0.05; done
  [[ -s "$pgid_file" ]] || { echo "Could not establish $method replay process group" >&2; exit 1; }
  ACTIVE_PGID="$(<"$pgid_file")"; wait "$pid"; ACTIVE_PGID=''; rm -f "$pgid_file"; restore_host
  "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" completed-methods --results-dir "$OUTPUT_DIR" | grep -Fxq "$method" \
    || { echo "$method replay did not complete" >&2; exit 1; }
done

if rg -n 'HTTP Request: POST' "$OUTPUT_DIR/logs"; then echo 'Replay unexpectedly made a provider request.' >&2; exit 1; fi
"$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" plot-real-replay \
  --real-dir "$REAL_DIR" --replay-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR/plots"
echo "SYSBENCH_DUAL_ACTION_REPLAY: PASS ($OUTPUT_DIR)"
