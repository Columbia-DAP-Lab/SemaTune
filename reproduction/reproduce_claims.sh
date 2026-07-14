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
BASE_MANIFEST="$SCRIPT_DIR/claim_manifest.json"
EXTENDED_MANIFEST="$SCRIPT_DIR/extended_claim_manifest.json"
FULL_MANIFEST="$SCRIPT_DIR/full_claim_manifest.json"
MANIFEST="$BASE_MANIFEST"

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/reproduce_claims.sh --dry-run [--extended|--full]' \
    '  reproduction/reproduce_claims.sh --dry-run --trace-replay-from DIR' \
    '  reproduction/reproduce_claims.sh --dry-run --trace-replay-bundle DIR' \
    '  reproduction/reproduce_claims.sh --archived-only --output-dir DIR [--clean]' \
    '  reproduction/reproduce_claims.sh --run --output-dir DIR [--clean] [--trace-replay-from DIR|--trace-replay-bundle DIR] [--extended|--full] [--keep-going] [--rerun-existing]' \
    '' \
    'The live workflow regenerates archived C1-C4 evidence first, then performs one fresh' \
    'repetition over Silo, TPC-C, and Sysbench. Use the default for faster validation;' \
    'if evaluation time permits, --extended fully populates Plots 6/7/10 on those' \
    'workloads. --full expands Plots 6/7 to all 11 workloads.' \
    'Both omit TuxBot-Trim and MLOS at 41 knobs; no 4-knob run is selected.' \
    'Live provider execution requires GEMINI_API_KEY; trace replay forbids provider calls.'
}

need_value() {
  local option="$1" value="${2:-}"
  [[ -n "$value" && "$value" != --* ]] || {
    echo "Missing value for $option." >&2
    usage >&2
    exit 2
  }
}

MODE=""
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
KEEP_GOING=0
RERUN_EXISTING=0
FULL=0
EXTENDED=0
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
    --output-dir) need_value "$1" "${2:-}"; OUTPUT_DIR="$2"; shift 2 ;;
    --keep-going) KEEP_GOING=1; shift ;;
    --rerun-existing) RERUN_EXISTING=1; shift ;;
    --extended) EXTENDED=1; shift ;;
    --full) FULL=1; shift ;;
    --clean) CLEAN=1; shift ;;
    --trace-replay-from) need_value "$1" "${2:-}"; TRACE_REPLAY_FROM="$2"; shift 2 ;;
    --trace-replay-bundle) need_value "$1" "${2:-}"; TRACE_REPLAY_BUNDLE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }
if [[ "$EXTENDED" -eq 1 && "$FULL" -eq 1 ]]; then
  echo 'Select only one tier: --extended or --full.' >&2
  exit 2
fi
if [[ "$EXTENDED" -eq 1 ]]; then
  MANIFEST="$EXTENDED_MANIFEST"
elif [[ "$FULL" -eq 1 ]]; then
  MANIFEST="$FULL_MANIFEST"
