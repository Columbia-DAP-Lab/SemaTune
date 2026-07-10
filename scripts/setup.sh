#!/bin/bash

set -euo pipefail


PROJECT_TOP="$(git rev-parse --show-toplevel)"

# BenchBase directory path
BENCHBASE_DIR="$PROJECT_TOP/deps/benchbase"  # Replace this with the actual path
RESULTS_DIR="$PROJECT_TOP/results"

git submodule update --init --recursive

# Set up Java 21
setup_java() {
  echo "Updating package list and installing Java 21..."
  sudo apt update
  sudo apt install -y openjdk-21-jdk

  echo "Configuring default Java version..."
  sudo update-alternatives --config java
  
  # Get JAVA_HOME path
  JAVA_PATH=$(dirname $(dirname $(readlink -f $(which java))))

  echo "Setting JAVA_HOME..."
  sudo sed -i '/JAVA_HOME/d' /etc/environment
  echo "JAVA_HOME=\"$JAVA_PATH\"" | sudo tee -a /etc/environment
  
  # Apply the changes
  source /etc/environment

  # Verify Java setup
  echo "JAVA_HOME is set to: $JAVA_HOME"
  java -version
}

# Set up CockroachDB
setup_cockroachdb() {
  echo "Installing CockroachDB..."

  # Download and install CockroachDB
  sudo apt-get install -y apt-transport-https ca-certificates curl
  curl https://binaries.cockroachdb.com/cockroach-v21.2.10.linux-amd64.tgz | tar -xz
  sudo cp -i cockroach-v21.2.10.linux-amd64/cockroach /usr/local/bin/

  # Create directories for CockroachDB data and log files
  sudo mkdir -p /var/lib/cockroach
  sudo mkdir -p /var/log/cockroach

  # Start CockroachDB in secure mode
  sudo cockroach start-single-node --insecure --store=/var/lib/cockroach --listen-addr=localhost:26257 --http-addr=localhost:8080 --background

  # Set up CockroachDB user and database
  echo "Setting up CockroachDB root user and benchbase database..."
  cockroach sql --insecure --execute="CREATE DATABASE benchbase;"

}

# Set up perf
setup_perf() {
    echo "Installing perf..."
    sudo apt install -y linux-tools-common linux-tools-generic linux-tools-$(uname -r)

    # Modify privileges for perf to avoid security warnings
    echo -1 | sudo tee /proc/sys/kernel/perf_event_paranoid
    sudo sh -c 'echo "kernel.perf_event_paranoid=-1" >> /etc/sysctl.conf'

    # Disable nmi_watchdog
    sudo sh -c 'echo 0 > /proc/sys/kernel/nmi_watchdog'
    
    sudo sysctl -p
}

# Set up sysbench
setup_sysbench() {
    echo "Installing sysbench..."
    sudo apt install -y sysbench
    
    echo "Verifying sysbench installation..."
    sysbench --version
    
    echo "Setting up sysbench database..."
    # Create benchdb database if it doesn't exist
    sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname='benchdb'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE benchdb;"
    
    # Grant permissions to admin user
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE benchdb TO admin;"
    
    echo "Sysbench setup completed"
}

# Set up BenchBase
setup_benchbase_postgres() {

  cd "$BENCHBASE_DIR"
  
  # Build BenchBase for PostgreSQL
  export BENCHBASE_PROFILE=postgres
  ./mvnw clean package -P postgres -DskipTests

  # Extract BenchBase PostgreSQL package
  cd "$BENCHBASE_DIR/target"
  tar xvzf benchbase-postgres.tgz
}

# Set up and run BenchBase with CockroachDB
setup_benchbase_cockroachdb() {
  
  cd "$BENCHBASE_DIR"
  
  # Build BenchBase for CockroachDB
  ./mvnw clean package -P cockroachdb

  # Extract BenchBase CockroachDB package
  cd "$BENCHBASE_DIR/target"
  tar xvzf benchbase-cockroachdb.tgz

}

enable_hr_tick() {
    echo "Checking if HRTICK option is enabled..."
    grep CONFIG_HIGH_RES_TIMERS /boot/config-$(uname -r)
    grep CONFIG_SCHED_HRTICK   /boot/config-$(uname -r)
    
    echo "Enabling HRTICK..."
    sudo sh -c 'echo HRTICK > /sys/kernel/debug/sched/features'
}

# Set up Tailbench
setup_tailbench() {
    echo "Setting up Tailbench..."
    cd "$PROJECT_TOP/deps/Tailbench"
    # Ensure it is executable
    chmod +x tailbench-setup.sh
    ./tailbench-setup.sh
}

# ---------------------
# Main script execution
# ---------------------

echo "Starting setup process..."

echo "Installing dependencies..."
sudo apt update
sudo apt install -y python3-pip
python3 -m pip install --upgrade --require-hashes \
  -r "$PROJECT_TOP/requirements-bootstrap.lock"
python3 -m pip install --no-build-isolation --require-hashes \
  -r "$PROJECT_TOP/requirements.txt"

setup_java

setup_perf

# setup_postgresql
$PROJECT_TOP/scripts/setup_postgres.sh

# setup sysbench binaries and DB for benchmarks
setup_sysbench
$PROJECT_TOP/scripts/setup_sysbench_db.sh

# setup_cockroachdb

setup_benchbase_postgres
# setup_benchbase_cockroachdb

enable_hr_tick

setup_tailbench

# Capture baseline values for new tunable parameters (for post-run restoration checks).
echo "Capturing new-parameter baseline snapshot..."
python3 "$PROJECT_TOP/scripts/verify_parameter_roundtrip.py" snapshot \
  --output "$PROJECT_TOP/results/new_params_baseline_setup.json" || \
  echo "Warning: failed to capture new-parameter baseline snapshot"

echo "Setup complete!"
