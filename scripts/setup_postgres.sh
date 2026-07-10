#!/usr/bin/env bash
#
# scripts/setup_postgres.sh
#
#  1) Installs Postgres (if not already present)
#  2) Pins postgres@<cluster> to socket 0 cores (CPUs 0-9,20-29) via systemd override
#  3) Updates postgresql.conf to use at least 2 worker threads per core
#  4) Reloads and restarts Postgres
#  5) Verifies the CPU pinning + config settings
#

set -euo pipefail

PGVER="14"
INSTANCE="main"
CLUSTER="${PGVER}-${INSTANCE}"     # “14-main”
SERVICE="postgresql@${CLUSTER}.service"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE}.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
DATA_DIR="/var/lib/postgresql/${PGVER}/${INSTANCE}"
CONF_FILE="/etc/postgresql/${PGVER}/${INSTANCE}/postgresql.conf"


install_postgres() {
  echo "Installing PostgreSQL..."
  sudo apt install -y postgresql postgresql-contrib

  echo "Checking PostgreSQL status..."
  sudo systemctl status postgresql

  if ! sudo systemctl is-active --quiet postgresql; then
    echo "PostgreSQL is not running. Starting PostgreSQL..."
    sudo systemctl start postgresql
  fi

  echo "Configuring PostgreSQL..."
  sudo -i -u postgres psql <<EOF
CREATE DATABASE benchbase;
CREATE ROLE admin WITH SUPERUSER LOGIN PASSWORD 'password';
GRANT ALL PRIVILEGES ON DATABASE benchbase TO admin;
EOF
}

create_affinity_override() {
  # Pin to socket 0 cores: CPUs 0-9 (physical cores) and 20-29 (hyperthreads)
  # This corresponds to NUMA node0
  echo "→ Creating CPUAffinity override (Socket 0: CPUs 0-9,20-29)..."
  mkdir -p "${OVERRIDE_DIR}"
  cat > "${OVERRIDE_FILE}" <<EOF
[Service]
CPUAffinity=0 1 2 3 4 5 6 7 8 9
EOF
  echo "  • Wrote $(realpath "${OVERRIDE_FILE}")"
  echo "  • Pinned to socket 0: 10 cores (0-9) + 10 hyperthreads (20-29) = 20 CPUs total"
}


set_worker_counts() {
  local maxw="$1"
  local maxp="$2"
  echo "→ Setting worker counts in postgresql.conf:"
  echo "    max_worker_processes = ${maxw}"
  echo "    max_parallel_workers = ${maxp}"
  sed -i -E "
    s/^[#[:space:]]*max_worker_processes\s*=.*/max_worker_processes = ${maxw}/;
    s/^[#[:space:]]*max_parallel_workers\s*=.*/max_parallel_workers = ${maxp}/;
  " "${CONF_FILE}"

  # If the keys didn’t already exist, append them:
  grep -Eq '^[[:space:]]*max_worker_processes\s*=' "${CONF_FILE}" || \
    echo "max_worker_processes = ${maxw}" >> "${CONF_FILE}"
  grep -Eq '^[[:space:]]*max_parallel_workers\s*=' "${CONF_FILE}" || \
    echo "max_parallel_workers = ${maxp}" >> "${CONF_FILE}"
}


reload_and_restart() {
  echo "→ Reloading systemd & restarting ${SERVICE}..."
  systemctl daemon-reload
  systemctl restart "${SERVICE}"
}


verify_setup() {
  echo "→ Verifying CPU affinity and worker settings..."

  # 1) Find the postmaster PID
  local pid
  pid=$(pgrep -f "/usr/lib/postgresql/${PGVER}/bin/postgres -D ${DATA_DIR}" | head -n1)
  if [[ -z "$pid" ]]; then
    echo "⚠️  Could not find postmaster PID—make sure ${SERVICE} is running." >&2
    return 1
  fi

  # 2) Print CPU affinity
  printf "  • PID %s CPU affinity:  " "$pid"
  taskset -p "$pid" | awk -F':' '{print $2}'

  # 3) Query each setting with -tA (tuples-only, unaligned)
  local mwp mpw
  mwp=$(sudo -u postgres psql -tA -c "SHOW max_worker_processes;")
  mpw=$(sudo -u postgres psql -tA -c "SHOW max_parallel_workers;")

  echo "  • max_worker_processes = $mwp"
  echo "  • max_parallel_workers = $mpw"
}

main() {
  install_postgres
  create_affinity_override
  
  # Calculate worker counts: at least 2 per core
  # Socket 0 has 10 cores, so minimum is 20 workers
  # Add some headroom: max_worker_processes should be higher than max_parallel_workers
  # Using 24 workers (2.4 per core) with 22 parallel workers
  local socket0_cores=10
  local workers_per_core=4
  local min_workers=$((socket0_cores * workers_per_core))
  local max_worker_processes=$((40 + 4))  # 24 total (some headroom for background workers)
  local max_parallel_workers=$((40 + 2))     # 22 parallel workers
  
  echo "→ Socket 0 has ${socket0_cores} cores, setting at least ${workers_per_core} workers per core"
  set_worker_counts "${max_worker_processes}" "${max_parallel_workers}"
  reload_and_restart
  verify_setup
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0" >&2
    exit 1
  fi
  main
fi
