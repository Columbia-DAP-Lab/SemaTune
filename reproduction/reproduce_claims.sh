#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -x "$REPO_ROOT/.venv-functional/bin/python" ]]; then
  DEFAULT_PYTHON="$REPO_ROOT/.venv-functional/bin/python"
  export PATH="$REPO_ROOT/.venv-functional/bin:$PATH"
else
  DEFAULT_PYTHON=python3
fi
PYTHON="${PYTHON:-$DEFAULT_PYTHON}"
MANIFEST="$SCRIPT_DIR/claim_manifest.json"

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/reproduce_claims.sh --dry-run [--full]' \
    '  reproduction/reproduce_claims.sh --dry-run --trace-replay-from DIR' \
    '  reproduction/reproduce_claims.sh --dry-run --trace-replay-bundle DIR' \
    '  reproduction/reproduce_claims.sh --archived-only --output-dir DIR [--clean]' \
    '  reproduction/reproduce_claims.sh --run --output-dir DIR [--clean] [--trace-replay-from DIR|--trace-replay-bundle DIR] [--full] [--keep-going] [--rerun-existing]' \
    '' \
    'The live workflow regenerates archived C1-C4 evidence first, then performs one fresh' \
    'repetition over Silo, TPC-C, and Sysbench. --full uses all paper Plot 1/2/5 inputs.' \
    'Live provider execution requires GEMINI_API_KEY; trace replay forbids provider calls.'
}

MODE=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
KEEP_GOING=0
RERUN_EXISTING=0
FULL=0
CLEAN=0
TRACE_REPLAY_FROM=""
TRACE_REPLAY_BUNDLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--archived-only|--run)
      [[ -z "$MODE" ]] || { echo 'Select exactly one mode.' >&2; exit 2; }
      MODE="${1#--}"
      shift
      ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --keep-going) KEEP_GOING=1; shift ;;
    --rerun-existing) RERUN_EXISTING=1; shift ;;
    --full) FULL=1; shift ;;
    --clean) CLEAN=1; shift ;;
    --trace-replay-from) TRACE_REPLAY_FROM="${2:-}"; shift 2 ;;
    --trace-replay-bundle) TRACE_REPLAY_BUNDLE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }
if [[ "$MODE" == "dry-run" && "$CLEAN" -eq 1 ]]; then
  echo '--clean cannot be combined with --dry-run because a dry run is read-only.' >&2
  exit 2
fi
if [[ -n "$TRACE_REPLAY_FROM" && -n "$TRACE_REPLAY_BUNDLE" ]]; then
  echo 'Select only one trace source: --trace-replay-from or --trace-replay-bundle.' >&2
  exit 2
