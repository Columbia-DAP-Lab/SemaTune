#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

usage() {
  printf '%s\n' \
    'Usage:' \
    '  functional_example/run.sh --dry-run' \
    '  functional_example/run.sh --quick --trace-replay [--output-dir DIR]' \
    '  functional_example/run.sh --quick --real-llm [--output-dir DIR]' \
    '  functional_example/run.sh --quick --trace-replay --resume [--output-dir DIR]' \
    '  functional_example/run.sh --quick --trace-replay --resume --method ID [--output-dir DIR]' \
    '' \
    'The live suite runs 14 selected tuner/signal variants on Sysbench OLTP-RW' \
    '(5 tuning + 5 stable windows, eight OS parameters).'
}

DRY_RUN=0
QUICK=0
TRACE=0
REAL=0
OUTPUT_DIR="$REPO_ROOT/results/functional_sysbench"
RESUME=0
SELECT_METHOD=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --quick) QUICK=1; shift ;;
    --trace-replay|--mock-llm) TRACE=1; shift ;;
    --real-llm) REAL=1; shift ;;
    --resume) RESUME=1; shift ;;
    --method) SELECT_METHOD="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ "$DRY_RUN" -eq 1 ]]; then
  [[ "$QUICK" -eq 0 && "$TRACE" -eq 0 && "$REAL" -eq 0 && "$RESUME" -eq 0 ]] || { echo '--dry-run cannot be combined with live modes.' >&2; exit 2; }
else
  [[ "$QUICK" -eq 1 && $((TRACE + REAL)) -eq 1 ]] || { usage >&2; exit 2; }
fi
if [[ -n "$SELECT_METHOD" ]]; then
  case "$SELECT_METHOD" in
    fixed|mlos|mlos_ipc|mlos_cache|bayesian|dqn|qlearning|sematune_single|sematune_dual|sematune_system|sematune_ipc|sematune_trim|sematune_trim_ipc|sematune_trim_cache) ;;
    *) echo "Unknown method ID: $SELECT_METHOD" >&2; exit 2 ;;
  esac
fi

export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" validate

if [[ "$DRY_RUN" -eq 1 ]]; then
  "$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" preflight
  printf '%s\n' \
    'DRY_RUN: PASS (read-only; no root, database, provider, benchmark, output, or kernel writes)' \
    '  Workload: Sysbench OLTP read/write, 4 × 100,000-row tables, 40 threads' \
    '  Methods: Fixed; MLOS App/IPC/Cache; Bayesian; DQN; Q-learning; SemaTune Single;' \
    '           SemaTune App/System/IPC; SemaTune-Trim App/IPC/Cache' \
    '  Schedule: 5 tuning + 5 stable windows per method, 10 s/window' \
    '  Search space: the eight OS parameters used in paper Figures 6, 7, and 8' \
    '  Functional model: Gemini 2.5 Flash-Lite for every LLM role (cost-limited operational check)' \
    '  CPUs: controls/perf 0-9; Sysbench 10-19' \
    '  Expected wall time: approximately 30-50 minutes; hard timeout: 60 minutes'
  exit 0
fi

echo 'WARNING: this Sysbench suite changes scheduler, busy-poll, P-state, and C-state controls.'
echo 'Use only a dedicated/disposable bare-metal machine. Captured controls are restored and byte-verified.'

