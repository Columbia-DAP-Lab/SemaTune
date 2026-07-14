#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FUNCTIONAL_DIR="$REPO_ROOT/functional_example"
VENV="$REPO_ROOT/.venv-functional"
SITE_ENV="$FUNCTIONAL_DIR/site.env"
MUTILATE_ENV="$FUNCTIONAL_DIR/mutilate.env"
MUTILATE_CONTROL_PORT=19876
MODE=base
MODE_SELECTED=0
SERVER_IP=''
CLIENT_IP=''

usage() {
  cat <<'EOF'
Usage:
  scripts/setup.sh [--base]
  scripts/setup.sh --full [--server-ip IP --client-ip IP]
  scripts/setup.sh --memcached-server --server-ip IP --client-ip IP
  scripts/setup.sh --memcached-client --server-ip IP --client-ip IP

Set up SemaTune on Ubuntu 22.04. The default is --base.

  --base               Install the complete Sysbench Functional environment:
                       the hash-locked Python environment, Sysbench, PostgreSQL
                       and its local database, Java 21, and pinned BenchBase.
  --full               Install --base plus all pinned benchmark submodules and
                       native dependencies. When both network addresses are
                       supplied, also configure this host as the memcached server.
  --memcached-server   From a fresh clone, install --base plus memcached and
                       generate the two-node server configuration.
  --memcached-client   Install only the pinned Mutilate load generator and a
                       restartable client service on the load-generator node.
  --server-ip IP       Internal IPv4 address of the memcached/SemaTune server.
  --client-ip IP       Internal IPv4 address of the Mutilate load-generator node.
  -h, --help

Setup never runs a benchmark. The memcached client service may be installed and
left waiting for a future server-side Functional run.
EOF
}

need_value() {
  local option="$1" value="${2:-}"
  [[ -n "$value" && "$value" != --* ]] || {
    echo "Missing value for $option." >&2
    usage >&2
    exit 2
  }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) ((MODE_SELECTED += 1)); MODE=base; shift ;;
    --full) ((MODE_SELECTED += 1)); MODE=full; shift ;;
    --memcached-server) ((MODE_SELECTED += 1)); MODE=memcached-server; shift ;;
    --memcached-client) ((MODE_SELECTED += 1)); MODE=memcached-client; shift ;;
    --server-ip) need_value "$1" "${2:-}"; SERVER_IP="$2"; shift 2 ;;
    --client-ip) need_value "$1" "${2:-}"; CLIENT_IP="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if (( MODE_SELECTED > 1 )); then
  echo 'Choose exactly one setup mode.' >&2
  usage >&2
  exit 2
fi
if [[ -n "$SERVER_IP" || -n "$CLIENT_IP" ]]; then
  [[ -n "$SERVER_IP" && -n "$CLIENT_IP" ]] || {
    echo '--server-ip and --client-ip must be supplied together.' >&2
    exit 2
  }
fi
case "$MODE" in
  base)
    [[ -z "$SERVER_IP" ]] || {
      echo '--base does not configure Mutilate; use --memcached-server or --full.' >&2
      exit 2
    }
    ;;
  memcached-server|memcached-client)
    [[ -n "$SERVER_IP" ]] || {
      echo "$MODE requires --server-ip and --client-ip." >&2
      exit 2
    }
    ;;
esac

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
SETUP_USER="${SUDO_USER:-$(id -un)}"
[[ "$SETUP_USER" != root || "$(id -u)" -eq 0 ]] || SETUP_USER="$(id -un)"
SETUP_GROUP="$(id -gn "$SETUP_USER")"

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
  # The canonical Zenodo archive is metadata-free and protected by SHA256SUMS.
  if [[ -e "$REPO_ROOT/$relative/.git" ]]; then
    local actual
    actual="$(git -C "$REPO_ROOT/$relative" rev-parse HEAD)"
    [[ "$actual" == "$expected" ]] || {
      echo "SETUP: FAIL: $relative is at $actual, expected $expected." >&2
      exit 1
    }
  fi
}

