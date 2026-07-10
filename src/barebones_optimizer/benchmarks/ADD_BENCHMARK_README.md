# How to Add a New Benchmark to the OS Parameter Tuner

This guide explains step-by-step how to add a new benchmark to the tuner framework.

## Overview

The tuner framework uses a modular benchmark interface (`BenchmarkInterface`) that all benchmarks must implement. This interface ensures:
- Consistent metric collection (perf stat, power, C-state, CPU utilization)
- Standardized metric format (`BenchmarkMetrics` dataclass)
- Easy integration with the optimizer
- Automatic CPU pinning support
- Automatic system metrics collection

## Step-by-Step Guide

### Step 1: Understand the Benchmark Interface

All benchmarks must inherit from `BenchmarkInterface` and implement three abstract methods:

1. **`pre_execute()`** - Setup phase (e.g., load data, start services)
2. **`execute_window(window_number, duration)`** - Run benchmark for a measurement window
3. **`parse_results(output_dir)`** - Parse benchmark-specific metrics from output files

Additionally, benchmarks inherit automatic support for:
- **Perf stat collection** - Call `self.collect_perf_metrics(window_number, duration)` in `execute_window`
- **System metrics collection** - Call `self.start_system_measurement(window_number, duration)` and `self._populate_system_metrics()` in `execute_window`
- **CPU pinning** - Use `self._wrap_with_taskset(cmd)` to wrap commands

### Step 2: Create Your Benchmark Class

Create a new file `src/barebones_optimizer/your_benchmark.py`:

```python
#!/usr/bin/env python3
"""
Your benchmark implementation for the simplified OS tuner.
"""

import os
import subprocess
import time
import logging
from typing import Dict, Any

from .benchmark import BenchmarkInterface, BenchmarkMetrics

logger = logging.getLogger(__name__)


class YourBenchmark(BenchmarkInterface):
    """Your benchmark implementation."""
    
    def __init__(self, config):
        """Initialize benchmark with configuration.
        
        Args:
            config: Configuration object (SimpleConfig)
        """
        super().__init__(config)
        
        # Extract benchmark-specific config values
        self.your_config_param = getattr(config, 'your_config_param', 'default_value')
        
        # Setup state tracking
        self.setup_done = False
        
        # Create output directory for this benchmark
        self.window_output_dir = os.path.join(self.results_dir, "your_benchmark_windows")
        os.makedirs(self.window_output_dir, exist_ok=True)
    
    def pre_execute(self) -> bool:
        """Run pre-execution setup.
        
        This method is called once before the first window execution.
        Use it for:
        - Loading data into database
        - Starting services
        - Creating test files
        - Any one-time setup
        
        Returns:
            True if setup succeeded, False otherwise
        """
        if self.setup_done:
            return True
        
        logger.info("Running your benchmark setup...")
        
        setup_cmd = ["your_tool", "setup", "--option", self.your_config_param]
        setup_cmd = self._wrap_with_taskset(setup_cmd)
        
        subprocess.run(setup_cmd, check=True)
        self.setup_done = True
        return True
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a measurement window.
        
        This is the core method that runs your benchmark for the specified duration
        and returns the metrics. It MUST:
        1. Start system metrics collection
        2. Start perf stat collection
        3. Run the benchmark
        4. Parse results
        5. Populate system metrics
        
        Args:
            window_number: Current iteration/window number (1-indexed)
            duration: Duration of the measurement window in seconds
            
        Returns:
            BenchmarkMetrics with all collected metrics
        """
        logger.info(f"Executing your benchmark window {window_number} for {duration}s")
        
        # 1. Start system metrics collection (power, C-state, CPU utilization)
        # This starts a background thread that collects metrics every 1 second
        # Collection starts 1s after window start and ends 1s before window end
        # Returns the window start timestamp
        window_start_time = self.start_system_measurement(window_number, duration)
        
        # 2. Start perf stat measurement
        # This runs perf stat for (duration-2) seconds, starting 1s after window start
        # Results are automatically saved to a file and parsed
        self.collect_perf_metrics(window_number, duration)
        
        # 3. Run your benchmark
        output_file = os.path.join(self.window_output_dir, f"window_{window_number}.txt")
        
        cmd = [
            "your_tool",
            "run",
            "--duration", str(duration),
            "--output", output_file,
            "--option", self.your_config_param
        ]
        
        cmd = self._wrap_with_taskset(cmd)
        
        logger.info(f"Running: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        process.wait()
        window_end_time = time.time()
        
        if process.returncode != 0:
            raise RuntimeError(f"Benchmark window {window_number} failed with exit code {process.returncode}")
        
        # 4. Parse benchmark-specific results
        metrics = self.parse_results(window_dir)
        
        # 5. Populate system metrics (perf, power, C-state, CPU)
        # This reads the saved perf metrics file and system metrics file,
        # and populates the BenchmarkMetrics object
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        return metrics
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse benchmark-specific results from output files.
        
        This method should:
        - Read output files from the benchmark execution
        - Extract performance metrics (throughput, latency, etc.)
        - Return a BenchmarkMetrics object
        
        Note: System metrics (perf, power, C-state, CPU) are handled automatically
        by _populate_system_metrics() - you don't need to parse them here.
        
        Args:
            output_dir: Directory containing benchmark output files
            
        Returns:
            BenchmarkMetrics with benchmark-specific metrics
        """
        output_file = os.path.join(output_dir, f"window_{window_number}.txt")
        
        if not os.path.exists(output_file):
            raise FileNotFoundError(f"Output file not found: {output_file}")
        
        with open(output_file, 'r') as f:
            content = f.read()
        
        # Parse your benchmark's output format
        import re
        throughput_match = re.search(r'throughput[:\s]+([\d.]+)', content, re.IGNORECASE)
        if not throughput_match:
            raise ValueError(f"Could not parse throughput from output file")
        throughput = float(throughput_match.group(1))
        
        latency_match = re.search(r'avg[:\s]+latency[:\s]+([\d.]+)', content, re.IGNORECASE)
        if not latency_match:
            raise ValueError(f"Could not parse latency from output file")
        latency_avg = float(latency_match.group(1))
        
        metrics = BenchmarkMetrics(
            throughput=throughput,
            goodput=throughput,
            latency_avg=latency_avg,
            latency_p95=0.0,  # Set if available
            extra_metrics={}
        )
        
        logger.info(f"Parsed metrics: throughput={throughput:.2f}, latency_avg={latency_avg:.2f}ms")
        return metrics
    
    def cleanup(self) -> None:
        """Cleanup benchmark resources (optional).
        
        This method is called after optimization completes (or on interruption).
        Use it for:
        - Stopping services
        - Cleaning up temporary files
        - Resetting system state
        
        Note: This is optional - only implement if needed.
        """
        logger.info("Cleaning up your benchmark...")
        # Your cleanup logic here
        self.setup_done = False
```

