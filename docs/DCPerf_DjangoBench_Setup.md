# DCPerf & DjangoBench Setup Guide

This document summarizes the complete setup process, including initial configuration and specific fixes applied to run **DjangoBench** (and SparkBench) on this environment.

## 1. Initial Setup Steps

These steps were taken to initialize the environment and prepare the benchmarks.

### A. Repository & Dependencies
1.  **Clone DCPerf**: The repository is located at `/mydata/os-param-tuning/deps/DCPerf`.
2.  **Install Python Dependencies**:
    ```bash
    pip install -r deps/DCPerf/requirements.txt
    ```
3.  **Install System Dependencies**:
    *   **Java 8** (Required for Cassandra 3.11): `sudo apt-get install openjdk-8-jdk`
    *   **Git LFS** (Required for Spark dataset): `sudo apt-get install git-lfs && git lfs install`
    *   **Other tools**: `siege`, `maven`, `ant`, `cmake`, `gcc` (as needed by specific benchmarks).

### B. SparkBench Preparation
1.  **Dataset**: The Spark benchmark requires a large dataset.
    *   Ensure `deps/DCPerf/benchmarks/spark_standalone/dataset/` is populated.
    *   If using `git-lfs`, pull the files: `git lfs pull`.
2.  **Wrapper Script**: Created `scripts/run_dcperf.sh` to simplify running `benchpress` commands (since `benchpress_cli.py` might be missing or not in PATH).

### C. DjangoBench Preparation
1.  **Environment**: `deps/DCPerf/benchmarks/django_workload`
    *   Ensured `bin/run.sh` is executable.
    *   Verified `uwsgi` is installed in the virtual environment or system.

---

## 2. Specific Fixes Applied

To get **DjangoBench** running correctly, the following fixes were applied to address runtime failures:

### A. Cassandra Compatibility
*   **Java 8 Enforcement**: Modified `bin/run.sh` to force `JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64` when starting Cassandra, as Cassandra 3.11 fails on newer Java versions.
*   **Data Directories**: Created `/data/cassandra/{data,commitlog,saved_caches,hints}` and ensured they are writable by the current user.
*   **Permissions**: Fixed ownership of `apache-cassandra/conf` and generated config files so they can be written without root.
*   **Pinning**: Used `taskset -c 0-9` (default in script) to pin Cassandra to specific cores.

### B. Siege / Networking Fixes (Fixed "Name or service not known")
*   **Force IPv4 Loopback**: Updated `bin/run.sh` and `client/urls_template.txt` to use `127.0.0.1` instead of `localhost` to bypass potential hostname resolution issues.
*   **URL Rewriting**: Added a failsafe `sed` command in `bin/run.sh` (`run_benchmark` function) that rewrites the generated `urls.txt` to always use `127.0.0.1` immediately before Siege runs.
*   **Clean Environment (Crucial)**: Added `unset LD_PRELOAD http_proxy https_proxy HTTP_PROXY HTTPS_PROXY` in `bin/run.sh` before executing Siege. This prevents environment variables (like those from `benchpress` hooks or system proxies) from interfering with Siege’s local network connections.

### C. Console Output
*   Updated `scripts/run_dcperf.sh` and `benchpress/logging_config.py` to ensure benchmark progress (INFO logs) is printed to the terminal instead of being silenced.

---

## 3. How to Run

### Prerequisite: Fix Permissions
Since `benchpress` or previous manual runs (especially with `sudo`) can leave files owned by root, **always run this permission fix first** if you encounter "Permission denied" errors or after a failed run.

```bash
sudo chown -R "$USER:$USER" /mydata/os-param-tuning/deps/DCPerf/benchmarks/django_workload /data/cassandra
```

### Run DjangoBench (Standalone)
Run the benchmark using the wrapper script from the project root. This runs the Django workload in "standalone" mode (Client + Server + Database on one machine).

```bash
./scripts/run_dcperf.sh run django_workload_default -r standalone
```

*   **Expected Output**:
    *   Django server starts (uWSGI).
    *   Cassandra starts (Java 8).
    *   Siege runs for the configured iterations (default 7).
    *   **Success Indicator**: You will see non-zero `Transaction rate` and `Successful transactions` in the final output (e.g., `Transaction rate: ~2000+ trans/sec`).

### Run SparkBench
To run the Spark standalone benchmark:

```bash
./scripts/run_dcperf.sh run spark_standalone_local
```

---

## 4. Troubleshooting

*   **"Name or service not known"**: This indicates `urls.txt` or the environment is dirty. The fixes in `run.sh` (forcing `127.0.0.1` and unsetting proxies) should prevent this. Check `/tmp/siege_env.log` (created by the debug step in `run.sh`) to see the environment variables if it recurs.
*   **"Permission denied"**: Run the `chown` command listed above.
*   **"Connection refused"**: Usually means Cassandra or Django failed to start. Check `deps/DCPerf/benchmarks/django_workload/bin/nohup.out` or the console logs for Java/Cassandra errors.
