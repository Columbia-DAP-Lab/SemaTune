#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FUNCTIONAL_DIR="$REPO_ROOT/functional_example"
VENV="$REPO_ROOT/.venv-functional"
SITE_ENV="$FUNCTIONAL_DIR/site.env"
MODE=base
MODE_SELECTED=0

usage() {
  cat <<'EOF'
Usage: scripts/setup.sh [--base | --full]

Set up SemaTune on Ubuntu 22.04. The default is --base.

  --base  Install the complete Functional environment: the hash-locked Python
          environment, Sysbench, PostgreSQL and its local database, Java 21,
          and the pinned BenchBase PostgreSQL build.
  --full  Install --base plus all pinned benchmark submodules and the native
          software dependencies for Mutilate, TailBench, and DCPerf/SparkBench;
          build Mutilate and TailBench, install TailBench inputs, and install
          SparkBench with its verified dataset and populated warehouse.
  -h, --help

Neither mode runs a benchmark. Full mode does not automate the second-node
Mutilate deployment or allocation-specific network addresses.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) ((MODE_SELECTED += 1)); MODE=base; shift ;;
    --full) ((MODE_SELECTED += 1)); MODE=full; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if (( MODE_SELECTED > 1 )); then
  echo 'Choose exactly one of --base or --full.' >&2
  usage >&2
  exit 2
fi

if [[ "$(uname -s)" != Linux ]]; then
  echo 'SETUP: FAIL: SemaTune requires Linux bare metal.' >&2
  exit 1
fi
if [[ "$(dpkg --print-architecture)" != amd64 ]]; then
  echo 'SETUP: FAIL: the locked artifact environment requires amd64.' >&2
  exit 1
fi
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 22.04 ]]; then
    echo "SETUP: FAIL: Ubuntu 22.04 is required (found ${PRETTY_NAME:-unknown})." >&2
    exit 1
  fi
fi

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  if ! sudo -n true >/dev/null 2>&1; then
    echo 'SETUP: FAIL: root or non-interactive sudo is required.' >&2
    exit 1
  fi
  ROOT=(sudo -n)
fi

apt_install() {
  "${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    --no-install-recommends "$@"
}

ensure_submodule() {
  local relative="$1" marker="$2"
  if [[ -e "$REPO_ROOT/$relative/$marker" ]]; then
    return
  fi
  if [[ ! -d "$REPO_ROOT/.git" && ! -f "$REPO_ROOT/.git" ]]; then
    echo "SETUP: FAIL: $relative is absent from this archive." >&2
    echo 'Use the recursively materialized Zenodo archive or a Git checkout.' >&2
    exit 1
  fi
  git -C "$REPO_ROOT" submodule update --init --recursive -- "$relative"
  [[ -e "$REPO_ROOT/$relative/$marker" ]] || {
    echo "SETUP: FAIL: materializing $relative did not create $marker." >&2
    exit 1
  }
}

verify_submodule_commit() {
  local relative="$1" expected="$2"
  # A Git checkout retains submodule metadata and can be verified directly.
  # The canonical Zenodo archive is metadata-free and is instead protected by
  # the archive-wide SHA256SUMS manifest.
  if [[ -e "$REPO_ROOT/$relative/.git" ]]; then
    local actual
    actual="$(git -C "$REPO_ROOT/$relative" rev-parse HEAD)"
    [[ "$actual" == "$expected" ]] || {
      echo "SETUP: FAIL: $relative is at $actual, expected $expected." >&2
      exit 1
    }
  fi
}

echo "Setting up SemaTune ($MODE)..."
"${ROOT[@]}" apt-get update
apt_install \
  ca-certificates ethtool git jq linux-tools-common linux-tools-generic numactl \
  openssl pciutils postgresql postgresql-client python3.10 python3.10-venv \
  python3-pip sysbench unzip util-linux build-essential openjdk-21-jdk-headless

JAVA21_HOME=/usr/lib/jvm/java-21-openjdk-amd64
[[ -x "$JAVA21_HOME/bin/java" && -x "$JAVA21_HOME/bin/javac" ]] || {
  echo "SETUP: FAIL: OpenJDK 21 was installed without the expected $JAVA21_HOME." >&2
  exit 1
}
"${ROOT[@]}" update-alternatives --set java "$JAVA21_HOME/bin/java"
"${ROOT[@]}" update-alternatives --set javac "$JAVA21_HOME/bin/javac"

if ! command -v perf >/dev/null 2>&1; then
  kernel_tools="linux-tools-$(uname -r)"
  if ! apt_install "$kernel_tools"; then
    echo "SETUP: FAIL: perf is unavailable and $kernel_tools could not be installed." >&2
    exit 1
  fi
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  python3.10 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes \
  --requirement "$REPO_ROOT/requirements-bootstrap.lock"
"$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes \
  --requirement "$REPO_ROOT/requirements.txt"

"${ROOT[@]}" systemctl enable --now postgresql

if [[ ! -f "$SITE_ENV" ]]; then
  umask 077
  database_password="$(openssl rand -hex 24)"
  printf '%s\n' \
    'export SEMATUNE_SYSBENCH_HOST=127.0.0.1' \
    'export SEMATUNE_SYSBENCH_PORT=5432' \
    'export SEMATUNE_SYSBENCH_USER=admin' \
    "export SEMATUNE_SYSBENCH_PASSWORD=$database_password" \
    'export SEMATUNE_SYSBENCH_DB=benchdb' > "$SITE_ENV"
  chmod 600 "$SITE_ENV"