### Step 3: Add Configuration Fields

Add benchmark-specific configuration fields to `SimpleConfig` in `src/barebones_optimizer/config.py`:

```python
# Your benchmark settings
your_benchmark_param1: str = "default_value"
your_benchmark_param2: int = 100
your_benchmark_bin_path: str = "/usr/bin/your_tool"
```

### Step 4: Register the Benchmark

Add your benchmark to the registry in `src/barebones_optimizer/benchmark_registry.py`:

```python
# Your benchmark
YOUR_BENCHMARK = BenchmarkInfo(
    name="your_benchmark",
    script="",  # Not applicable if no script
    requires_setup=True,  # True if pre_execute() does setup
    requires_cleanup=False,  # True if cleanup() is needed
    base_command=["your_tool"],  # Base command parts
    description="Your benchmark description"
)
```

Add it to the `BenchmarkType` enum:

```python
YOUR_BENCHMARK = BenchmarkInfo(...)
```

### Step 5: Add to Benchmark Factory

Update `create_benchmark()` in `src/barebones_optimizer/main.py`:

```python
from .your_benchmark import YourBenchmark

def create_benchmark(config: SimpleConfig):
    # ... existing code ...
    elif benchmark_name == "your_benchmark":
        return YourBenchmark(config)
    # ... rest of code ...
```

Also update `src/barebones_optimizer/dual_loop_main.py` if needed.

### Step 6: Test Your Benchmark

Create a test configuration file `config/test_your_benchmark.json`:

```json
{
  "benchmark": "your_benchmark",
  "your_benchmark_param1": "value",
  "your_benchmark_param2": 100,
  "pin_to_cores": null,
  "max_iterations": 3,
  "window_duration": 10,
  "tuner_type": "fixed",
  "parameter_ranges": {
    "min_granularity_ns": [100000, 50000000]
  },
  "fixed_parameters": {
    "latency_ns": 24000000,
    "wakeup_granularity_ns": 4000000,
    "min_granularity_ns": 3000000
  },
  "optimization_metric": "throughput",
  "optimization_goal": "maximize",
  "results_dir": "results/test"
}
```

