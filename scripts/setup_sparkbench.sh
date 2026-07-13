#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DCPERF_DIR="$REPO_ROOT/deps/DCPerf"
SPARK_INSTALL="$DCPERF_DIR/benchmarks/spark_standalone"
DATASET_NAME=bpc_t93586_s2_synthetic
WITH_DATASET=0
DATA_ROOT="${SEMATUNE_SPARKBENCH_DATA_ROOT:-/mydata/SemaTune-sparkbench}"

usage() {
  cat <<'EOF'
Usage:
  scripts/setup_sparkbench.sh
  scripts/setup_sparkbench.sh --with-dataset [--data-root DIR]

The default installs the pinned DCPerf source and checksum-verified Spark 2.4.5
runtime. --with-dataset additionally downloads and verifies the approximately
109 GB Git-LFS dataset, configures storage under /mydata, and fully populates
the Spark SQL warehouse. Allow at least 300 GiB free and up to three hours for
the first population. Interrupted partial warehouses are rebuilt safely.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-dataset) WITH_DATASET=1; shift ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$(uname -s)" == Linux ]] || { echo 'SparkBench requires Linux.' >&2; exit 1; }
[[ "$(dpkg --print-architecture)" == amd64 ]] || { echo 'The pinned SparkBench artifact requires amd64.' >&2; exit 1; }

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || { echo 'Root or non-interactive sudo is required.' >&2; exit 1; }
  ROOT=(sudo -n)
fi
"${ROOT[@]}" apt-get update
"${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates fio git git-lfs openjdk-8-jdk python3-click python3-pandas \
  python3-tabulate python3-yaml wget

if [[ ! -f "$DCPERF_DIR/benchpress_cli.py" ]]; then
  [[ -d "$REPO_ROOT/.git" || -f "$REPO_ROOT/.git" ]] || {
    echo 'DCPerf is absent. Use the materialized Zenodo archive or a Git checkout.' >&2; exit 1;
  }
  git -C "$REPO_ROOT" submodule update --init --recursive -- deps/DCPerf
fi
[[ -f "$DCPERF_DIR/benchpress_cli.py" ]] || { echo 'Failed to materialize DCPerf.' >&2; exit 1; }
if [[ -e "$DCPERF_DIR/.git" ]]; then
  actual="$(git -C "$DCPERF_DIR" rev-parse HEAD)"
  expected=5d8d16d63cf28311ee85a2f63ce7506ade67dbef
  [[ "$actual" == "$expected" ]] || { echo "DCPerf is at $actual, expected $expected." >&2; exit 1; }
fi

python3 "$SCRIPT_DIR/fetch_artifact_inputs.py" fetch spark-2.4.5-hadoop-2.7 --root "$REPO_ROOT"
mkdir -p "$SPARK_INSTALL"
verified_archive="$REPO_ROOT/downloads/spark-2.4.5-bin-hadoop2.7.tgz"
install_archive="$SPARK_INSTALL/spark-2.4.5-bin-hadoop2.7.tgz"
if [[ ! -f "$install_archive" ]]; then
  cp "$verified_archive" "$install_archive"
fi

(cd "$DCPERF_DIR" && "${ROOT[@]}" ./benchpress_cli.py install spark_standalone_local)
[[ -x "$SPARK_INSTALL/spark-2.4.5-bin-hadoop2.7/bin/spark-sql" ]] || {
  echo 'SparkBench install did not produce the Spark 2.4.5 runtime.' >&2; exit 1;
}
grep -Fxq './packages/spark_standalone/install_spark_standalone.sh' \
  "$DCPERF_DIR/benchmark_installs.txt" || {
    echo 'DCPerf did not record the SparkBench installation marker.' >&2; exit 1;
  }
"${ROOT[@]}" python3 "$SCRIPT_DIR/configure_sparkbench_population.py" \
  "$DCPERF_DIR/packages/spark_standalone/templates/proj_root/scripts/config_spark.py" \
  "$SPARK_INSTALL/scripts/config_spark.py"

