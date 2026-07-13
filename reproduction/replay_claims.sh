#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE="$SCRIPT_DIR/trace_baselines/c1_c4_provider"
OUTPUT_DIR="$REPO_ROOT/results/reproduced_core"
MODE=""
RESUME=0
EXTRA=()

usage() {
  printf '%s\n' \
    'Usage:' \
    '  reproduction/replay_claims.sh --dry-run [--output-dir DIR]' \
    '  reproduction/replay_claims.sh --run [--output-dir DIR] [--resume] [--keep-going] [--rerun-existing]' \
    '' \
    'Runs the scoped C1-C4 workflow from the committed provider-response baseline.' \
    'A new run safely archives the old output; --resume reuses complete jobs in place.'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--run)
      [[ -z "$MODE" ]] || { echo 'Select exactly one mode.' >&2; exit 2; }
      MODE="$1"
      shift
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || { echo '--output-dir requires a value.' >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --resume) RESUME=1; shift ;;
    --keep-going|--rerun-existing) EXTRA+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$MODE" ]] || { usage >&2; exit 2; }
[[ -f "$BUNDLE/manifest.json" ]] || { echo "Missing committed trace baseline: $BUNDLE" >&2; exit 2; }
if [[ "$MODE" == "--dry-run" && "$RESUME" -eq 1 ]]; then
  echo '--resume is only valid with --run.' >&2
  exit 2
fi

COMMAND=(
  "$SCRIPT_DIR/reproduce_claims.sh"
  "$MODE"
  --trace-replay-bundle "$BUNDLE"
  --output-dir "$OUTPUT_DIR"
)
if [[ "$MODE" == "--run" && "$RESUME" -eq 0 ]]; then
  COMMAND+=(--clean)
fi
COMMAND+=("${EXTRA[@]}")

exec env -u GEMINI_API_KEY -u OPENROUTER_API_KEY "${COMMAND[@]}"
