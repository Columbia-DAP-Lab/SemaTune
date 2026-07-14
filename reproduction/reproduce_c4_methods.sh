#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -x "$REPO_ROOT/.venv-functional/bin/python" ]]; then
  PYTHON="${PYTHON:-$REPO_ROOT/.venv-functional/bin/python}"
  export PATH="$REPO_ROOT/.venv-functional/bin:$PATH"
else
  PYTHON="${PYTHON:-python3}"
fi
MANIFEST="$SCRIPT_DIR/c4_method_manifest.json"
MODE=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
KEEP_GOING=0

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/reproduce_c4_methods.sh --dry-run [--output-dir DIR]' \
    '  reproduction/reproduce_c4_methods.sh --run [--output-dir DIR] [--keep-going]' \
    '' \
    'Extends the live C4 result on Silo, TPC-C, and Sysbench OLTP-RW with measured' \
    'TuxBot results at 2, 8, 16, and 41 knobs, plus TuxBot-Trim and MLOS' \
    'at 2, 8, and 16 knobs. Trim/MLOS at 41 are intentionally not scheduled' \
    'because their high-dimensional iterations take too long.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--run)
      [[ -z "$MODE" ]] || { echo 'Select exactly one mode.' >&2; exit 2; }
      MODE="${1#--}"; shift ;;
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo '--output-dir requires DIR.' >&2; exit 2; }
      OUTPUT_DIR="$2"; shift 2 ;;
    --keep-going) KEEP_GOING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }

"$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" validate
if [[ "$MODE" == "dry-run" ]]; then
  "$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" run \
    --dry-run --output-dir "$OUTPUT_DIR/fresh"
  printf '%s\n' \
    'C4 METHOD DRY RUN: 33 configurations and 1650 windows.' \
    'The completed provider baseline reuses 18 configurations and 900 windows;' \
    '15 measured MLOS/TuxBot-Trim configurations and 750 windows remain.'
  exit 0
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/results/"?*) ;;
  *) echo "Output must be below $REPO_ROOT/results: $OUTPUT_DIR" >&2; exit 2 ;;
esac
# Only the strict three-workload source histories are prerequisites here.  The C4
# suite owns and updates the shared run_status.json, so requiring the older full
# C1-C3 status would make a resumable C4 retry reject its own prior status.
"$PYTHON" "$SCRIPT_DIR/validate_c123_families.py" \
  --output-dir "$OUTPUT_DIR" --baseline-only

SITE_ENV="$REPO_ROOT/functional_example/site.env"
[[ -f "$SITE_ENV" ]] || { echo "Missing $SITE_ENV; run scripts/setup.sh --base." >&2; exit 2; }
# shellcheck disable=SC1090
source "$SITE_ENV"
"$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" preflight

FRESH_DIR="$OUTPUT_DIR/fresh"
SNAPSHOT="$FRESH_DIR/host_state_before_c4_methods.json"
REPORT="$FRESH_DIR/restoration_report_c4_methods.json"
ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then ROOT=(sudo -E); fi
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
RUN_ARGS=(--manifest "$MANIFEST" run --output-dir "$FRESH_DIR")
[[ "$KEEP_GOING" -eq 1 ]] && RUN_ARGS+=(--keep-going)
"$PYTHON" "$SCRIPT_DIR/suite.py" "${RUN_ARGS[@]}"
restore_host
trap - EXIT INT TERM

"$PYTHON" "$SCRIPT_DIR/plot_c4_methods.py" \
  --results-dir "$FRESH_DIR/raw" \
  --output-dir "$FRESH_DIR" \
  --report-dir "$OUTPUT_DIR" \
  --manifest "$MANIFEST"

printf '%s\n' \
  "REPRODUCE_C4_METHODS: PASS ($OUTPUT_DIR)" \
  "  report: $OUTPUT_DIR/c4_method_report.md" \
  "  plot: $FRESH_DIR/plots/ablation_param_geomean.pdf" \
  "  table: $FRESH_DIR/tables/ablation_param_geomean.csv"