else
  # shellcheck disable=SC1090
  source "$SITE_ENV"
  database_password="${SEMATUNE_SYSBENCH_PASSWORD:?site.env lacks SEMATUNE_SYSBENCH_PASSWORD}"
fi

POSTGRES_PSQL=(runuser -u postgres -- psql)
if [[ "$(id -u)" -ne 0 ]]; then
  POSTGRES_PSQL=(sudo -n -u postgres psql)
fi
"${POSTGRES_PSQL[@]}" --quiet --no-psqlrc --set ON_ERROR_STOP=1 \
  --set role_password="$database_password" <<'SQL'
SELECT format('CREATE ROLE admin LOGIN PASSWORD %L', :'role_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin') \gexec
SELECT format('ALTER ROLE admin WITH LOGIN PASSWORD %L', :'role_password') \gexec
SELECT 'CREATE DATABASE benchdb OWNER admin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'benchdb') \gexec
SQL

# shellcheck disable=SC1090
source "$SITE_ENV"
PGPASSWORD="$SEMATUNE_SYSBENCH_PASSWORD" psql \
  --host "$SEMATUNE_SYSBENCH_HOST" --port "$SEMATUNE_SYSBENCH_PORT" \
  --username "$SEMATUNE_SYSBENCH_USER" --dbname "$SEMATUNE_SYSBENCH_DB" \
  --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --command 'SELECT 1' >/dev/null

ensure_submodule deps/benchbase mvnw
verify_submodule_commit deps/benchbase 54d30feb1f9c8b88cca7715fc19de1622cfd1b82
BENCHBASE_DIR="$REPO_ROOT/deps/benchbase"
BENCHBASE_JAR="$BENCHBASE_DIR/target/benchbase-postgres/benchbase.jar"
if [[ ! -f "$BENCHBASE_JAR" ]]; then
  echo 'Building the pinned BenchBase PostgreSQL distribution...'
  (
    cd "$BENCHBASE_DIR"
    export JAVA_HOME="$JAVA21_HOME"
    export PATH="$JAVA_HOME/bin:$PATH"
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0=safe.directory
    export GIT_CONFIG_VALUE_0="$BENCHBASE_DIR"
    ./mvnw --batch-mode -DskipTests -Ddescriptors=src/main/assembly/dir.xml \
      clean package -P postgres
  )
fi
NESTED_BENCHBASE="$BENCHBASE_DIR/target/benchbase-postgres/benchbase-postgres"
if [[ ! -f "$BENCHBASE_JAR" && -f "$NESTED_BENCHBASE/benchbase.jar" ]]; then
  cp -a "$NESTED_BENCHBASE/." "$(dirname "$BENCHBASE_JAR")/"
  rm -rf "$NESTED_BENCHBASE"
fi
[[ -f "$BENCHBASE_JAR" ]] || {
  echo "SETUP: FAIL: BenchBase build did not produce $BENCHBASE_JAR." >&2
  exit 1
}

if [[ "$MODE" == full ]]; then
  echo 'Installing full benchmark dependency set...'
  apt_install \
    ant autoconf automake bison cmake default-libmysqlclient-dev doxygen fio \
    gengetopt git-lfs graphviz imagemagick libaio-dev libboost-all-dev \
    libbz2-dev libdb5.3++-dev libevent-dev libgdk-pixbuf2.0-dev \
    libgoogle-perftools-dev libgtk2.0-dev libicu-dev libjemalloc-dev \
    libjpeg-dev liblz4-dev liblzma-dev libncurses-dev libnuma-dev libopenexr-dev \
    libpng-dev libreadline-dev libtiff-dev libtool libzmq3-dev memcached \
    libssl-dev openjdk-8-jdk pkg-config scons sox subversion swig tcl-dev tk-dev wget \
    uuid-dev xz-utils zlib1g-dev

  ensure_submodule deps/mutilate README.md
  verify_submodule_commit deps/mutilate d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64

  if [[ ! -x "$REPO_ROOT/deps/mutilate/mutilate" ]]; then
    echo 'Building pinned Mutilate...'
    (
      cd "$REPO_ROOT/deps/mutilate"
      if [[ -f configure.ac || -f configure.in ]]; then
        autoreconf -fi
        ./configure
        make -j"$(nproc)"
      else
        scons -j"$(nproc)"
      fi
    )
  fi
  [[ -x "$REPO_ROOT/deps/mutilate/mutilate" ]] || {
    echo 'SETUP: FAIL: the Mutilate build did not produce deps/mutilate/mutilate.' >&2
    exit 1
  }

  "$SCRIPT_DIR/setup_tailbench.sh"
  "$SCRIPT_DIR/setup_sparkbench.sh" --with-dataset

  cat <<'EOF'
FULL SETUP: software dependencies are installed.
  TailBench Masstree, Silo, Sphinx, and Xapian were built, and their inputs
  were installed under /mydata unless already present.
  SparkBench's pinned dataset and populated warehouse were installed under
  /mydata. Initial population can take up to three hours; later runs reuse it.
EOF
fi

PYTHONPATH="$REPO_ROOT/src:$FUNCTIONAL_DIR" \
  "$VENV/bin/python" "$FUNCTIONAL_DIR/tpcc_tool.py" validate --runtime

echo "SETUP: PASS ($MODE)"
echo "  Python: $($VENV/bin/python --version 2>&1)"
echo "  Sysbench: $(sysbench --version)"
echo "  PostgreSQL: $(psql --version)"
echo "  Java: $(java -version 2>&1 | head -n1)"
echo "  BenchBase: $BENCHBASE_JAR"
echo "  Database environment: $SITE_ENV (mode 600, ignored by Git)"