fi
if [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" && "$MODE" == "archived-only" ]]; then
  echo 'Trace replay is only valid with --dry-run or --run.' >&2
  exit 2
fi
if [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" && "$FULL" -eq 1 ]]; then
  echo 'Trace replay currently supports only the scoped C1-C4 manifest, not --full.' >&2
  exit 2
fi
if [[ -n "$TRACE_REPLAY_BUNDLE" ]]; then
  TRACE_REPLAY_BUNDLE="$(realpath -m "$TRACE_REPLAY_BUNDLE")"
fi
if [[ -n "$TRACE_REPLAY_FROM" ]]; then
  TRACE_REPLAY_FROM="$(realpath -m "$TRACE_REPLAY_FROM")"
  TRACE_REPLAY_RAW="$TRACE_REPLAY_FROM/fresh/raw"
fi

if [[ "$FULL" -eq 1 ]]; then
  "$PYTHON" "$SCRIPT_DIR/suite.py" validate
else
  "$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" validate
fi
if [[ "$MODE" == "dry-run" ]]; then
  if [[ "$FULL" -eq 1 ]]; then
    "$PYTHON" "$SCRIPT_DIR/suite.py" run --dry-run --plots 1,2,5 --output-dir "$OUTPUT_DIR/full"
    printf '%s\n' \
      'FULL CLAIM WORKFLOW: 232 unique configurations and 13430 benchmark windows.' \
      'Nominal benchmark-window time is 18.65 hours; allow one to several days overall.'
  else
    DRY_ARGS=(--manifest "$MANIFEST" run --dry-run --output-dir "$OUTPUT_DIR")
    [[ -n "$TRACE_REPLAY_FROM" ]] && DRY_ARGS+=(--replay-from "$TRACE_REPLAY_RAW")
    [[ -n "$TRACE_REPLAY_BUNDLE" ]] && DRY_ARGS+=(--replay-bundle "$TRACE_REPLAY_BUNDLE")
    "$PYTHON" "$SCRIPT_DIR/suite.py" "${DRY_ARGS[@]}"
    if [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" ]]; then
      printf '%s\n' \
        'Nominal benchmark time: 1050 windows x 5 seconds = 1.46 hours.' \
        'Recorded response delays are preserved, but no hosted-model calls or API credentials are used.'
    else
      printf '%s\n' \
        'Nominal benchmark time: 1050 windows x 5 seconds = 1.46 hours.' \
        'Allow additional time for workload startup, resets, and hosted-model calls; the target is under 10 hours.'
    fi
  fi
  exit 0
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
case "$OUTPUT_DIR/" in
  "$REPO_ROOT/all_results/paper_evaluation/"*|"$REPO_ROOT/paper_evaluation_plots/"*)
    echo "Refusing a checked-in evidence output path: $OUTPUT_DIR" >&2
    exit 2
    ;;
esac
if [[ -n "$TRACE_REPLAY_FROM" && "$TRACE_REPLAY_FROM" == "$OUTPUT_DIR" && "$CLEAN" -ne 1 ]]; then
  echo 'When replay source and output are identical, --clean is required to archive the source first.' >&2
  exit 2
fi

if [[ "$MODE" == "run" ]]; then
  SITE_ENV="$REPO_ROOT/functional_example/site.env"
  [[ -f "$SITE_ENV" ]] || { echo "Missing $SITE_ENV; run scripts/setup.sh --base." >&2; exit 2; }
  # shellcheck disable=SC1090
  source "$SITE_ENV"
  if [[ "$FULL" -eq 1 ]]; then
    "$PYTHON" "$SCRIPT_DIR/suite.py" preflight --plots 1,2,5
  else
    PREFLIGHT_ARGS=(--manifest "$MANIFEST" preflight)
    [[ -n "$TRACE_REPLAY_FROM" ]] && PREFLIGHT_ARGS+=(--replay-from "$TRACE_REPLAY_RAW")
    [[ -n "$TRACE_REPLAY_BUNDLE" ]] && PREFLIGHT_ARGS+=(--replay-bundle "$TRACE_REPLAY_BUNDLE")
    "$PYTHON" "$SCRIPT_DIR/suite.py" "${PREFLIGHT_ARGS[@]}"
  fi
fi

if [[ "$CLEAN" -eq 1 && -e "$OUTPUT_DIR" ]]; then
  case "$OUTPUT_DIR/" in
    "$REPO_ROOT/results/archive/"*)
      echo "Refusing to clean an archive path: $OUTPUT_DIR" >&2
      exit 2
      ;;
    "$REPO_ROOT/results/"?*) ;;
    *)
      echo "--clean requires --output-dir to be below $REPO_ROOT/results: $OUTPUT_DIR" >&2
      exit 2
      ;;
  esac
  ARCHIVE_ROOT="$REPO_ROOT/results/archive"
  ARCHIVE_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  ARCHIVE_DEST="$ARCHIVE_ROOT/$(basename "$OUTPUT_DIR")-$ARCHIVE_STAMP"
  [[ ! -e "$ARCHIVE_DEST" ]] || { echo "Archive destination already exists: $ARCHIVE_DEST" >&2; exit 2; }
  mkdir -p "$ARCHIVE_ROOT"
  mv -- "$OUTPUT_DIR" "$ARCHIVE_DEST"
  echo "CLEAN_ARCHIVE: $ARCHIVE_DEST"
  if [[ -n "$TRACE_REPLAY_FROM" ]]; then
    case "$TRACE_REPLAY_FROM/" in
      "$OUTPUT_DIR/"*)
        TRACE_REPLAY_FROM="$ARCHIVE_DEST${TRACE_REPLAY_FROM#"$OUTPUT_DIR"}"
        TRACE_REPLAY_RAW="$TRACE_REPLAY_FROM/fresh/raw"
        ;;
    esac
  fi
