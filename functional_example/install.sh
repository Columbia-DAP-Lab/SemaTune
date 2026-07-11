#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/.venv-functional"
SITE_ENV="$SCRIPT_DIR/site.env"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "INSTALL: FAIL: the live Functional example requires Linux bare metal." >&2
  exit 1
fi

ROOT=()
if [[ "$(id -u)" -ne 0 ]]; then
  if ! sudo -n true >/dev/null 2>&1; then
    echo "INSTALL: FAIL: root or non-interactive sudo is required for OS packages and PostgreSQL setup." >&2
    exit 1
  fi
  ROOT=(sudo -n)
fi

echo "Installing the minimal OS dependency set..."
"${ROOT[@]}" apt-get update
"${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3.10 python3.10-venv python3-pip \
  sysbench postgresql postgresql-client \
  linux-tools-common linux-tools-generic \
  util-linux numactl jq pciutils ethtool openssl ca-certificates

if ! command -v perf >/dev/null 2>&1; then
  kernel_tools="linux-tools-$(uname -r)"
  if ! "${ROOT[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$kernel_tools"; then
    echo "INSTALL: FAIL: perf is unavailable and $kernel_tools could not be installed." >&2
    exit 1
  fi
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  python3.10 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade \
  'pip==26.0.1' 'setuptools==82.0.1' 'wheel==0.46.3' 'packaging==26.0'
"$VENV/bin/python" -m pip install --disable-pip-version-check --requirement "$SCRIPT_DIR/requirements.txt"
echo "Installing the pinned classical-tuner closure..."
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

PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR" "$VENV/bin/python" "$SCRIPT_DIR/tpcc_tool.py" validate --runtime
PYTHONPATH="$REPO_ROOT/src:$SCRIPT_DIR" "$VENV/bin/python" "$SCRIPT_DIR/tpcc_tool.py" preflight --live

echo "INSTALL: PASS"
echo "  Python: $($VENV/bin/python --version 2>&1)"
echo "  Sysbench: $(sysbench --version)"
echo "  PostgreSQL: $(psql --version)"
echo "  Local database environment: $SITE_ENV (mode 600, ignored by git)"
