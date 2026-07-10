# Sysbench CPU Benchmark - Exact Commands Executed

This document shows the exact commands that are executed in each iteration when running the sysbench CPU benchmark configurations.

## Configuration Files Created

### Min Granularity Tuning (Minimize P95 Latency)
1. **sysbench_cpu_1param_min_granularity_fixed.json** - Fixed tuner, minimize p95 latency
2. **sysbench_cpu_1param_min_granularity_mlos.json** - MLOS tuner, minimize p95 latency
3. **sysbench_cpu_1param_min_granularity_llm.json** - LLM tuner, minimize p95 latency

### Power Minimization with Latency Constraint
4. **sysbench_cpu_power_min_latency_constraint_fixed.json** - Fixed tuner, minimize power with latency < 400ms constraint
5. **sysbench_cpu_power_min_latency_constraint_llm.json** - LLM tuner, minimize power with latency < 400ms constraint
6. **sysbench_cpu_power_min_latency_constraint.json** - MLOS tuner, minimize power with latency < 400ms constraint

## Command Structure

### Base Sysbench CPU Command

For each iteration/window, the following command is executed:

```bash
sysbench cpu --threads=32 --time=10 --cpu-max-prime=50000 run
```

### With CPU Pinning (pin_to_cores="0-1")

Since all configs have `"pin_to_cores": "0-1"`, the actual command executed is:

```bash
taskset -c 0-1 sysbench cpu --threads=32 --time=10 --cpu-max-prime=50000 run
```

## Command Breakdown

- **`taskset -c 0-1`**: Pins the process to CPU cores 0 and 1
- **`sysbench cpu`**: Runs the sysbench CPU benchmark
- **`--threads=32`**: Uses 32 threads (from `sysbench_threads` config)
- **`--time=10`**: Runs for 10 seconds (from `window_duration` config)
- **`--cpu-max-prime=50000`**: Maximum prime number for CPU test (from `sysbench_cpu_max_prime` config)
- **`run`**: Execute the benchmark

## Per Iteration Execution

For each iteration (window), the following happens:

1. **Parameter Application**: OS parameters are set (e.g., `min_granularity_ns` via `/sys/kernel/debug/sched/min_granularity_ns`)
2. **System Metrics Collection Starts**: Background thread starts collecting power, CPU utilization, and C-state metrics
3. **Perf Metrics Collection Starts**: `perf stat` command runs in parallel
4. **Benchmark Execution**: The sysbench command above is executed
5. **Metrics Parsing**: Results are parsed from sysbench output
6. **System Metrics Collection Stops**: Background thread stops and averages metrics
7. **Reward Calculation**: Based on `optimization_metric` (e.g., `latency_p95` or `power_socket0_watts`)
8. **Tuner Called**: Tuner suggests next parameter values for next iteration

## Example: Full Command Sequence for One Iteration

```bash
# 1. Set scheduler parameter (example: min_granularity_ns=1000000)
echo 1000000 | sudo tee /sys/kernel/debug/sched/min_granularity_ns

# 2. Start system metrics collection (background thread)
# Reads RAPL energy counters, /proc/stat, C-state residency every 1 second

# 3. Start perf stat collection (background process)
perf stat -e cycles,instructions,cache-references,cache-misses,branch-misses,page-faults \
  -o /path/to/results/window_N/perf_stat.log \
  -x, -- sleep 10

# 4. Execute sysbench benchmark
taskset -c 0-1 sysbench cpu --threads=32 --time=10 --cpu-max-prime=50000 run

# 5. Parse results and calculate reward
# - latency_p95 from sysbench output
# - power_socket0_watts from RAPL counters
# - Apply constraint penalty if latency_p95 >= 400.0 (for power minimization config)
```

## Running the Configurations

### Fixed Tuner (Minimize P95 Latency)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_1param_min_granularity_fixed.json
```

### MLOS Tuner (Minimize P95 Latency)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_1param_min_granularity_mlos.json
```

### LLM Tuner (Minimize P95 Latency)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_1param_min_granularity_llm.json
```

### Fixed Tuner (Minimize Power, Latency < 400ms)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_power_min_latency_constraint_fixed.json
```

### LLM Tuner (Minimize Power, Latency < 400ms)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_power_min_latency_constraint_llm.json
```

### MLOS Tuner (Minimize Power, Latency < 400ms)
```bash
sudo python3 src/barebones_optimizer/main.py --config config/sysbench_cpu_power_min_latency_constraint.json
```

## Notes

- All commands require `sudo` because they modify kernel scheduler parameters
- The `window_duration` (10 seconds) determines how long each iteration runs
- The `max_iterations` (60) determines how many iterations to run
- System metrics (power, CPU, C-states) are collected automatically in the background
- Perf metrics are collected automatically if `use_perf_stat` is enabled

