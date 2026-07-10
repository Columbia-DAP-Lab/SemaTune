#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

BACKENDS="gemini31_final_actor,kimi_final_actor"
MAX_ITERATIONS=""
POST_TUNING_WINDOWS=""
KEEP_TEMP=0
POSITIONAL_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_model_backends_batch.sh [config_root] [num_runs] [options]

Options:
  --backends LIST         Comma-separated backend suffixes to run.
                          Default: gemini31_final_actor,kimi_final_actor
  --max-iterations N      Override max_iterations in a temporary config copy.
  --post-windows N        Override post_tuning_windows in a temporary config copy.
  --keep-temp             Keep the temporary config directory after the run.

Examples:
  ./scripts/run_model_backends_batch.sh config/full_param 1
  ./scripts/run_model_backends_batch.sh config/full_param/sysbench_oltp_rw_hi_p99 1 \
    --max-iterations 5 --post-windows 0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backends)
      BACKENDS="$2"
      shift 2
      ;;
    --max-iterations)
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --post-windows)
      POST_TUNING_WINDOWS="$2"
      shift 2
      ;;
    --keep-temp)
      KEEP_TEMP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "${#POSITIONAL_ARGS[@]}" -gt 2 ]]; then
  echo "Expected at most 2 positional arguments: [config_root] [num_runs]" >&2
  usage >&2
  exit 1
fi

CONFIG_ROOT="${POSITIONAL_ARGS[0]:-config/full_param}"
NUM_RUNS="${POSITIONAL_ARGS[1]:-1}"

if [[ ! "$NUM_RUNS" =~ ^[0-9]+$ ]] || [[ "$NUM_RUNS" -lt 1 ]]; then
  echo "num_runs must be a positive integer" >&2
  exit 1
fi

if [[ -n "$MAX_ITERATIONS" ]] && { [[ ! "$MAX_ITERATIONS" =~ ^[0-9]+$ ]] || [[ "$MAX_ITERATIONS" -lt 1 ]]; }; then
  echo "--max-iterations must be a positive integer" >&2
  exit 1
fi

if [[ -n "$POST_TUNING_WINDOWS" ]] && { [[ ! "$POST_TUNING_WINDOWS" =~ ^[0-9]+$ ]] || [[ "$POST_TUNING_WINDOWS" -lt 0 ]]; }; then
  echo "--post-windows must be a non-negative integer" >&2
  exit 1
fi

if [[ "$CONFIG_ROOT" = /* ]]; then
  CONFIG_ROOT_ABS="$(realpath -m "$CONFIG_ROOT")"
else
  CONFIG_ROOT_ABS="$(realpath -m "${REPO_ROOT}/${CONFIG_ROOT}")"
fi
if [[ ! -d "$CONFIG_ROOT_ABS" ]]; then
  echo "Config root not found: $CONFIG_ROOT_ABS" >&2
  exit 1
fi

TMP_CONFIG_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/model_backend_batch.XXXXXX")"

cleanup() {
  if [[ "$KEEP_TEMP" -eq 0 ]]; then
    rm -rf "$TMP_CONFIG_ROOT"
  else
    echo "Kept temporary configs at: $TMP_CONFIG_ROOT"
  fi
}
trap cleanup EXIT

REPO_ROOT="$REPO_ROOT" \
CONFIG_ROOT_ABS="$CONFIG_ROOT_ABS" \
TMP_CONFIG_ROOT="$TMP_CONFIG_ROOT" \
BACKENDS="$BACKENDS" \
MAX_ITERATIONS="$MAX_ITERATIONS" \
POST_TUNING_WINDOWS="$POST_TUNING_WINDOWS" \
python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

config_root = Path(os.environ["CONFIG_ROOT_ABS"])
tmp_root = Path(os.environ["TMP_CONFIG_ROOT"])
suffixes = [s.strip() for s in os.environ["BACKENDS"].split(",") if s.strip()]
max_iterations = os.environ["MAX_ITERATIONS"].strip()
post_windows = os.environ["POST_TUNING_WINDOWS"].strip()

matched = 0
for src in sorted(config_root.rglob("*.json")):
    if not any(src.name.endswith(f"_{suffix}.json") for suffix in suffixes):
        continue

    rel = src.relative_to(config_root)
    dst = tmp_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    with src.open() as f:
        data = json.load(f)

    if max_iterations:
        data["max_iterations"] = int(max_iterations)
    if post_windows:
        data["post_tuning_windows"] = int(post_windows)

    with dst.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    matched += 1

if matched == 0:
    print(
        f"No configs matched suffixes {suffixes} under {config_root}",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"Prepared {matched} backend config(s) under {tmp_root}")
PY

NEEDS_OPENROUTER="$(
  TMP_CONFIG_ROOT="$TMP_CONFIG_ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path

tmp_root = Path(os.environ["TMP_CONFIG_ROOT"])

def needs_openrouter(cfg: dict) -> bool:
    model_fields = [
        cfg.get("llm_actor_model"),
        cfg.get("llm_speculator_model"),
        cfg.get("llm_model_name"),
    ]
    for model in model_fields:
        if not model:
            continue
        if not str(model).startswith("gemini-"):
            return True
    return False

need = 0
for path in tmp_root.rglob("*.json"):
    with path.open() as f:
        cfg = json.load(f)
    if needs_openrouter(cfg):
        need = 1
        break

print(need)
PY
)"

if [[ "$NEEDS_OPENROUTER" == "1" ]] && [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY must be set in the environment for the selected backend set" >&2
  exit 1
fi

"${REPO_ROOT}/run_configs_batch.sh" "$TMP_CONFIG_ROOT" "$NUM_RUNS" --tuners "$BACKENDS"
