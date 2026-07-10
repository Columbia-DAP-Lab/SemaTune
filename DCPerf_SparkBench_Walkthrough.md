# DCPerf SparkBench Walkthrough

This guide documents how to set up, run, and customize the SparkBench benchmark within the DCPerf suite.

## 1. Prerequisites

Ensure the following are installed on the system (requires root):
- Java 8+ (`openjdk-8-jdk` or similar)
- `git-lfs`
- Python 3
- `fio` (optional, for I/O sanity checks)

```bash
sudo apt-get update
sudo apt-get install -y openjdk-8-jdk git-lfs fio python3
git lfs install
```

## 2. Installation

Navigate to the DCPerf directory:

```bash
cd /mydata/os-param-tuning/deps/DCPerf
```

Install the SparkBench benchmark (local mode). This sets up Spark and directories but fails to download the dataset automatically in some environments.

```bash
sudo ./benchpress_cli.py install spark_standalone_local
```

## 3. Manual Dataset Setup (Required)

The automatic download often fails (missing `manifold` tool). Perform these steps manually:

```bash
# Go to the dataset directory
cd benchmarks/spark_standalone/dataset

# Remove any failed/partial download
sudo rm -rf bpc_t93586_s2_synthetic

# Clone the dataset repository using git-lfs
git clone https://github.com/facebookresearch/DCPerf-datasets

# Move the specific dataset to the expected location
mv DCPerf-datasets/bpc_t93586_s2_synthetic .

# Cleanup
rm -rf DCPerf-datasets
```

## 4. Customizing Execution (Optional)

### Force Specific Thread Count
By default, Spark auto-detects core count. To force a specific number of threads (e.g., 10 threads or oversubscribing to 40), modify `packages/spark_standalone/templates/runner.py`.

Edit `packages/spark_standalone/templates/runner.py` in the `run_test` function:

```python
def run_test(args):
    # ...
    cmd_list = [
        "../scripts/run_perf_common.py",
        "exp",
        # ... existing args ...
        "--worker-cores", "10"  # <--- ADD THIS LINE (e.g. 10 or 40)
    ]
```

## 5. Running the Benchmark

Run the benchmark using `benchpress_cli.py`.

**Standard Local Run:**
```bash
cd /mydata/os-param-tuning/deps/DCPerf
sudo ./benchpress_cli.py run spark_standalone_local
```

**Pinned Run (e.g., cores 0-9):**
Use `taskset` to restrict the process and its children to specific cores.

```bash
sudo taskset -c 0-9 ./benchpress_cli.py run spark_standalone_local
```

## 6. Results and Parsing

### Output
- **Console:** A JSON summary is printed at the end of the run.
- **Metrics:**
  - `queries_per_hour`: Primary throughput metric (higher is better).
  - `execution_time_test_...`: Latency for the query (lower is better).
  - `score`: Normalized performance score.

### File Locations
- **Raw Results:** `benchmarks/spark_standalone/work/results.txt` (temporary).
- **Archived Results:** Moved to `benchmark_metrics_<run_id>/work/` after the run.
- **Spark Logs:** Located in the same `work` directory (e.g., `release_test_93586.log`).

### Parsing Logic
- **Parser:** `benchpress/plugins/parsers/spark_standalone.py`
  - Parses `stdout` (which includes content of `results.txt`).
  - Extracts lines like `queries-per-hour : <val>` and `test-release_test_<id> : <val>`.

- **Workload Script:** `packages/spark_standalone/templates/proj_root/scripts/run_perf_common.py`
  - Defines the SQL workload and writes the `results.txt` format.
