#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FUNCTIONAL="$REPO_ROOT/functional_example"
SOURCE="$REPO_ROOT/reproduction/configs/parameter_count/sysbench_oltp_rw_final/41_param/sysbench_oltp_rw_hi_p99/mlos.json"
OUTPUT_DIR="$REPO_ROOT/results/sysbench_41param_smoke"
ITERATIONS=3
IGNORE_LOCK=0
TIMEOUT_SECONDS="${SEMATUNE_SYSBENCH_41PARAM_TIMEOUT_SECONDS:-600}"
PYTHON="$REPO_ROOT/.venv-functional/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --iterations) ITERATIONS="${2:-}"; shift 2 ;;
    --ignore-lock) IGNORE_LOCK=1; shift ;;
    -h|--help)
      echo 'Usage: scripts/run_sysbench_41param_smoke.sh [--iterations N] [--ignore-lock] [--output-dir DIR]'
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ "$ITERATIONS" =~ ^[1-9][0-9]*$ ]] || { echo '--iterations must be positive.' >&2; exit 2; }
[[ -f "$FUNCTIONAL/site.env" ]] || { echo 'Run scripts/setup.sh --base first.' >&2; exit 1; }
# shellcheck disable=SC1091
source "$FUNCTIONAL/site.env"
export PYTHONPATH="$REPO_ROOT/src:$FUNCTIONAL"
export OS_PARAM_TUNING_ROOT="$REPO_ROOT"

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
if [[ -e "$OUTPUT_DIR" && -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "Output directory must be absent or empty: $OUTPUT_DIR" >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR/raw" "$OUTPUT_DIR/configs" "$OUTPUT_DIR/logs"
CONFIG="$OUTPUT_DIR/configs/sysbench_41param_mlos_smoke.json"
"$PYTHON" - "$SOURCE" "$CONFIG" "$OUTPUT_DIR/raw" "$ITERATIONS" <<'PY'
import json
import sys
from pathlib import Path

source, output, results, iterations = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
payload = json.loads(source.read_text(encoding="utf-8"))
if payload.get("benchmark") != "sysbench_oltp" or payload.get("tuner_type") != "mlos":
    raise SystemExit("canonical 41-parameter MLOS Sysbench config changed")
if len(payload.get("parameters_to_tune") or []) != 41:
    raise SystemExit("canonical Sysbench config does not contain 41 parameters")
payload.update({
    "max_iterations": iterations,
    "post_tuning_windows": 0,
    "window_duration": 5,
    "respect_config_window_duration": True,
    "experiment_profile": None,
    "results_dir": str(results.resolve()),
    "previous_run_gist": None,
    "llm_replay_file": None,
    "llm_api_key": None,
    "openrouter_api_key": None,
})

def common_tokens(paths):
    values = None
    for path in paths:
        if not path.is_file():
            continue
        current = set(path.read_text(encoding="utf-8").split())
        values = current if values is None else values & current
    return values or set()

policies = [
    Path(f"/sys/devices/system/cpu/cpufreq/policy{cpu}")
    for cpu in range(10)
]
available_governors = common_tokens(
    [policy / "scaling_available_governors" for policy in policies]
)
available_epp = common_tokens(
    [policy / "energy_performance_available_preferences" for policy in policies]
)
available_congestion = set(
    Path("/proc/sys/net/ipv4/tcp_available_congestion_control")
    .read_text(encoding="utf-8")
    .split()
)
for name, available in (
    ("scaling_governor", available_governors),
    ("epp", available_epp),
    ("tcp_congestion_control", available_congestion),
):
    configured = payload["parameter_ranges"][name]
    supported = [value for value in configured if str(value) in available]
    if not supported:
        raise SystemExit(f"no supported values remain for {name}")
    payload["parameter_ranges"][name] = supported
if "unlimited" not in payload["parameter_ranges"]["cstate_max"]:
    payload["parameter_ranges"]["cstate_max"].append("unlimited")
from optimizer.config import SimpleConfig
config = SimpleConfig.from_dict(payload)
config.validate()
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"SYSBENCH_41PARAM_CONFIG: PASS ({iterations} MLOS iterations, 5 seconds/window)")
PY

LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
if [[ "$IGNORE_LOCK" -eq 1 ]]; then
  echo 'WARNING: shared host lock explicitly bypassed for this smoke run.'
elif ! flock -n 9; then
  echo "WAITING: another SemaTune workload owns $LOCK_FILE"
  echo 'The 41-parameter Sysbench smoke will start automatically when it releases the host.'
  flock 9
fi

"$PYTHON" "$FUNCTIONAL/tpcc_tool.py" preflight --live
PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" psql \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --dbname "$SEMATUNE_SYSBENCH_DB" --username "$SEMATUNE_SYSBENCH_USER" \
  --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --command 'SELECT 1' >/dev/null

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n --preserve-env=SEMATUNE_SYSBENCH_HOST,SEMATUNE_SYSBENCH_PORT,SEMATUNE_SYSBENCH_USER,SEMATUNE_SYSBENCH_PASSWORD,SEMATUNE_SYSBENCH_DB,OS_PARAM_TUNING_ROOT,PYTHONPATH)
fi

