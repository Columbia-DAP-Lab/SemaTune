#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WAIT_PID=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
MAX_ATTEMPTS=4

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/finish_plots_6_7_10_queue.sh --wait-for-pid PID [--output-dir DIR] [--max-attempts N]' \
    '' \
    'Waits for the active Plot 10 pass, resumes Plot 10 until strictly complete, then' \
    'runs the missing three-application Plots 6/7 signal matrix and regenerates plots.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait-for-pid)
      [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo '--wait-for-pid requires a positive PID.' >&2; exit 2; }
      WAIT_PID="$2"; shift 2 ;;
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo '--output-dir requires DIR.' >&2; exit 2; }
      OUTPUT_DIR="$2"; shift 2 ;;
    --max-attempts)
      [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo '--max-attempts requires a positive integer.' >&2; exit 2; }
      MAX_ATTEMPTS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$WAIT_PID" ]] || { usage >&2; exit 2; }

START_TICK=""
if [[ -r "/proc/$WAIT_PID/stat" ]]; then
  START_TICK="$(awk '{print $22}' "/proc/$WAIT_PID/stat")"
fi
printf 'Plots 6/7/10 queue: waiting for PID %s (start tick %s)\n' \
  "$WAIT_PID" "${START_TICK:-already-exited}"
while [[ -n "$START_TICK" && -r "/proc/$WAIT_PID/stat" ]]; do
  CURRENT_TICK="$(awk '{print $22}' "/proc/$WAIT_PID/stat")"
  [[ "$CURRENT_TICK" == "$START_TICK" ]] || break
  sleep 20
done

run_with_retries() {
  local label="$1"
  shift
  local attempt
  for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
    printf '%s attempt %d/%d: starting\n' "$label" "$attempt" "$MAX_ATTEMPTS"
    if "$@"; then
      printf '%s attempt %d/%d: PASS\n' "$label" "$attempt" "$MAX_ATTEMPTS"
      return 0
    fi
    printf '%s attempt %d/%d: failed; complete histories remain resumable\n' \
      "$label" "$attempt" "$MAX_ATTEMPTS" >&2
  done
  printf '%s: exhausted %d attempts\n' "$label" "$MAX_ATTEMPTS" >&2
  return 1
}

run_with_retries 'Plot 10' \
  "$SCRIPT_DIR/reproduce_c4_methods.sh" --run --output-dir "$OUTPUT_DIR" --keep-going
run_with_retries 'Plots 6/7' \
  "$SCRIPT_DIR/reproduce_three_app_plot12.sh" --run --output-dir "$OUTPUT_DIR" --keep-going

printf '%s\n' \
  'Plots 6/7/10 queue: PASS' \
  "  Plots 6/7 report: $OUTPUT_DIR/three_app_plots_6_7_report.md" \
  "  Plot 10 report: $OUTPUT_DIR/c4_method_report.md"