[[ -f "$SCRIPT_DIR/site.env" ]] || { echo "Missing $SCRIPT_DIR/site.env; run scripts/setup.sh --base." >&2; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"

# Sysbench cleanup/prepare is destructive to the shared OLTP tables. Prevent two
# Functional suites from overlapping on this host, which would otherwise leave
# one process observing a partially recreated database. Keep the descriptor
# open for the lifetime of this shell so flock releases it on every exit path.
LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo 'Another SemaTune Sysbench Functional suite is already running on this host.' >&2
  echo "Wait for it to finish (lock: $LOCK_FILE); do not overlap trace and real runs." >&2
  exit 1
fi
MODE='trace-replay'
if [[ "$REAL" -eq 1 ]]; then
  MODE='real-llm'
  [[ -n "${GEMINI_API_KEY:-}" ]] || { echo 'Real mode requires the caller’s exported GEMINI_API_KEY.' >&2; exit 1; }
fi

"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" validate --runtime
preflight_args=(preflight --live)
[[ "$REAL" -eq 1 ]] && preflight_args+=(--real-llm)
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" "${preflight_args[@]}"
PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" psql \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --dbname "$SEMATUNE_SYSBENCH_DB" --username "$SEMATUNE_SYSBENCH_USER" \
  --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --command 'SELECT 1' >/dev/null

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'The live suite requires root or non-interactive sudo.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=GEMINI_API_KEY,SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing a checked-in evidence output path: $OUTPUT_DIR" >&2; exit 2 ;;
esac
if [[ "$RESUME" -eq 0 && -e "$OUTPUT_DIR" && -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "Output directory must be absent or empty: $OUTPUT_DIR" >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs"

export OS_PARAM_TUNING_ROOT="$REPO_ROOT"
"$PYTHON" "$SCRIPT_DIR/functional_tool.py" machine --output "$OUTPUT_DIR/machine.json"
"$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" manifest --output "$OUTPUT_DIR/run_manifest.json" --mode "$MODE"

mapfile -t METHODS < <("$PYTHON" -c 'import json,sys; from pathlib import Path; d=json.loads(Path(sys.argv[1]).read_text()); [print(m["id"], m["config"], m["results_subdir"], m.get("trace", "-")) for m in d["methods"]]' "$SCRIPT_DIR/sysbench_suite.json")
declare -A COMPLETED=()
if [[ "$RESUME" -eq 1 ]]; then
  while IFS= read -r method; do COMPLETED["$method"]=1; done < <(
    "$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" completed-methods --results-dir "$OUTPUT_DIR"
  )
  echo "RESUME: preserving ${#COMPLETED[@]} completed method(s)"
fi
for row in "${METHODS[@]}"; do
  read -r method source_name result_subdir trace_name <<< "$row"
  materialize=(materialize --source "$SCRIPT_DIR/$source_name" --output "$OUTPUT_DIR/configs/$source_name" --results-dir "$OUTPUT_DIR/raw/$result_subdir")
  if [[ "$TRACE" -eq 1 && "$trace_name" != '-' ]]; then
    [[ -f "$SCRIPT_DIR/$trace_name" ]] || { echo "Missing recorded trace: $SCRIPT_DIR/$trace_name" >&2; exit 1; }
    materialize+=(--replay "$SCRIPT_DIR/$trace_name")
  fi
  "$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" "${materialize[@]}"
done

SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
RESTORE_NEEDED=0
ACTIVE_PID=''
ACTIVE_PGID=''
START_SECONDS=$SECONDS
SUITE_TIMEOUT_SECONDS="${SEMATUNE_FUNCTIONAL_TIMEOUT_SECONDS:-3600}"
[[ "$SUITE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo 'SEMATUNE_FUNCTIONAL_TIMEOUT_SECONDS must be positive.' >&2; exit 2; }

terminate_group() {
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    for _ in {1..20}; do
      "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null || break
      sleep 0.25
    done
    "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null && "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  [[ -n "$ACTIVE_PID" ]] && wait "$ACTIVE_PID" 2>/dev/null || true
  ACTIVE_PID=''; ACTIVE_PGID=''
}

restore_host() {
  local rc=0
  terminate_group
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$SCRIPT_DIR/host_state_guard.py" restore --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
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

run_method() {
  local method="$1" config="$2" log="$3"
  local remaining=$((SUITE_TIMEOUT_SECONDS - (SECONDS - START_SECONDS)))
  (( remaining > 0 )) || { echo 'Sysbench suite exceeded its total timeout.' >&2; return 124; }
  echo "RUNNING: $method (remaining suite timeout ${remaining}s)"
  local pgid_file="$OUTPUT_DIR/.${method}.pgid"
  "${ROOT[@]}" setsid --wait sh -c \
    'pgid_file="$1"; shift; printf "%s\n" "$$" > "$pgid_file"; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
    sh "$pgid_file" "${remaining}s" "$PYTHON" -m optimizer.main --config "$config" \
    >"$log" 2>&1 &
  ACTIVE_PID=$!
  for _ in {1..40}; do [[ -s "$pgid_file" ]] && break; sleep 0.05; done
  [[ -s "$pgid_file" ]] || { echo "Failed to establish $method process group." >&2; terminate_group; return 1; }
  ACTIVE_PGID="$(<"$pgid_file")"
  local rc=0
  # Do not block in one long `wait`: Bash can defer INT/TERM traps while a
  # sudo-wrapped child is active. Polling keeps the shell interruptible, so the
  # cleanup trap can terminate ACTIVE_PGID and restore the host immediately.
  while kill -0 "$ACTIVE_PID" 2>/dev/null; do
    local child_state
    child_state="$(ps -o stat= -p "$ACTIVE_PID" 2>/dev/null || true)"
    [[ "$child_state" == Z* ]] && break
    sleep 0.25
  done
  wait "$ACTIVE_PID" || rc=$?
  ACTIVE_PID=''; ACTIVE_PGID=''; rm -f "$pgid_file"
  [[ "$rc" -eq 0 ]] || echo "$method failed or timed out; inspect $log" >&2
  return "$rc"
}

for row in "${METHODS[@]}"; do
  read -r method source_name _ <<< "$row"
  [[ -z "$SELECT_METHOD" || "$method" == "$SELECT_METHOD" ]] || continue
  if [[ -n "${COMPLETED[$method]:-}" && -z "$SELECT_METHOD" ]]; then
    echo "SKIPPING: $method (completed 5+5 history already present)"
    continue
  fi
  run_method "$method" "$OUTPUT_DIR/configs/$source_name" "$OUTPUT_DIR/logs/$method.log"
done

restore_host
"${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
mapfile -t FINISHED < <("$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" completed-methods --results-dir "$OUTPUT_DIR")
EXPECTED_METHODS="$(jq '.methods | length' "$SCRIPT_DIR/sysbench_suite.json")"
if [[ "${#FINISHED[@]}" -eq "$EXPECTED_METHODS" ]]; then
  "$PYTHON" "$SCRIPT_DIR/tpcc_tool.py" summarize --results-dir "$OUTPUT_DIR"
  "$SCRIPT_DIR/plot.sh" --results-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR/plots"
  echo "FUNCTIONAL_SYSBENCH_RUN: PASS ($OUTPUT_DIR)"
else
  echo "FUNCTIONAL_SYSBENCH_METHOD: PASS (${SELECT_METHOD:-selected methods}; ${#FINISHED[@]}/$EXPECTED_METHODS complete; rerun with --resume)"
fi
