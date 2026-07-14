#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -x "$REPO_ROOT/.venv-functional/bin/python" ]]; then
  PYTHON="${PYTHON:-$REPO_ROOT/.venv-functional/bin/python}"
else
  PYTHON="${PYTHON:-python3}"
fi
WAIT_PID=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
KEEP_GOING=0

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/queue_c4_methods.sh --wait-for-pid PID [--output-dir DIR] [--keep-going]' \
    '' \
    'Waits for an existing C1-C3 wrapper, strictly validates its final output, and only' \
    'then starts the measured C4 TuxBot/MLOS/TuxBot-Trim comparison.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait-for-pid)
      [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || {
        echo '--wait-for-pid requires a positive PID.' >&2; exit 2;
      }
      WAIT_PID="$2"; shift 2 ;;
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo '--output-dir requires DIR.' >&2; exit 2; }
      OUTPUT_DIR="$2"; shift 2 ;;
    --keep-going) KEEP_GOING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$WAIT_PID" ]] || { usage >&2; exit 2; }

# Record the Linux process start tick so PID reuse cannot trigger a wait on an
# unrelated process. If the process already exited, validation below decides
# whether the prerequisite run completed successfully.
START_TICK=""
if [[ -r "/proc/$WAIT_PID/stat" ]]; then
  START_TICK="$(awk '{print $22}' "/proc/$WAIT_PID/stat")"
fi
printf 'C4 queue: waiting for PID %s (start tick %s)\n' "$WAIT_PID" "${START_TICK:-already-exited}"
while [[ -n "$START_TICK" && -r "/proc/$WAIT_PID/stat" ]]; do
  CURRENT_TICK="$(awk '{print $22}' "/proc/$WAIT_PID/stat")"
  [[ "$CURRENT_TICK" == "$START_TICK" ]] || break
  sleep 20
done

printf '%s\n' 'C4 queue: prerequisite process exited; validating C1-C3 output.'
"$PYTHON" "$SCRIPT_DIR/validate_c123_families.py" --output-dir "$OUTPUT_DIR"

RUN_ARGS=(--run --output-dir "$OUTPUT_DIR")
[[ "$KEEP_GOING" -eq 1 ]] && RUN_ARGS+=(--keep-going)
printf '%s\n' 'C4 queue: C1-C3 validation passed; starting C4 method comparison.'
exec "$SCRIPT_DIR/reproduce_c4_methods.sh" "${RUN_ARGS[@]}"