valid_ipv4() {
  local address="$1" octet
  local -a octets
  [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a octets <<< "$address"
  for octet in "${octets[@]}"; do
    (( 10#$octet <= 255 )) || return 1
  done
}

host_has_ipv4() {
  local expected="$1"
  ip -o -4 address show up | awk '{sub(/\/.*/, "", $4); print $4}' | grep -Fqx "$expected"
}

validate_network_role() {
  local local_ip="$1" peer_ip="$2" role="$3"
  valid_ipv4 "$SERVER_IP" && valid_ipv4 "$CLIENT_IP" || {
    echo 'SETUP: FAIL: --server-ip and --client-ip must be IPv4 addresses.' >&2
    exit 1
  }
  [[ "$SERVER_IP" != "$CLIENT_IP" ]] || {
    echo 'SETUP: FAIL: server and client addresses must differ.' >&2
    exit 1
  }
  host_has_ipv4 "$local_ip" || {
    echo "SETUP: FAIL: $role address $local_ip is not assigned to an active local interface." >&2
    ip -brief -4 address >&2 || true
    exit 1
  }
  ip route get "$peer_ip" >/dev/null 2>&1 || {
    echo "SETUP: FAIL: no route from $local_ip to peer $peer_ip." >&2
    exit 1
  }
}

write_mutilate_env() {
  local role="$1"
  umask 022
  {
    printf 'export SEMATUNE_MUTILATE_ROLE=%q\n' "$role"
    printf 'export SEMATUNE_MUTILATE_SERVER_IP=%q\n' "$SERVER_IP"
    printf 'export SEMATUNE_MUTILATE_CLIENT_IP=%q\n' "$CLIENT_IP"
    printf 'export SEMATUNE_MUTILATE_CONTROL_PORT=%q\n' "$MUTILATE_CONTROL_PORT"
    printf 'export SEMATUNE_MUTILATE_TARGET=%q\n' "$SERVER_IP:11211"
    printf 'export SEMATUNE_MUTILATE_BIN=%q\n' "$REPO_ROOT/deps/mutilate/mutilate"
  } > "$MUTILATE_ENV"
  chmod 644 "$MUTILATE_ENV"
}

install_base() {
  apt_install \
    ca-certificates ethtool git jq linux-tools-common linux-tools-generic numactl \
    openssl pciutils postgresql postgresql-client python3.10 python3.10-venv \
    python3-pip sysbench unzip util-linux build-essential openjdk-21-jdk-headless

  local java21_home=/usr/lib/jvm/java-21-openjdk-amd64
  [[ -x "$java21_home/bin/java" && -x "$java21_home/bin/javac" ]] || {
    echo "SETUP: FAIL: OpenJDK 21 was installed without the expected $java21_home." >&2
    exit 1
  }
  "${ROOT[@]}" update-alternatives --set java "$java21_home/bin/java"
  "${ROOT[@]}" update-alternatives --set javac "$java21_home/bin/javac"

  # linux-tools-generic may be newer than the running kernel after unattended
  # upgrades.  The /usr/bin/perf dispatcher exists in that case but cannot run,
  # so exercise it before deciding that the matching tools are installed.
  if ! perf --version >/dev/null 2>&1; then
    local kernel_tools="linux-tools-$(uname -r)"
    if ! apt_install "$kernel_tools"; then
      echo "SETUP: FAIL: perf is unavailable and $kernel_tools could not be installed." >&2
      exit 1
    fi
    perf --version >/dev/null 2>&1 || {
      echo "SETUP: FAIL: perf remains unusable after installing $kernel_tools." >&2
      exit 1
    }
  fi

  if [[ ! -x "$VENV/bin/python" ]]; then
    python3.10 -m venv "$VENV"
  fi
  "$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes \
    --requirement "$REPO_ROOT/requirements-bootstrap.lock"
  "$VENV/bin/python" -m pip install --disable-pip-version-check --require-hashes \
    --requirement "$REPO_ROOT/requirements.txt"

  "${ROOT[@]}" systemctl enable --now postgresql

  local database_password
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

  local postgres_psql=(runuser -u postgres -- psql)
  if [[ "$(id -u)" -ne 0 ]]; then
    postgres_psql=(sudo -n -u postgres psql)
  fi
  "${postgres_psql[@]}" --quiet --no-psqlrc --set ON_ERROR_STOP=1 \
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
  local benchbase_dir="$REPO_ROOT/deps/benchbase"
  local benchbase_jar="$benchbase_dir/target/benchbase-postgres/benchbase.jar"
  if [[ ! -f "$benchbase_jar" ]]; then
    echo 'Building the pinned BenchBase PostgreSQL distribution...'
    (
      cd "$benchbase_dir"
      export JAVA_HOME="$java21_home"
      export PATH="$JAVA_HOME/bin:$PATH"
      export GIT_CONFIG_COUNT=1
      export GIT_CONFIG_KEY_0=safe.directory
      export GIT_CONFIG_VALUE_0="$benchbase_dir"
      ./mvnw --batch-mode -DskipTests -Ddescriptors=src/main/assembly/dir.xml \
        clean package -P postgres
    )
  fi
  local nested_benchbase="$benchbase_dir/target/benchbase-postgres/benchbase-postgres"
  if [[ ! -f "$benchbase_jar" && -f "$nested_benchbase/benchbase.jar" ]]; then
    cp -a "$nested_benchbase/." "$(dirname "$benchbase_jar")/"
    rm -rf "$nested_benchbase"
  fi
  [[ -f "$benchbase_jar" ]] || {
    echo "SETUP: FAIL: BenchBase build did not produce $benchbase_jar." >&2
    exit 1
  }

  PYTHONPATH="$REPO_ROOT/src:$FUNCTIONAL_DIR" \
    "$VENV/bin/python" "$FUNCTIONAL_DIR/tpcc_tool.py" validate --runtime

  echo 'BASE SETUP: PASS'
  echo "  Python: $($VENV/bin/python --version 2>&1)"
  echo "  Sysbench: $(sysbench --version)"
  echo "  PostgreSQL: $(psql --version)"
  echo "  Java: $(java -version 2>&1 | head -n1)"
  echo "  BenchBase: $benchbase_jar"
  echo "  Database environment: $SITE_ENV (mode 600, ignored by Git)"
}

install_mutilate_build() {
  apt_install ca-certificates build-essential gengetopt git libevent-dev libzmq3-dev \
    python3.10 scons
  ensure_submodule deps/mutilate README.md
  verify_submodule_commit deps/mutilate d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64

  local mutilate_dir="$REPO_ROOT/deps/mutilate"
  local mutilate_bin="$mutilate_dir/mutilate"
  local installed_version=''
  if [[ -x "$mutilate_bin" ]]; then
    installed_version="$("$mutilate_bin" --version 2>/dev/null || true)"
  fi
  if [[ "$installed_version" != 'mutilate 0.1' ]]; then
    rm -f "$mutilate_bin"
    echo 'Building pinned Mutilate...'
    (
      # The pinned SConstruct uses Python 2 print syntax, but Ubuntu 22.04's
      # SCons runs on Python 3.  Translate a disposable copy so the verified
      # submodule remains byte-for-byte at its pinned revision.
      local build_dir
      build_dir="$(mktemp -d)"
      trap 'rm -rf "$build_dir"' EXIT
      cp -a "$mutilate_dir/." "$build_dir/"
      rm -rf "$build_dir/.git"
      python3.10 -m lib2to3 --no-diffs --nobackups --write \
        --fix print "$build_dir/SConstruct" >/dev/null
      scons -C "$build_dir" -j"$(nproc)"
      install -m 0755 "$build_dir/mutilate" "$mutilate_bin"
    )
  fi
  [[ -x "$mutilate_bin" ]] || {
    echo 'SETUP: FAIL: the Mutilate build did not produce deps/mutilate/mutilate.' >&2
    exit 1
  }
  installed_version="$("$mutilate_bin" --version)"
  [[ "$installed_version" == 'mutilate 0.1' ]] || {
    echo "SETUP: FAIL: unexpected Mutilate version: $installed_version" >&2
    exit 1
  }
  echo "$installed_version"
}

configure_memcached_server() {
  validate_network_role "$SERVER_IP" "$CLIENT_IP" server
  apt_install memcached
  "${ROOT[@]}" systemctl disable --now memcached.service >/dev/null 2>&1 || true
  write_mutilate_env server
  echo 'MEMCACHED SERVER SETUP: PASS'
  echo "  Server: $SERVER_IP:11211"
  echo "  Expected client: $CLIENT_IP"
  echo "  Control channel: $SERVER_IP:$MUTILATE_CONTROL_PORT"
  echo "  Runtime environment: $MUTILATE_ENV"
}

configure_memcached_client() {
  validate_network_role "$CLIENT_IP" "$SERVER_IP" client
  install_mutilate_build
  write_mutilate_env client

  local service=/etc/systemd/system/sematune-mutilate-client.service
  local temporary
  temporary="$(mktemp)"
  cat > "$temporary" <<EOF
[Unit]
Description=SemaTune Mutilate load-generator client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SETUP_USER
Group=$SETUP_GROUP
WorkingDirectory=$REPO_ROOT
ExecStart=$REPO_ROOT/scripts/run_mutilate_client.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
  "${ROOT[@]}" install -o root -g root -m 0644 "$temporary" "$service"
  rm -f "$temporary"
  "${ROOT[@]}" systemctl daemon-reload
  "${ROOT[@]}" systemctl enable sematune-mutilate-client.service >/dev/null
  "${ROOT[@]}" systemctl restart sematune-mutilate-client.service
  "${ROOT[@]}" systemctl is-active --quiet sematune-mutilate-client.service || {
    "${ROOT[@]}" systemctl status --no-pager sematune-mutilate-client.service >&2 || true
    echo 'SETUP: FAIL: the Mutilate client service did not remain active.' >&2
    exit 1
  }
  echo 'MEMCACHED CLIENT SETUP: PASS'
  echo "  Client source address: $CLIENT_IP"
  echo "  Server control address: $SERVER_IP:$MUTILATE_CONTROL_PORT"
  echo "  Mutilate: $REPO_ROOT/deps/mutilate/mutilate"
  echo '  Service: sematune-mutilate-client.service (active; reconnects automatically)'
}

install_full() {
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

  install_mutilate_build
  "$SCRIPT_DIR/setup_tailbench.sh"
  "$SCRIPT_DIR/setup_sparkbench.sh" --with-dataset

  cat <<'EOF'
FULL SETUP: software dependencies are installed.
  TailBench Masstree, Silo, Sphinx, and Xapian were built, and their inputs
  were installed under /mydata unless already present.
  SparkBench's pinned dataset and populated warehouse were installed under
  /mydata. Initial population can take up to three hours; later runs reuse it.
EOF
}

# Fail allocation mistakes before apt, builds, or service changes. The role
# helpers repeat this check immediately before writing their configuration.
case "$MODE" in
  memcached-server) validate_network_role "$SERVER_IP" "$CLIENT_IP" server ;;
  memcached-client) validate_network_role "$CLIENT_IP" "$SERVER_IP" client ;;
  full) [[ -z "$SERVER_IP" ]] || validate_network_role "$SERVER_IP" "$CLIENT_IP" server ;;
esac

echo "Setting up SemaTune ($MODE)..."
"${ROOT[@]}" apt-get update

case "$MODE" in
  base)
    install_base
    ;;
  full)
    install_base
    install_full
    if [[ -n "$SERVER_IP" ]]; then
      configure_memcached_server
    else
      echo 'FULL SETUP NOTE: supply --server-ip and --client-ip to generate the two-node server configuration.'
    fi
    ;;
  memcached-server)
    install_base
    configure_memcached_server
    ;;
  memcached-client)
    configure_memcached_client
    ;;
esac

echo "SETUP: PASS ($MODE)"