if [[ "$WITH_DATASET" -eq 1 ]]; then
  DATA_ROOT="$(realpath -m "$DATA_ROOT")"
  if [[ ! -d "$DATA_ROOT" ]]; then
    "${ROOT[@]}" install -d -o "$(id -u)" -g "$(id -g)" "$DATA_ROOT"
  fi
  [[ -w "$DATA_ROOT" ]] || { echo "Dataset root is not writable: $DATA_ROOT" >&2; exit 1; }
  available_kib="$(df -Pk "$DATA_ROOT" | awk 'NR==2 {print $4}')"
  required_kib=$((300 * 1024 * 1024))
  if (( available_kib < required_kib )); then
    echo "Dataset root has less than 300 GiB free: $DATA_ROOT" >&2
    exit 1
  fi
  dataset_repo="$DATA_ROOT/DCPerf-datasets"
  if [[ ! -d "$dataset_repo/.git" ]]; then
    GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/facebookresearch/DCPerf-datasets.git "$dataset_repo"
  fi
  git -C "$dataset_repo" fetch --force origin afbc2c250aebb0c18e65a685f2b5e454e7d0c03b
  git -C "$dataset_repo" checkout --detach afbc2c250aebb0c18e65a685f2b5e454e7d0c03b
  git -C "$dataset_repo" lfs pull --include="$DATASET_NAME/**"
  python3 "$SCRIPT_DIR/verify_spark_dataset.py" "$dataset_repo"
  dataset_path="$dataset_repo/$DATASET_NAME"
  [[ -d "$dataset_path" ]] || { echo "Dataset is missing: $dataset_path" >&2; exit 1; }
  "${ROOT[@]}" mkdir -p "$SPARK_INSTALL/dataset"
  link="$SPARK_INSTALL/dataset/$DATASET_NAME"
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "Refusing to replace non-symlink dataset path: $link" >&2
    exit 1
  fi
  "${ROOT[@]}" ln -sfn "$dataset_path" "$link"
  for name in warehouse spark_local_dir; do
    "${ROOT[@]}" mkdir -p "$DATA_ROOT/$name"
  done
  for mapping in "warehouse:$DATA_ROOT/warehouse" "tmp:$DATA_ROOT/spark_local_dir"; do
    name="${mapping%%:*}"
    target="${mapping#*:}"
    link_path="$SPARK_INSTALL/$name"
    if [[ -d "$link_path" && ! -L "$link_path" ]]; then
      if find "$link_path" -mindepth 1 -print -quit | grep -q .; then
        echo "Refusing to replace non-empty Spark path: $link_path" >&2
        exit 1
      fi
      "${ROOT[@]}" rmdir "$link_path"
    elif [[ -e "$link_path" && ! -L "$link_path" ]]; then
      echo "Refusing to replace non-symlink Spark path: $link_path" >&2
      exit 1
    fi
    "${ROOT[@]}" ln -sfn "$target" "$link_path"
  done
  "$SCRIPT_DIR/populate_sparkbench.sh" --data-root "$DATA_ROOT"
  mkdir -p "$REPO_ROOT/config"
  {
    echo '# Generated by scripts/setup_sparkbench.sh.'
    printf 'export SEMATUNE_SPARKBENCH_DATA_ROOT=%q\n' "$DATA_ROOT"
    printf 'export SEMATUNE_SPARKBENCH_JAVA_HOME=%q\n' '/usr/lib/jvm/java-8-openjdk-amd64'
  } > "$REPO_ROOT/config/sparkbench.env"
fi

echo 'SPARKBENCH_SETUP: PASS'
echo "  DCPerf: $DCPERF_DIR"
echo "  Spark: $SPARK_INSTALL/spark-2.4.5-bin-hadoop2.7"
if [[ "$WITH_DATASET" -eq 1 ]]; then
  echo "  Dataset: $DATA_ROOT/DCPerf-datasets/$DATASET_NAME"
  echo "  Warehouse: populated and validated at $DATA_ROOT/warehouse"
  echo "  Runtime config: $REPO_ROOT/config/sparkbench.env"
else
  echo "  Dataset: not requested (rerun with --with-dataset; default $DATA_ROOT)"
fi
