#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Run generated 8-param cycle schedule configs for Spark, Silo, and TPCC.

Usage:
  ./scripts/run_8_param_cycle_schedules.sh [--bench spark|silo|tpcc|all] [--tunes 2,5] [--repeats N] [--dry-run] [--no-sudo]

Defaults:
  --bench all
  --repeats 5
  uses sudo

Generated configs are expected at:
  config/8_param_cycle_schedules/<benchmark>/
Detailed run logs are written under:
  logs/8_param_cycle_schedules/<timestamp>/

Schedule sets:
  spark: 1,2,5,10,20,30 tuning + 10 stable
  silo:  1,2,5,10,20,30,40,50 tuning + 10 stable
  tpcc:  1,2,5,10,20,30,40,50 tuning + 10 stable
EOF
}

BENCH_FILTER="all"
TUNES_FILTER_RAW=""
REPEATS=5
DRY_RUN=0
USE_SUDO=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bench)
      BENCH_FILTER="${2:-}"
      shift 2
      ;;
    --tunes)
      TUNES_FILTER_RAW="${2:-}"
      shift 2
      ;;
    --repeats)
      REPEATS="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-sudo)
      USE_SUDO=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$BENCH_FILTER" != "all" && "$BENCH_FILTER" != "spark" && "$BENCH_FILTER" != "silo" && "$BENCH_FILTER" != "tpcc" ]]; then
  echo "Error: --bench must be one of: spark, silo, tpcc, all" >&2
  exit 1
fi

