#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-python3}"

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/reproduce_all.sh --dry-run [--plots all|1,2,...]' \
    '  reproduction/reproduce_all.sh --archived-only --output-dir DIR [--plots all|1,2,...]' \
    '  reproduction/reproduce_all.sh --run --output-dir DIR [--plots all|1,2,...] [--keep-going]' \
    '' \
    '--run performs exactly one rerun per unique selected configuration and requires GEMINI_API_KEY.' \
    'It changes host kernel controls and must run only on the dedicated paper-compatible machine.'
}

MODE=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced"
PLOTS="all"
KEEP_GOING=0
RERUN_EXISTING=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--archived-only|--run)
      [[ -z "$MODE" ]] || { echo "Select exactly one mode." >&2; exit 2; }
      MODE="${1#--}"
      shift
      ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --plots) PLOTS="${2:-}"; shift 2 ;;
    --keep-going) KEEP_GOING=1; shift ;;
    --rerun-existing) RERUN_EXISTING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }

"$PYTHON" "$SCRIPT_DIR/suite.py" validate

if [[ "$MODE" == "dry-run" ]]; then
  "$PYTHON" "$SCRIPT_DIR/suite.py" run --dry-run --plots "$PLOTS" --output-dir "$OUTPUT_DIR"
  exit 0
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing a checked-in evidence output path: $OUTPUT_DIR" >&2; exit 2 ;;
esac

if [[ "$MODE" == "archived-only" ]]; then
  mkdir -p "$OUTPUT_DIR/plots"
  "$SCRIPT_DIR/plot_all.sh" \
    --results-dir "$REPO_ROOT/all_results/paper_evaluation" \
    --output-dir "$OUTPUT_DIR/plots" \
    --plots "$PLOTS" --validation measured
  exit 0
fi

echo 'WARNING: the one-rerun paper suite changes scheduler, network, VM, P-state, and C-state controls.'
echo 'Use only the dedicated/disposable paper-compatible bare-metal machine. This suite can run for days.'

mkdir -p "$OUTPUT_DIR"
SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  command -v sudo >/dev/null || { echo 'sudo is required for the live suite.' >&2; exit 2; }
  ROOT=(sudo -E)
fi

RESTORE_NEEDED=0
restore_host() {
  local rc=0
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$REPO_ROOT/functional_example/host_state_guard.py" restore \
      --snapshot "$SNAPSHOT" --report "$REPORT" || rc=1
    "${ROOT[@]}" "$PYTHON" "$REPO_ROOT/functional_example/host_state_guard.py" verify \
      --snapshot "$SNAPSHOT" || rc=1
    RESTORE_NEEDED=0
  fi
  return "$rc"
}
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  restore_host || rc=1
  exit "$rc"
}
trap cleanup EXIT INT TERM

"${ROOT[@]}" "$PYTHON" "$REPO_ROOT/functional_example/host_state_guard.py" capture --output "$SNAPSHOT"
RESTORE_NEEDED=1

RUN_ARGS=(run --plots "$PLOTS" --output-dir "$OUTPUT_DIR")
[[ "$KEEP_GOING" -eq 1 ]] && RUN_ARGS+=(--keep-going)
[[ "$RERUN_EXISTING" -eq 1 ]] && RUN_ARGS+=(--rerun-existing)
"$PYTHON" "$SCRIPT_DIR/suite.py" "${RUN_ARGS[@]}"

restore_host
trap - EXIT INT TERM

"$SCRIPT_DIR/plot_all.sh" \
  --results-dir "$OUTPUT_DIR/raw" \
  --output-dir "$OUTPUT_DIR/plots" \
  --plots "$PLOTS" --validation fresh

echo "REPRODUCE_ALL: PASS ($OUTPUT_DIR)"
