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
MANIFEST="$SCRIPT_DIR/c123_family_manifest.json"

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/reproduce_c123_families.sh --dry-run [--output-dir DIR]' \
    '  reproduction/reproduce_c123_families.sh --run [--output-dir DIR] [--clean] [--seed-live-from DIR] [--keep-going]' \
    '' \
    'Runs one real-provider repetition for C1-C3 on the paper BenchBase, Sysbench,' \
    'and TailBench workloads. Completed provider-backed jobs resume in place.' \
    '--clean moves an existing output below results/archive before starting all jobs anew.' \
    '--seed-live-from safely replaces a replay output with a validated live-provider baseline.' \
    'Trace replay and C4 parameter sweeps are intentionally unsupported.'
}

MODE=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
SEED_LIVE_FROM=""
KEEP_GOING=0
CLEAN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--run)
      [[ -z "$MODE" ]] || { echo 'Select exactly one mode.' >&2; exit 2; }
      MODE="${1#--}"
      shift
      ;;
    --output-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo '--output-dir requires DIR.' >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --seed-live-from)
      [[ $# -ge 2 && -n "$2" ]] || { echo '--seed-live-from requires DIR.' >&2; exit 2; }
      SEED_LIVE_FROM="$2"
      shift 2
      ;;
    --clean) CLEAN=1; shift ;;
    --keep-going) KEEP_GOING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }
if [[ "$MODE" == "dry-run" && -n "$SEED_LIVE_FROM" ]]; then
  echo '--seed-live-from changes result placement and cannot be combined with --dry-run.' >&2
  exit 2
fi
if [[ "$MODE" == "dry-run" && "$CLEAN" -eq 1 ]]; then
  echo '--clean cannot be combined with --dry-run because a dry run is read-only.' >&2
  exit 2
fi
if [[ "$CLEAN" -eq 1 && -n "$SEED_LIVE_FROM" ]]; then
  echo 'Select either --clean for all-new results or --seed-live-from to reuse a provider baseline.' >&2
  exit 2
fi

"$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" validate
if [[ "$MODE" == "dry-run" ]]; then
  "$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" run \
    --dry-run --output-dir "$OUTPUT_DIR/fresh"
  printf '%s\n' \
    'C1-C3 FAMILY DRY RUN: 44 configurations, 22 LLM configurations, 2200 windows.' \
    'A completed three-workload provider baseline reuses 12 configurations and 600 windows;' \
    'the extension executes 32 configurations and 1600 windows.' \
    'No output, API request, benchmark, database setup, or host-control change was performed.'
  exit 0
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing a checked-in evidence output path: $OUTPUT_DIR" >&2
    exit 2
    ;;
  "$REPO_ROOT/results/"?*) ;;
  *) echo "Output must be below $REPO_ROOT/results: $OUTPUT_DIR" >&2; exit 2 ;;
esac
if [[ "$CLEAN" -eq 1 ]]; then
  case "$OUTPUT_DIR/" in
    "$REPO_ROOT/results/archive/"*)
      echo "Refusing to clean an archive path: $OUTPUT_DIR" >&2
      exit 2
      ;;
  esac
fi

SITE_ENV="$REPO_ROOT/functional_example/site.env"
[[ -f "$SITE_ENV" ]] || { echo "Missing $SITE_ENV; run scripts/setup.sh --base." >&2; exit 2; }
# shellcheck disable=SC1090
source "$SITE_ENV"
"$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" preflight

if [[ "$CLEAN" -eq 1 && -e "$OUTPUT_DIR" ]]; then
  case "$OUTPUT_DIR/" in
    "$REPO_ROOT/results/archive/"*)
      echo "Refusing to clean an archive path: $OUTPUT_DIR" >&2
      exit 2
      ;;
  esac
  ARCHIVE_ROOT="$REPO_ROOT/results/archive"
  ARCHIVE_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  ARCHIVE_DEST="$ARCHIVE_ROOT/$(basename "$OUTPUT_DIR")-pre-c123-clean-$ARCHIVE_STAMP"
  [[ ! -e "$ARCHIVE_DEST" ]] || { echo "Archive destination exists: $ARCHIVE_DEST" >&2; exit 2; }
  mkdir -p "$ARCHIVE_ROOT"
  mv -- "$OUTPUT_DIR" "$ARCHIVE_DEST"
  echo "C123_CLEAN_ARCHIVE: $ARCHIVE_DEST"