Run it:

```bash
python3 src/barebones_optimizer/main.py -c config/test_your_benchmark.json
```

## Key Concepts

### BenchmarkMetrics Dataclass

Your `parse_results()` method should return a `BenchmarkMetrics` object with:

```python
BenchmarkMetrics(
    throughput=1000.0,      # Requests/transactions per second
    goodput=950.0,          # Successful requests per second
    latency_avg=5.2,        # Average latency in milliseconds
    latency_p95=10.5,       # 95th percentile latency in milliseconds
    extra_metrics={          # Additional benchmark-specific metrics
        "custom_metric": 123.0
    }
)
```

### System Metrics (Automatic)

The following metrics are **automatically collected** by the base class:
- **Perf stat**: Cycles, instructions, IPC, cache misses, branch misses, page faults
- **Power**: RAPL socket power (Watts), RAPL DRAM power (Watts)
- **C-state**: POLL, C1, C1E, C6 residency percentages
- **CPU utilization**: Load percentage for focused cores and socket 0

You don't need to collect these manually - just call:
1. `self.start_system_measurement(window_number, duration)` at window start
2. `self.collect_perf_metrics(window_number, duration)` at window start
3. `self._populate_system_metrics(metrics, window_number, start, end)` after parsing

### CPU Pinning

If `pin_to_cores` is set in config (e.g., `"0-4"`), automatically wrap commands:

```python
cmd = self._wrap_with_taskset(cmd)
```

This will add `taskset -c <cores>` if pinning is enabled.

### Perf Stat Collection

The `collect_perf_metrics()` method:
- Starts perf stat 1 second after window start
- Ends perf stat 1 second before window end
- Monitors cores globally (not specific PIDs)
- Parses output automatically
- Saves results to `window_{N}_perf_info.json`

You just need to call it - no manual handling required.

## Example: Adding a Simple Benchmark

Let's add a simple CPU-intensive benchmark:

### 1. Create `src/barebones_optimizer/stress_benchmark.py`:

```python
#!/usr/bin/env python3
"""Simple stress benchmark implementation."""

import os
import subprocess
import time
import logging
import re
from .benchmark import BenchmarkInterface, BenchmarkMetrics

logger = logging.getLogger(__name__)


class StressBenchmark(BenchmarkInterface):
    """Simple CPU stress benchmark."""
    
    def __init__(self, config):
        super().__init__(config)
        self.threads = getattr(config, 'stress_threads', 4)
        self.setup_done = False
    
    def pre_execute(self) -> bool:
        """No setup needed for stress test."""
        self.setup_done = True
        return True
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        logger.info(f"Running stress test window {window_number} for {duration}s")
        
        window_start_time = self.start_system_measurement(window_number, duration)
        self.collect_perf_metrics(window_number, duration)
        
        # Run stress-ng
        cmd = ["stress-ng", "--cpu", str(self.threads), "--timeout", str(duration)]
        cmd = self._wrap_with_taskset(cmd)
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        process.wait(timeout=duration + 10)
        window_end_time = time.time()
        
        # Parse results (stress-ng outputs summary at end)
        metrics = self.parse_results(None)  # No output file for stress-ng
        
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        return metrics
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Stress-ng doesn't output detailed metrics, so we use a simple metric."""
        # For stress benchmarks, we might use system metrics as primary metrics
        # Or calculate a simple metric based on CPU utilization
        return BenchmarkMetrics(
            throughput=1.0,  # Placeholder
            goodput=1.0,
            latency_avg=0.0,
            latency_p95=0.0,
            extra_metrics={"stress_benchmark": True}
        )
```

### 2. Add to registry:

```python
STRESS = BenchmarkInfo(
    name="stress",
    script="",
    requires_setup=False,
    requires_cleanup=False,
    base_command=["stress-ng"],
    description="CPU stress benchmark"
)
```

### 3. Add config field:

```python
stress_threads: int = 4
```

### 4. Add to factory:

```python
elif benchmark_name == "stress":
    return StressBenchmark(config)
```

## Continuous vs Restarting Benchmarks

### Restarting Benchmark (Default)

Each window restarts the benchmark:

```python
def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
    # Start system metrics
    actual_window_start = self.start_system_measurement(window_number, duration)
    self.collect_perf_metrics(window_number, duration)
    
    # Run benchmark for duration
    cmd = ["benchmark", "--duration", str(duration)]
    process = subprocess.run(cmd, ...)
    
    # Parse results
    metrics = self.parse_results(...)
    
    # Populate system metrics
    self._populate_system_metrics(...)
    return metrics
```

