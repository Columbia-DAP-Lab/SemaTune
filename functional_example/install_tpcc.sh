#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/.venv-functional"

# The base installer provides Python 3.10, PostgreSQL, perf, plotting, and the
# ignored environment-only local database credentials.
"$SCRIPT_DIR/install.sh"

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo -n true >/dev/null 2>&1 || {
    echo 'INSTALL_TPCC: FAIL: root or non-interactive sudo is required.' >&2
    exit 1
  }
  ROOT=(sudo -n)
fi

"${ROOT[@]}" apt-get update
"${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  build-essential git unzip tar

java_major="$(java -version 2>&1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' | head -n1 || true)"
if [[ -z "$java_major" || "$java_major" -lt 21 ]]; then
  "${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-21-jdk-headless
fi

echo 'Installing the pinned classical-tuner closure...'
"$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes \
  --requirement "$REPO_ROOT/requirements.txt"

expected_benchbase_commit='54d30feb1f9c8b88cca7715fc19de1622cfd1b82'
actual_benchbase_commit="$(git -c safe.directory="$REPO_ROOT/deps/benchbase" \
  -C "$REPO_ROOT/deps/benchbase" rev-parse HEAD)"
if [[ "$actual_benchbase_commit" != "$expected_benchbase_commit" ]]; then
  echo "INSTALL_TPCC: FAIL: BenchBase is at $actual_benchbase_commit, expected $expected_benchbase_commit" >&2
  exit 1
fi

BENCHBASE_JAR="$REPO_ROOT/deps/benchbase/target/benchbase-postgres/benchbase.jar"
if [[ ! -f "$BENCHBASE_JAR" ]]; then
  echo 'Building the pinned BenchBase PostgreSQL distribution...'
  (
    cd "$REPO_ROOT/deps/benchbase"
    # BenchBase's Maven metadata plugin launches Git itself. Pass the ownership
    # exception only to this build instead of changing the evaluator's global
    # Git configuration.
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0=safe.directory
    export GIT_CONFIG_VALUE_0="$REPO_ROOT/deps/benchbase"
    ./mvnw --batch-mode -DskipTests -Ddescriptors=src/main/assembly/dir.xml clean package -P postgres
  )
fi
# This pinned BenchBase revision's directory assembly retains its base
# directory, yielding target/benchbase-postgres/benchbase-postgres/. Normalize
# it to the path used by the checked-in experiment configs.
NESTED_BENCHBASE="$REPO_ROOT/deps/benchbase/target/benchbase-postgres/benchbase-postgres"
if [[ ! -f "$BENCHBASE_JAR" && -f "$NESTED_BENCHBASE/benchbase.jar" ]]; then
  cp -a "$NESTED_BENCHBASE/." "$(dirname "$BENCHBASE_JAR")/"
  rm -rf "$NESTED_BENCHBASE"
fi
[[ -f "$BENCHBASE_JAR" ]] || {
  echo "INSTALL_TPCC: FAIL: build did not produce $BENCHBASE_JAR" >&2
  exit 1
}

# shellcheck disable=SC1091
source "$SCRIPT_DIR/site.env"
PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" pg_isready \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --dbname "$SEMATUNE_SYSBENCH_DB" --username "$SEMATUNE_SYSBENCH_USER" >/dev/null

PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR" "$VENV/bin/python" "$SCRIPT_DIR/tpcc_tool.py" validate --runtime
PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR" "$VENV/bin/python" "$SCRIPT_DIR/tpcc_tool.py" preflight --live

echo 'INSTALL_TPCC: PASS'
echo "  Java: $(java -version 2>&1 | head -n1)"
echo "  BenchBase: $BENCHBASE_JAR"
echo '  Methods: Fixed, MLOS, Bayesian, DQN, Q-learning, SemaTune Single, SemaTune Dual, SemaTune-Trim'