SNAPSHOT="$OUTPUT_DIR/host_state_before.json"
REPORT="$OUTPUT_DIR/restoration_report.json"
RESTORE_NEEDED=0
ACTIVE_PGID=''
restore_host() {
  local rc=0
  if [[ -n "$ACTIVE_PGID" ]] && "${ROOT[@]}" kill -0 -- "-$ACTIVE_PGID" 2>/dev/null; then
    "${ROOT[@]}" kill -TERM -- "-$ACTIVE_PGID" 2>/dev/null || true
    sleep 1
    "${ROOT[@]}" kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
  fi
  ACTIVE_PGID=''
  if [[ "$RESTORE_NEEDED" -eq 1 ]]; then
    "${ROOT[@]}" "$PYTHON" "$FUNCTIONAL/host_state_guard.py" restore \
      --snapshot "$SNAPSHOT" --report "$REPORT" || rc=$?
    RESTORE_NEEDED=0
  fi
  return "$rc"
}
cleanup() {
  local rc=$? restore_rc=0
  trap - EXIT INT TERM HUP
  restore_host || restore_rc=$?
  [[ "$rc" -eq 0 && "$restore_rc" -ne 0 ]] && rc=$restore_rc
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

"${ROOT[@]}" "$PYTHON" "$FUNCTIONAL/host_state_guard.py" capture --output "$SNAPSHOT"
RESTORE_NEEDED=1
echo "RUNNING: Sysbench OLTP-RW 41-parameter MLOS smoke ($ITERATIONS iterations)"
PGID_FILE="$OUTPUT_DIR/.run.pgid"
"${ROOT[@]}" setsid --wait sh -c \
  'printf "%s\n" "$$" > "$1"; shift; exec timeout --foreground --signal=TERM --kill-after=20s "$@"' \
  sh "$PGID_FILE" "${TIMEOUT_SECONDS}s" "$PYTHON" \
  -m optimizer.main --config "$CONFIG" \
  >"$OUTPUT_DIR/logs/sysbench_41param.log" 2>&1 &
RUN_PID=$!
for _ in {1..40}; do [[ -s "$PGID_FILE" ]] && break; sleep 0.05; done
[[ -s "$PGID_FILE" ]] || { echo 'Could not establish benchmark process group.' >&2; exit 1; }
ACTIVE_PGID="$(<"$PGID_FILE")"
wait "$RUN_PID"
ACTIVE_PGID=''
rm -f "$PGID_FILE"
restore_host
"${ROOT[@]}" chown -R "$(id -u):$(id -g)" "$OUTPUT_DIR" 2>/dev/null || true

"$PYTHON" - "$OUTPUT_DIR/raw" "$ITERATIONS" "$REPORT" "$OUTPUT_DIR/smoke_summary.json" "$CONFIG" "$OUTPUT_DIR/logs/sysbench_41param.log" <<'PY'
import json
import math
import sys
from pathlib import Path

raw, expected, restoration_path, output, config_path, log_path = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]), Path(sys.argv[6])
histories = sorted(raw.glob("optimization_history_sysbench_oltp_*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
if not histories:
    raise SystemExit("no Sysbench optimization history produced")
history = json.loads(histories[0].read_text(encoding="utf-8"))
rows = [row for row in history.get("history") or [] if 1 <= int(row.get("iteration", -1)) <= expected]
values = [float((row.get("metrics") or {}).get("latency_p99")) for row in rows]
if len(rows) != expected or any(not math.isfinite(value) or value <= 0 for value in values):
    raise SystemExit(f"expected {expected} positive latency windows, found {len(rows)}")
config = json.loads(config_path.read_text(encoding="utf-8"))
expected_parameters = set(config.get("parameters_to_tune") or [])
observed_parameters = set().union(*(set(row.get("parameters") or {}) for row in rows))
if expected_parameters != observed_parameters:
    raise SystemExit(
        f"parameter application coverage mismatch: missing={sorted(expected_parameters - observed_parameters)}"
    )
log_text = log_path.read_text(encoding="utf-8", errors="replace")
application_markers = (
    "Applied mlos tuner parameters",
    "Applied tuner parameters IMMEDIATELY",
)
if not any(marker in log_text for marker in application_markers) or "Failed to apply" in log_text:
    raise SystemExit("the optimizer did not successfully apply the complete MLOS proposal")
restoration = json.loads(restoration_path.read_text(encoding="utf-8"))
if restoration.get("restore_status") != "PASS" or restoration.get("verify_status") != "PASS" or restoration.get("byte_mismatches"):
    raise SystemExit("host restoration did not pass")
summary = {
    "status": "PASS",
    "benchmark": "sysbench_oltp",
    "tuner": "mlos",
    "parameter_count": 41,
    "iterations": expected,
    "latency_p99_ms": values,
    "best_latency_p99_ms": min(values),
    "mean_latency_p99_ms": sum(values) / len(values),
    "restoration": "PASS",
    "restored_control_count": restoration.get("control_count"),
    "history": str(histories[0].resolve()),
}
output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
print(f"SYSBENCH_41PARAM_SMOKE: PASS ({output})")
PY