if ! [[ "$REPEATS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: --repeats must be a positive integer" >&2
  exit 1
fi

declare -a TUNES_FILTER_VALUES=()
if [[ -n "$TUNES_FILTER_RAW" ]]; then
  IFS=',' read -r -a _parts <<< "$TUNES_FILTER_RAW"
  for _part in "${_parts[@]}"; do
    _token="${_part//[[:space:]]/}"
    if [[ -z "$_token" ]]; then
      continue
    fi
    if ! [[ "$_token" =~ ^[1-9][0-9]*$ ]]; then
      echo "Error: --tunes must be a comma-separated list of positive integers (example: 2,5)." >&2
      exit 1
    fi
    TUNES_FILTER_VALUES+=("$_token")
  done
  if [[ "${#TUNES_FILTER_VALUES[@]}" -eq 0 ]]; then
    echo "Error: --tunes resolved to an empty list." >&2
    exit 1
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_ROOT="$REPO_ROOT/config/8_param_cycle_schedules"
RUN_STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_ROOT="$REPO_ROOT/logs/8_param_cycle_schedules/$RUN_STAMP"

SPARK_DIR="$CONFIG_ROOT/dcperf_spark_tput"
SILO_DIR="$CONFIG_ROOT/silo_hi_p99"
TPCC_DIR="$CONFIG_ROOT/tpcc_hi_p99"

SPARK_PREFIX="dcperf_spark_config"
SILO_PREFIX="tailbench_silo_config"
TPCC_PREFIX="tpcc_config"

TUNERS=("mlos" "llm_dual_full_metrics_mode3")
SPARK_TUNING=(1 2 5 10 20 30)
TAIL_TUNING=(1 2 5 10 20 30 40 50)
STABLE=10

CURRENT_JOB=0
TOTAL_JOBS=0

benchmark_job_count() {
  local tuning_count="$1"
  echo $((tuning_count * ${#TUNERS[@]} * REPEATS))
}

count_selected_tuning_for_list() {
  if [[ "${#TUNES_FILTER_VALUES[@]}" -eq 0 ]]; then
    echo "$#"
    return
  fi
  local count=0
  local value req
  for value in "$@"; do
    for req in "${TUNES_FILTER_VALUES[@]}"; do
      if [[ "$value" == "$req" ]]; then
        count=$((count + 1))
        break
      fi
    done
  done
  echo "$count"
}

progress_bar() {
  local current="$1"
  local total="$2"
  local detail="$3"
  local width=30
  local filled=0
  if [[ "$total" -gt 0 ]]; then
    filled=$((current * width / total))
  fi
  local empty=$((width - filled))
  local bar
  bar="$(printf '%*s' "$filled" '' | tr ' ' '#')$(printf '%*s' "$empty" '' | tr ' ' '-')"
  echo "[${bar}] ${current}/${total} | ${detail}"
}

run_config() {
  local cfg="$1"
  local log_file="$2"
  if [[ ! -f "$cfg" ]]; then
    echo "Missing config: $cfg" >&2
    exit 1
  fi

  local -a base_cmd
  base_cmd=(
    env
    "GIT_CONFIG_COUNT=1"
    "GIT_CONFIG_KEY_0=safe.directory"
    "GIT_CONFIG_VALUE_0=$REPO_ROOT"
    python3 -m src.barebones_optimizer.main --config "$cfg"
  )

  local -a cmd
  if [[ "$USE_SUDO" -eq 1 ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
      cmd=("${base_cmd[@]}")
    else
      cmd=(sudo "${base_cmd[@]}")
    fi
  else
    cmd=("${base_cmd[@]}")
  fi

  mkdir -p "$(dirname "$log_file")"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Cmd: ${cmd[*]}"
    echo "Log: $log_file"
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if (
      cd "$REPO_ROOT"
      "${cmd[@]}" >"$log_file" 2>&1
    ); then
      :
    else
      local rc=$?
      echo "Run failed with exit code $rc. Last log lines:"
      tail -n 40 "$log_file" || true
      exit "$rc"
    fi
  fi
}

run_benchmark() {
  local bench="$1"
  local dir="$2"
  local prefix="$3"
  shift 3
  local all_tuning_list=("$@")
  local tuning_list=()
  if [[ "${#TUNES_FILTER_VALUES[@]}" -eq 0 ]]; then
    tuning_list=("${all_tuning_list[@]}")
  else
    local tune req
    for tune in "${all_tuning_list[@]}"; do
      for req in "${TUNES_FILTER_VALUES[@]}"; do
        if [[ "$tune" == "$req" ]]; then
          tuning_list+=("$tune")
          break
        fi
      done
    done
  fi

  if [[ "${#tuning_list[@]}" -eq 0 ]]; then
    echo ""
    echo "=== Benchmark: $bench ==="
    echo "Skipping benchmark: no selected tuning windows match this schedule."
    return
  fi

  local local_done=0
  local total_local=$(( ${#tuning_list[@]} * ${#TUNERS[@]} * REPEATS ))

  echo ""
  echo "=== Benchmark: $bench ==="
  echo "Repeats per tuner/config: $REPEATS"
  if [[ "${#TUNES_FILTER_VALUES[@]}" -gt 0 ]]; then
    echo "Selected tuning windows: ${tuning_list[*]}"
  fi
  echo "Planned runs for benchmark: $total_local"
  for tune in "${tuning_list[@]}"; do
    for tuner in "${TUNERS[@]}"; do
      local cfg="${dir}/${prefix}_${tuner}_tune${tune}_stable${STABLE}.json"
      for ((rep=1; rep<=REPEATS; rep++)); do
        CURRENT_JOB=$((CURRENT_JOB + 1))
        local_done=$((local_done + 1))
        progress_bar "$CURRENT_JOB" "$TOTAL_JOBS" "${bench} ${local_done}/${total_local} tune=${tune} tuner=${tuner} repeat=${rep}/${REPEATS}"
        local log_file="${LOG_ROOT}/${bench}/tune${tune}/${tuner}_repeat${rep}.log"
        run_config "$cfg" "$log_file"
      done
    done
  done
}

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "spark" ]]; then
  TOTAL_JOBS=$((TOTAL_JOBS + $(benchmark_job_count "$(count_selected_tuning_for_list "${SPARK_TUNING[@]}")")))
fi

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "silo" ]]; then
  TOTAL_JOBS=$((TOTAL_JOBS + $(benchmark_job_count "$(count_selected_tuning_for_list "${TAIL_TUNING[@]}")")))
fi

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "tpcc" ]]; then
  TOTAL_JOBS=$((TOTAL_JOBS + $(benchmark_job_count "$(count_selected_tuning_for_list "${TAIL_TUNING[@]}")")))
fi

if [[ "$TOTAL_JOBS" -eq 0 ]]; then
  echo "Error: no matching runs to execute for the current --bench/--tunes selection." >&2
  exit 1
fi

echo "Total planned runs: $TOTAL_JOBS"
echo "Log directory: $LOG_ROOT"

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "spark" ]]; then
  run_benchmark "dcperf_spark_tput" "$SPARK_DIR" "$SPARK_PREFIX" "${SPARK_TUNING[@]}"
fi

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "silo" ]]; then
  run_benchmark "silo_hi_p99" "$SILO_DIR" "$SILO_PREFIX" "${TAIL_TUNING[@]}"
fi

if [[ "$BENCH_FILTER" == "all" || "$BENCH_FILTER" == "tpcc" ]]; then
  run_benchmark "tpcc_hi_p99" "$TPCC_DIR" "$TPCC_PREFIX" "${TAIL_TUNING[@]}"
fi

echo ""
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run complete."
else
  echo "Batch run complete."
fi
