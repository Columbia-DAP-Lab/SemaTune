#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
OUTPUT_DIR="$REPO_ROOT/results/functional_sysbench_paper_suite"
SELECT_METHOD=''
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --method) SELECT_METHOD="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) echo "Usage: $0 [--output-dir DIR] [--method ID] [--force]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${GEMINI_API_KEY:-}" ]] || { echo 'Export GEMINI_API_KEY before running.' >&2; exit 1; }
[[ -f "$SCRIPT_DIR/site.env" ]] || { echo 'Run functional_example/install.sh first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"

"$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" validate-configs
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight --live --real-llm
echo 'WARNING: this suite changes scheduler, busy-poll, P-state, C-state, and IRQ controls.'
echo 'Use only a dedicated/disposable bare-metal host. Every method is separately restored and verified.'

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another Sysbench suite owns $LOCK_FILE" >&2; exit 1; }
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs" "$OUTPUT_DIR/state" "$OUTPUT_DIR/plots"

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=GEMINI_API_KEY,SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

mapfile -t METHODS < <("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(m["id"],m["config"]) for m in d["methods"]]' "$SCRIPT_DIR/sysbench_paper_suite.json")
if [[ -n "$SELECT_METHOD" ]] && ! printf '%s\n' "${METHODS[@]}" | cut -d' ' -f1 | grep -Fxq "$SELECT_METHOD"; then
  echo "Unknown method: $SELECT_METHOD" >&2; exit 2
fi
for row in "${METHODS[@]}"; do
  read -r method config_name <<<"$row"
  "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" materialize --method "$method" \
    --output "$OUTPUT_DIR/configs/$config_name" --results-dir "$OUTPUT_DIR/raw/$method"
done

RESTORE_NEEDED=0
ACTIVE_PGID=''
SNAPSHOT=''; REPORT=''
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

mapfile -t COMPLETE < <("$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" completed-methods --results-dir "$OUTPUT_DIR")
declare -A DONE=(); for method in "${COMPLETE[@]}"; do DONE["$method"]=1; done
echo "RESUME: preserving ${#DONE[@]} completed method(s)"

for row in "${METHODS[@]}"; do
  read -r method config_name <<<"$row"
  [[ -z "$SELECT_METHOD" || "$SELECT_METHOD" == "$method" ]] || continue
  if [[ -n "${DONE[$method]:-}" && ! ( "$FORCE" -eq 1 && "$SELECT_METHOD" == "$method" ) ]]; then
    echo "SKIPPING: $method (complete iterations 1-50)"; continue
  fi
  SNAPSHOT="$OUTPUT_DIR/state/${method}_before.json"
  REPORT="$OUTPUT_DIR/state/${method}_restoration.json"
  "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" capture --output "$SNAPSHOT"
  RESTORE_NEEDED=1
  echo "RUNNING: $method (canonical paper config)"
  PGID_FILE="$OUTPUT_DIR/.${method}.pgid"
  "${ROOT[@]}" setsid --wait sh -c \
    'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
    sh "$PGID_FILE" "${SEMATUNE_PAPER_METHOD_TIMEOUT_SECONDS:-1800}s" "$PYTHON" \
    "$REPO_ROOT/src/barebones_optimizer/main.py" --config "$OUTPUT_DIR/configs/$config_name" \
    >"$OUTPUT_DIR/logs/$method.log" 2>&1 &
  RUN_PID=$!
  for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
  [[ -s "$PGID_FILE" ]] || { echo "Could not establish $method process group" >&2; exit 1; }
  ACTIVE_PGID="$(<"$PGID_FILE")"
  wait "$RUN_PID"
  ACTIVE_PGID=''; rm -f "$PGID_FILE"
  restore_host
  "${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
  if ! "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" completed-methods --results-dir "$OUTPUT_DIR" | grep -Fxq "$method"; then
    echo "$method did not produce a complete history; inspect $OUTPUT_DIR/logs/$method.log" >&2
    exit 1
  fi
  echo "METHOD_COMPLETE: $method"
done

mapfile -t COMPLETE < <("$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" completed-methods --results-dir "$OUTPUT_DIR")
if [[ "${#COMPLETE[@]}" -eq "${#METHODS[@]}" ]]; then
  "$PYTHON" "$SCRIPT_DIR/sysbench_paper_suite.py" plot --results-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR/plots"
  echo "SYSBENCH_PAPER_SUITE: PASS ($OUTPUT_DIR)"
else
  echo "SYSBENCH_PAPER_METHODS: PASS (${#COMPLETE[@]}/${#METHODS[@]} complete; rerun to resume)"
fi