### Continuous Benchmark

Benchmark runs continuously, you parse checkpoints at window boundaries:

```python
def __init__(self, config):
    super().__init__(config)
    self.continuous_process = None

def pre_execute(self) -> bool:
    # Start benchmark once
    self.continuous_process = subprocess.Popen(["benchmark", "--continuous"])
    return True

def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
    # Benchmark is already running
    # Just parse results for this window
    actual_window_start = self.start_system_measurement(window_number, duration)
    self.collect_perf_metrics(window_number, duration)
    
    # Wait for window duration
    time.sleep(duration)
    
    # Parse checkpoint results
    metrics = self.parse_results(...)
    
    self._populate_system_metrics(...)
    return metrics

def cleanup(self) -> None:
    if self.continuous_process:
        self.continuous_process.terminate()
```

See `SysbenchContinuousBenchmark` for a full example.

## Best Practices

1. **Fail Fast**: Don't catch exceptions unnecessarily - let them propagate to fail fast and clearly
2. **Logging**: Use `logger.info()` for important events, errors will be raised as exceptions
3. **Resource Cleanup**: Implement `cleanup()` if your benchmark starts long-running processes
4. **Path Resolution**: Use `self.repo_root` for resolving relative paths
5. **Output Directory**: Create benchmark-specific subdirectories in `self.results_dir`
6. **CPU Pinning**: Always use `self._wrap_with_taskset(cmd)` for commands
7. **Metric Parsing**: Use direct dictionary access (e.g., `checkpoint["key"]`) - if keys are missing, let it fail rather than returning empty metrics

## Common Patterns

### Pattern 1: Command-Line Tool Benchmark

```python
def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
    actual_window_start = self.start_system_measurement(window_number, duration)
    self.collect_perf_metrics(window_number, duration)
    
    output_file = os.path.join(self.window_output_dir, f"window_{window_number}.txt")
    cmd = ["tool", "--duration", str(duration), "--output", output_file]
    cmd = self._wrap_with_taskset(cmd)
    
    process = subprocess.run(cmd, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"Benchmark failed with exit code {process.returncode}")
    
    window_end_time = time.time()
    metrics = self.parse_results(output_file)
    self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
    return metrics
```

### Pattern 2: Service-Based Benchmark

```python
def pre_execute(self) -> bool:
    subprocess.run(["service", "start"], check=True)
    time.sleep(2)
    return True

def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
    window_start_time = self.start_system_measurement(window_number, duration)
    self.collect_perf_metrics(window_number, duration)
    
    cmd = ["load_generator", "--duration", str(duration)]
    cmd = self._wrap_with_taskset(cmd)
    subprocess.run(cmd, check=True)
    
    window_end_time = time.time()
    metrics = self.parse_results(...)
    self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
    return metrics

def cleanup(self) -> None:
    subprocess.run(["service", "stop"], check=False)
```

### Pattern 3: File-Based Benchmark

```python
def pre_execute(self) -> bool:
    # Create test files
    test_file = os.path.join(self.results_dir, "test_data.bin")
    subprocess.run(["dd", "if=/dev/zero", f"of={test_file}", "bs=1M", "count=1000"], check=True)
    return True

def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
    # Benchmark reads/writes files
    # ... implementation ...
```

## Troubleshooting

### Benchmark doesn't start
- Check if command path is correct
- Verify permissions (may need sudo for some operations)
- Check if required services are running

### Metrics are always zero
- Verify output file is being written
- Check if parse_results() is correctly extracting values
- Add debug logging to see what's being parsed

### System metrics are missing
- Ensure `_populate_system_metrics()` is called after `parse_results()`
- Check that `start_system_measurement()` was called at window start
- Verify `collect_perf_metrics()` was called

### CPU pinning not working
- Verify `pin_to_cores` is set in config
- Check that `_wrap_with_taskset()` is used on all commands
- Ensure taskset is available: `which taskset`

## Reference Implementations

- **Simple restarting benchmark**: `SysbenchBenchmark` (sysbench.py)
- **Complex restarting benchmark**: `BenchBaseBenchmark` (benchbase.py)
- **Distributed benchmark**: `MutilateBenchmark` (mutilate_benchmark.py)
- **Continuous benchmark**: See `SysbenchContinuousBenchmark` pattern