fi

mkdir -p "$OUTPUT_DIR/archived/plots"
"$SCRIPT_DIR/plot_all.sh" \
  --results-dir "$REPO_ROOT/all_results/paper_evaluation" \
  --output-dir "$OUTPUT_DIR/archived/plots" \
  --plots 1,2,5 --validation measured

if [[ "$MODE" == "archived-only" ]]; then
  echo "CLAIMS_ARCHIVED: PASS ($OUTPUT_DIR/archived/plots)"
  exit 0
fi

if [[ "$FULL" -eq 1 ]]; then
  echo 'WARNING: --full selects all canonical Plot 1, 2, and 5 dependencies.'
  echo 'It runs 232 unique configurations and can take one to several days.'
  FULL_ARGS=(--run --plots 1,2,5 --output-dir "$OUTPUT_DIR/full")
  [[ "$KEEP_GOING" -eq 1 ]] && FULL_ARGS+=(--keep-going)
  [[ "$RERUN_EXISTING" -eq 1 ]] && FULL_ARGS+=(--rerun-existing)
  "$SCRIPT_DIR/reproduce_all.sh" "${FULL_ARGS[@]}"
  printf '%s\n' \
    "REPRODUCE_CLAIMS_FULL: PASS ($OUTPUT_DIR)" \
    "  fresh full plots: $OUTPUT_DIR/full/plots" \
    "  fresh full results: $OUTPUT_DIR/full/raw" \
    "  archived plots: $OUTPUT_DIR/archived/plots"
  exit 0
fi

echo 'WARNING: the scoped suite changes scheduler, network, VM, P-state, and C-state controls.'
echo 'Use only the dedicated/disposable paper-compatible bare-metal machine.'
echo 'The provided preconfigured CloudLab host is strongly recommended.'

FRESH_DIR="$OUTPUT_DIR/fresh"
mkdir -p "$FRESH_DIR"
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

"${ROOT[@]}" "$PYTHON" "$REPO_ROOT/functional_example/host_state_guard.py" capture --output "$SNAPSHOT"
RESTORE_NEEDED=1

RUN_ARGS=(--manifest "$MANIFEST" run --output-dir "$FRESH_DIR")
[[ "$KEEP_GOING" -eq 1 ]] && RUN_ARGS+=(--keep-going)
[[ "$RERUN_EXISTING" -eq 1 ]] && RUN_ARGS+=(--rerun-existing)
[[ -n "$TRACE_REPLAY_FROM" ]] && RUN_ARGS+=(--replay-from "$TRACE_REPLAY_RAW")
[[ -n "$TRACE_REPLAY_BUNDLE" ]] && RUN_ARGS+=(--replay-bundle "$TRACE_REPLAY_BUNDLE")
"$PYTHON" "$SCRIPT_DIR/suite.py" "${RUN_ARGS[@]}"

restore_host
trap - EXIT INT TERM

"$PYTHON" "$SCRIPT_DIR/plot_claims.py" \
  --results-dir "$FRESH_DIR/raw" \
  --output-dir "$FRESH_DIR" \
  --report-dir "$OUTPUT_DIR" \
  --archived-plots-dir "$OUTPUT_DIR/archived/plots" \
  --manifest "$MANIFEST"

if [[ -n "$TRACE_REPLAY_FROM" ]]; then
  "$PYTHON" "$SCRIPT_DIR/compare_replay.py" \
    --original-dir "$TRACE_REPLAY_FROM" \
    --replay-dir "$OUTPUT_DIR" \
    --manifest "$MANIFEST"
elif [[ -n "$TRACE_REPLAY_BUNDLE" ]]; then
  "$PYTHON" "$SCRIPT_DIR/compare_replay.py" \
    --baseline-bundle "$TRACE_REPLAY_BUNDLE" \
    --replay-dir "$OUTPUT_DIR" \
    --manifest "$MANIFEST"
fi

printf '%s\n' \
  "REPRODUCE_CLAIMS: PASS ($OUTPUT_DIR)" \
  "  report: $OUTPUT_DIR/claim_report.md" \
  "  fresh plots: $FRESH_DIR/plots" \
  "  fresh tables: $FRESH_DIR/tables" \
  "  archived plots: $OUTPUT_DIR/archived/plots"