fi
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
if [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" && ( "$EXTENDED" -eq 1 || "$FULL" -eq 1 ) ]]; then
  echo 'Trace replay currently supports only the default C1-C4 tier.' >&2
  exit 2
fi
if [[ -n "$TRACE_REPLAY_BUNDLE" ]]; then
  TRACE_REPLAY_BUNDLE="$(realpath -m "$TRACE_REPLAY_BUNDLE")"
fi
if [[ -n "$TRACE_REPLAY_FROM" ]]; then
  TRACE_REPLAY_FROM="$(realpath -m "$TRACE_REPLAY_FROM")"
  TRACE_REPLAY_RAW="$TRACE_REPLAY_FROM/fresh/raw"
fi

"$PYTHON" "$SCRIPT_DIR/suite.py" --manifest "$MANIFEST" validate
if [[ "$MODE" == "dry-run" ]]; then
  DRY_ARGS=(--manifest "$MANIFEST" run --dry-run --output-dir "$OUTPUT_DIR")
  [[ -n "$TRACE_REPLAY_FROM" ]] && DRY_ARGS+=(--replay-from "$TRACE_REPLAY_RAW")
  [[ -n "$TRACE_REPLAY_BUNDLE" ]] && DRY_ARGS+=(--replay-bundle "$TRACE_REPLAY_BUNDLE")
  "$PYTHON" "$SCRIPT_DIR/suite.py" "${DRY_ARGS[@]}"
  if [[ "$FULL" -eq 1 ]]; then
    printf '%s\n' \
      'WARNING: --full runs the complete Plot 6/7 matrix on all 11 workloads.' \
      'FULL CLAIM WORKFLOW: 164 unique configurations and 9480 benchmark windows.' \
      'This can take several days and can consume substantial hosted-model quota.'
  elif [[ "$EXTENDED" -eq 1 ]]; then
    printf '%s\n' \
      'EXTENDED CLAIM WORKFLOW: 60 unique configurations and 3360 benchmark windows.' \
      'Plot 6 includes Bayes/DQN/Q-Learning; Plot 7 includes every signal variant.' \
      'Plot 10 uses 2/8/16/41 for TuxBot and 2/8/16 for Trim/MLOS.'
  else
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

if [[ "$FULL" -eq 1 ]]; then
  printf '%s\n' \
    'WARNING: --full runs every Plot 6/7 method on all 11 selected workloads.' \
    'It selects 164 configurations and 9480 benchmark windows, can take several' \
    'days, and can consume substantial hosted-model quota. Use a dedicated host.'
elif [[ "$EXTENDED" -eq 1 ]]; then
  echo 'WARNING: --extended selects 60 configurations and 3360 benchmark windows.'
fi

if [[ "$MODE" == "run" ]]; then
  SITE_ENV="$REPO_ROOT/functional_example/site.env"
  [[ -f "$SITE_ENV" ]] || { echo "Missing $SITE_ENV; run scripts/setup.sh --base." >&2; exit 2; }
  # shellcheck disable=SC1090
  source "$SITE_ENV"
  PREFLIGHT_ARGS=(--manifest "$MANIFEST" preflight)
  [[ -n "$TRACE_REPLAY_FROM" ]] && PREFLIGHT_ARGS+=(--replay-from "$TRACE_REPLAY_RAW")
  [[ -n "$TRACE_REPLAY_BUNDLE" ]] && PREFLIGHT_ARGS+=(--replay-bundle "$TRACE_REPLAY_BUNDLE")
  "$PYTHON" "$SCRIPT_DIR/suite.py" "${PREFLIGHT_ARGS[@]}"
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
  --plots 6,7,10 --validation measured

if [[ "$MODE" == "archived-only" ]]; then
  echo "CLAIMS_ARCHIVED: PASS ($OUTPUT_DIR/archived/plots)"
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

if [[ "$EXTENDED" -eq 1 || "$FULL" -eq 1 ]]; then
  "$PYTHON" "$SCRIPT_DIR/plot_three_app_paper.py" \
    --results-dir "$FRESH_DIR/raw" \
    --output-dir "$FRESH_DIR" \
    --report-dir "$OUTPUT_DIR" \
    --manifest "$MANIFEST"
  "$PYTHON" "$SCRIPT_DIR/plot_c4_methods.py" \
    --results-dir "$FRESH_DIR/raw" \
    --output-dir "$FRESH_DIR" \
    --report-dir "$OUTPUT_DIR" \
    --manifest "$MANIFEST"
else
  "$PYTHON" "$SCRIPT_DIR/plot_claims.py" \
    --results-dir "$FRESH_DIR/raw" \
    --output-dir "$FRESH_DIR" \
    --report-dir "$OUTPUT_DIR" \
    --archived-plots-dir "$OUTPUT_DIR/archived/plots" \
    --manifest "$MANIFEST"
  PAPER_PLOT_ARGS=(
    --results-dir "$FRESH_DIR/raw"
    --output-dir "$FRESH_DIR"
    --report-dir "$OUTPUT_DIR"
    --manifest "$MANIFEST"
  )
  [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" ]] && PAPER_PLOT_ARGS+=(--allow-replay)
  "$PYTHON" "$SCRIPT_DIR/plot_three_app_paper.py" "${PAPER_PLOT_ARGS[@]}"
  C4_PLOT_ARGS=(
    --results-dir "$FRESH_DIR/raw"
    --output-dir "$FRESH_DIR"
    --report-dir "$OUTPUT_DIR"
    --manifest "$MANIFEST"
  )
  [[ -n "$TRACE_REPLAY_FROM$TRACE_REPLAY_BUNDLE" ]] && C4_PLOT_ARGS+=(--execution-mode trace-replay)
  "$PYTHON" "$SCRIPT_DIR/plot_c4_methods.py" "${C4_PLOT_ARGS[@]}"
fi

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

PASS_MARKER="REPRODUCE_CLAIMS"
[[ "$EXTENDED" -eq 1 ]] && PASS_MARKER="REPRODUCE_CLAIMS_EXTENDED"
[[ "$FULL" -eq 1 ]] && PASS_MARKER="REPRODUCE_CLAIMS_FULL"
REPORT_PATH="$OUTPUT_DIR/claim_report.md"
if [[ "$EXTENDED" -eq 1 || "$FULL" -eq 1 ]]; then
  REPORT_PATH="$OUTPUT_DIR/three_app_plots_6_7_report.md"
fi
printf '%s\n' \
  "$PASS_MARKER: PASS ($OUTPUT_DIR)" \
  "  report: $REPORT_PATH" \
  "  fresh plots: $FRESH_DIR/plots" \
  "  fresh tables: $FRESH_DIR/tables" \
  "  archived plots: $OUTPUT_DIR/archived/plots"
