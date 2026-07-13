#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DCPERF_DIR="$REPO_ROOT/deps/DCPerf"
SPARK_DIR="$DCPERF_DIR/benchmarks/spark_standalone"
SPARK_HOME="$SPARK_DIR/spark-2.4.5-bin-hadoop2.7"
DATASET_NAME=bpc_t93586_s2_synthetic
DATA_ROOT="${SEMATUNE_SPARKBENCH_DATA_ROOT:-/mydata/SemaTune-sparkbench}"
JAVA_HOME="${SEMATUNE_SPARKBENCH_JAVA_HOME:-/usr/lib/jvm/java-8-openjdk-amd64}"
TIMEOUT_SECONDS="${SEMATUNE_SPARKBENCH_POPULATE_TIMEOUT_SECONDS:-10800}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    -h|--help)
      echo 'Usage: scripts/populate_sparkbench.sh [--data-root DIR]'
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
DATA_ROOT="$(realpath -m "$DATA_ROOT")"
[[ -x "$JAVA_HOME/bin/java" ]] || { echo "Java 8 is missing: $JAVA_HOME" >&2; exit 1; }
[[ -x "$SPARK_HOME/bin/spark-sql" ]] || { echo 'SparkBench runtime is not installed.' >&2; exit 1; }
[[ -d "$SPARK_DIR/dataset/$DATASET_NAME" ]] || { echo 'SparkBench dataset is not installed.' >&2; exit 1; }
[[ "$(readlink -f "$SPARK_DIR/warehouse")" == "$DATA_ROOT/warehouse" ]] || {
  echo "Spark warehouse is not mapped to $DATA_ROOT/warehouse." >&2; exit 1;
}
[[ "$(readlink -f "$SPARK_DIR/tmp")" == "$DATA_ROOT/spark_local_dir" ]] || {
  echo "Spark shuffle storage is not mapped to $DATA_ROOT/spark_local_dir." >&2; exit 1;
}
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo 'Population timeout must be positive.' >&2; exit 2; }

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n)
fi
LOCK_FILE="/tmp/sematune-functional-sysbench-$(id -u).lock"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another SemaTune workload owns $LOCK_FILE" >&2; exit 1; }
pgrep -f 'org.apache.spark|spark_standalone.*runner.py' >/dev/null 2>&1 && {
  echo 'A Spark process is already active; refusing to populate concurrently.' >&2
  exit 1
}

marker="$DATA_ROOT/warehouse/.sematune-sparkbench-warehouse-complete.json"
database="$DATA_ROOT/warehouse/$DATASET_NAME.db"
metastore="$SPARK_HOME/metastore_db"
if [[ -f "$marker" && -d "$database" && -d "$metastore" ]]; then
  echo "SPARKBENCH_POPULATE: already complete ($DATA_ROOT/warehouse)"
  exit 0
fi

# DCPerf only checks whether these paths exist, which can mistake an interrupted
# conversion for a complete database. Without our completion marker they are
# generated partial state and must be rebuilt atomically from the verified input.
for partial in "$database" "$metastore"; do
  if [[ -e "$partial" ]]; then
    case "$partial" in
      "$DATA_ROOT/warehouse/$DATASET_NAME.db"|"$SPARK_HOME/metastore_db") ;;
      *) echo "Refusing unexpected cleanup path: $partial" >&2; exit 1 ;;
    esac
    "${ROOT[@]}" rm -rf --one-file-system "$partial"
  fi
done
"${ROOT[@]}" rm -rf --one-file-system "$SPARK_DIR/work"
"${ROOT[@]}" mkdir -p "$SPARK_DIR/work" "$DATA_ROOT/warehouse" "$DATA_ROOT/spark_local_dir"

"${ROOT[@]}" python3 "$SCRIPT_DIR/configure_sparkbench_population.py" \
  "$DCPERF_DIR/packages/spark_standalone/templates/proj_root/scripts/config_spark.py" \
  "$SPARK_DIR/scripts/config_spark.py"

population_cores=$(( $(nproc) / 4 * 4 ))
(( population_cores >= 4 )) || population_cores=4
log="$DATA_ROOT/sparkbench_population.log"
stop_spark() {
  "${ROOT[@]}" env \
    JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" \
    SEMATUNE_SPARKBENCH_POPULATE=1 \
    timeout --foreground --signal=TERM --kill-after=15s 120s \
    python3 "$SPARK_DIR/scripts/run_perf_common.py" stop --real \
      >/dev/null 2>&1 || true
}
trap 'stop_spark; exit 130' INT
trap 'stop_spark; exit 143' TERM HUP
echo "SPARKBENCH_POPULATE: building warehouse with $population_cores cores (timeout ${TIMEOUT_SECONDS}s)"
set +e
"${ROOT[@]}" env \
  JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" \
  SEMATUNE_SPARKBENCH_POPULATE=1 \
  timeout --foreground --signal=TERM --kill-after=60s "${TIMEOUT_SECONDS}s" \
  python3 "$SPARK_DIR/scripts/run_perf_common.py" install \
    -d "$DATASET_NAME" -l "$SPARK_DIR/warehouse" -k "$SPARK_DIR" \
    --worker-cores "$population_cores" --real >"$log" 2>&1
rc=$?
set -e
stop_spark
trap - INT TERM HUP
if [[ "$rc" -ne 0 ]]; then
  echo "SPARKBENCH_POPULATE: FAIL (exit $rc; log: $log)" >&2
  exit "$rc"
fi

for stage_log in "$SPARK_DIR/work/create_db.log" "$SPARK_DIR/work/create_tables.log"; do
  [[ -s "$stage_log" ]] || { echo "Missing population stage log: $stage_log" >&2; exit 1; }
  if grep -Eq 'Exception in thread|Caused by:|Traceback \(most recent call last\)' "$stage_log"; then
    echo "SPARKBENCH_POPULATE: FAIL (stage exception: $stage_log)" >&2
    exit 1
  fi
done
[[ -d "$database" && -f "$metastore/service.properties" ]] || {
  echo 'SPARKBENCH_POPULATE: FAIL (warehouse/metastore output is incomplete)' >&2
  exit 1
}
"${ROOT[@]}" python3 - "$marker" "$population_cores" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "status": "PASS",
    "dataset": "bpc_t93586_s2_synthetic",
    "dataset_commit": "afbc2c250aebb0c18e65a685f2b5e454e7d0c03b",
    "java": "OpenJDK 8",
    "population_cores": int(sys.argv[2]),
    "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
echo "SPARKBENCH_POPULATE: PASS ($marker)"