fi

if [[ -n "$SEED_LIVE_FROM" ]]; then
  SEED_LIVE_FROM="$(realpath -m "$SEED_LIVE_FROM")"
  [[ "$SEED_LIVE_FROM" != "$OUTPUT_DIR" ]] || {
    echo '--seed-live-from must differ from --output-dir.' >&2
    exit 2
  }
  "$PYTHON" "$SCRIPT_DIR/validate_c123_families.py" \
    --output-dir "$SEED_LIVE_FROM" --manifest "$MANIFEST" --baseline-only
  if [[ -f "$OUTPUT_DIR/fresh/run_status.json" ]]; then
    existing_mode="$($PYTHON -c 'import json,sys; print(json.load(open(sys.argv[1])).get("execution_mode", ""))' "$OUTPUT_DIR/fresh/run_status.json")"
    if [[ "$existing_mode" == "real-provider" ]]; then
      echo 'Output is already provider-backed; omit --seed-live-from to resume it.' >&2
      exit 2
    fi
  fi
  if [[ -e "$OUTPUT_DIR" ]]; then
    ARCHIVE_ROOT="$REPO_ROOT/results/archive"
    ARCHIVE_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    ARCHIVE_DEST="$ARCHIVE_ROOT/$(basename "$OUTPUT_DIR")-pre-c123-$ARCHIVE_STAMP"
    [[ ! -e "$ARCHIVE_DEST" ]] || { echo "Archive destination exists: $ARCHIVE_DEST" >&2; exit 2; }
    mkdir -p "$ARCHIVE_ROOT"
    mv -- "$OUTPUT_DIR" "$ARCHIVE_DEST"
    echo "C123_PREVIOUS_OUTPUT_ARCHIVE: $ARCHIVE_DEST"
  fi
  mkdir -p "$(dirname "$OUTPUT_DIR")"
  cp -a -- "$SEED_LIVE_FROM" "$OUTPUT_DIR"
  echo "C123_PROVIDER_BASELINE_RESTORED: $SEED_LIVE_FROM -> $OUTPUT_DIR"
fi

if [[ -e "$OUTPUT_DIR" ]]; then
  "$PYTHON" "$SCRIPT_DIR/validate_c123_families.py" \
    --output-dir "$OUTPUT_DIR" --manifest "$MANIFEST" --baseline-only
else
  mkdir -p "$OUTPUT_DIR/fresh"
fi

echo 'WARNING: this suite changes scheduler, network, VM, P-state, and C-state controls.'
echo 'Use only the dedicated/disposable paper-compatible bare-metal machine.'

FRESH_DIR="$OUTPUT_DIR/fresh"
SNAPSHOT="$FRESH_DIR/host_state_before.json"
REPORT="$FRESH_DIR/restoration_report.json"
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

"${ROOT[@]}" "$PYTHON" "$REPO_ROOT/functional_example/host_state_guard.py" capture \
  --output "$SNAPSHOT"
RESTORE_NEEDED=1

RUN_ARGS=(--manifest "$MANIFEST" run --output-dir "$FRESH_DIR")
[[ "$KEEP_GOING" -eq 1 ]] && RUN_ARGS+=(--keep-going)
"$PYTHON" "$SCRIPT_DIR/suite.py" "${RUN_ARGS[@]}"

restore_host
trap - EXIT INT TERM

"$PYTHON" "$SCRIPT_DIR/plot_c123_families.py" \
  --results-dir "$FRESH_DIR/raw" \
  --output-dir "$FRESH_DIR" \
  --report-dir "$OUTPUT_DIR" \
  --manifest "$MANIFEST"
"$PYTHON" "$SCRIPT_DIR/validate_c123_families.py" \
  --output-dir "$OUTPUT_DIR" --manifest "$MANIFEST"

printf '%s\n' \
  "REPRODUCE_C123_FAMILIES: PASS ($OUTPUT_DIR)" \
  "  report: $OUTPUT_DIR/c123_family_report.md" \
  "  plot 6: $FRESH_DIR/plots/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf" \
  "  plot 7: $FRESH_DIR/plots/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf" \
  "  tables: $FRESH_DIR/tables/c123_*.csv" \
  '  C4: not evaluated; trace replay: not performed'
