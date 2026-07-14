#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
ENV_FILE="$SCRIPT_DIR/mutilate.env"
OUTPUT_DIR="$REPO_ROOT/results/functional_mutilate_real"
TIMEOUT_SECONDS="${SEMATUNE_MUTILATE_FUNCTIONAL_TIMEOUT_SECONDS:-1200}"
DRY_RUN=0
QUICK=0
REAL=0

usage() {
  cat <<'EOF'
Usage:
  functional_example/run_mutilate.sh --dry-run
  functional_example/run_mutilate.sh --quick --real-llm [--output-dir DIR]

Runs one reduced TuxBot App experiment: one default baseline, three tuning
windows, and two stable windows against a remote Mutilate load generator.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --quick) QUICK=1; shift ;;
    --real-llm) REAL=1; shift ;;
    --output-dir) [[ -n "${2:-}" ]] || { echo 'Missing --output-dir value.' >&2; exit 2; }; OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ "$DRY_RUN" -eq 1 ]]; then
  [[ "$QUICK" -eq 0 && "$REAL" -eq 0 ]] || { echo '--dry-run cannot be combined with live modes.' >&2; exit 2; }
else
  [[ "$QUICK" -eq 1 && "$REAL" -eq 1 ]] || { usage >&2; exit 2; }
fi
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo 'SEMATUNE_MUTILATE_FUNCTIONAL_TIMEOUT_SECONDS must be positive.' >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo 'Run scripts/setup.sh --memcached-server first.' >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "Missing $ENV_FILE; run scripts/setup.sh --memcached-server." >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
[[ "${SEMATUNE_MUTILATE_ROLE:-}" == server ]] || { echo "$ENV_FILE is not a memcached-server configuration." >&2; exit 1; }

export PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"
"$PYTHON" "$SCRIPT_DIR/mutilate_tool.py" validate-source

PREFLIGHT=(preflight
  --server-ip "${SEMATUNE_MUTILATE_SERVER_IP:?}"
  --client-ip "${SEMATUNE_MUTILATE_CLIENT_IP:?}"
  --control-port "${SEMATUNE_MUTILATE_CONTROL_PORT:-19876}")
if [[ "$DRY_RUN" -eq 1 ]]; then
  "$PYTHON" "$SCRIPT_DIR/mutilate_tool.py" "${PREFLIGHT[@]}"
  printf '%s\n' \
    'DRY_RUN: PASS (read-only; no root, provider, benchmark, output, or kernel writes)' \
    "  Server: ${SEMATUNE_MUTILATE_SERVER_IP}:11211" \
    "  Load generator: ${SEMATUNE_MUTILATE_CLIENT_IP}" \
    '  Method: TuxBot App (Flash-Lite Actor + Flash-Lite Speculator)' \
    '  Schedule: 1 default baseline + 3 tuning + 2 stable windows, 5 s/window' \
    '  Acceptance: every window must contain 2+ positive Mutilate samples'
  exit 0
fi

[[ -n "${GEMINI_API_KEY:-}" ]] || { echo 'Real mode requires the caller’s exported GEMINI_API_KEY.' >&2; exit 1; }
PREFLIGHT+=(--real-llm)
"$PYTHON" "$SCRIPT_DIR/mutilate_tool.py" "${PREFLIGHT[@]}"
"$PYTHON" "$SCRIPT_DIR/functional_tool.py" preflight --live

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=GEMINI_API_KEY,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another TuxBot Functional run owns $LOCK_FILE" >&2; exit 1; }

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing a checked-in evidence output path: $OUTPUT_DIR" >&2; exit 2 ;;
esac
if [[ -e "$OUTPUT_DIR" && -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "Output directory must be absent or empty: $OUTPUT_DIR" >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs"
CONFIG="$OUTPUT_DIR/configs/mutilate_sematune_app.json"
"$PYTHON" "$SCRIPT_DIR/mutilate_tool.py" materialize \
  --server-ip "$SEMATUNE_MUTILATE_SERVER_IP" \
  --client-ip "$SEMATUNE_MUTILATE_CLIENT_IP" \
  --control-port "${SEMATUNE_MUTILATE_CONTROL_PORT:-19876}" \
  --output "$CONFIG" --results-dir "$OUTPUT_DIR/raw"
"$PYTHON" "$SCRIPT_DIR/functional_tool.py" machine --output "$OUTPUT_DIR/machine.json"

SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
RESTORE_NEEDED=0
ACTIVE_PID=''
ACTIVE_PGID=''

terminate_group() {
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    for _ in {1..20}; do
      "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null || break
      sleep 0.25
    done
    "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null && \
      "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  [[ -n "$ACTIVE_PID" ]] && wait "$ACTIVE_PID" 2>/dev/null || true
  ACTIVE_PID=''; ACTIVE_PGID=''
}

restore_host() {
  local rc=0
  terminate_group
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
echo 'RUNNING: Mutilate TuxBot App (1 baseline + 3 tuning + 2 stable; real Gemini)'
PGID_FILE="$OUTPUT_DIR/.mutilate.pgid"
"${ROOT[@]}" setsid --wait sh -c \
  'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
  sh "$PGID_FILE" "${TIMEOUT_SECONDS}s" "$PYTHON" \
  -m optimizer.main --config "$CONFIG" \
  >"$OUTPUT_DIR/logs/mutilate_sematune_app.log" 2>&1 &
ACTIVE_PID=$!
for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
[[ -s "$PGID_FILE" ]] || { echo 'Could not establish the Mutilate process group.' >&2; exit 1; }
ACTIVE_PGID="$(<"$PGID_FILE")"
RUN_RC=0
wait "$ACTIVE_PID" || RUN_RC=$?
ACTIVE_PID=''; ACTIVE_PGID=''; rm -f "$PGID_FILE"
[[ "$RUN_RC" -eq 0 ]] || {
  echo "Mutilate Functional run failed; inspect $OUTPUT_DIR/logs/mutilate_sematune_app.log" >&2
  exit "$RUN_RC"
}

restore_host
"${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true
"$PYTHON" "$SCRIPT_DIR/mutilate_tool.py" check --output-dir "$OUTPUT_DIR" --write-summary
