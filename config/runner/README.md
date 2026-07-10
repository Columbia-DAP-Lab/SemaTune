# BenchBase Runner Configuration Guide

This document describes the configuration options for the `explore.py` BenchBase runner with scheduler parameter tuning.

## Configuration Files

- `config.json` - Full configuration for production runs
- `config_minimal.json` - Minimal configuration for testing

## Configuration Options

### Rate Configuration

Choose **one** of these approaches:

#### Option 1: Range-based rates
```json
{
  "min_rate": 100,
  "max_rate": 1000,
  "step": 100
}
```
Generates rates: [100, 200, 300, ..., 1000]

#### Option 2: Custom rates
```json
{
  "custom_rates": [100, 200, 500, 1000]
}
```
Uses exactly the specified rates.

### Experiment Configuration

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `repetitions` | int | Number of times to repeat each test | `3` |
| `scheduler_params` | array | List of scheduler parameter strings | `["24000000:3000000:4000000"]` |
| `results_directory` | string | Directory to store results | `"benchmark_results"` |
| `temp_directory` | string | Directory for temporary files | `"temp"` |

### Scheduler Parameters Format

Each scheduler parameter string has the format: `latency_ns:min_granularity_ns:wakeup_granularity_ns`

Example configurations:
- `"24000000:3000000:4000000"` - High latency, high granularity
- `"6000000:750000:1000000"` - Low latency, low granularity

### BenchBase Configuration

```json
"benchbase_config": {
  "benchmark_type": "tpcc",
  "database": "postgres", 
  "time": 15,
  "terminals": 8,
  "scalefactor": 4,
  "batchsize": 128,
  "warmup": 0,
  "warmup_runtime": 5,
  "arrival": "regular"
}
```

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `benchmark_type` | string | Type of benchmark to run | `"tpcc"` |
| `database` | string | Database type | `"postgres"` |
| `time` | int | Duration of each test in seconds | `15` |
| `terminals` | int | Number of client terminals | `8` |
| `scalefactor` | int | Database scale factor | `4` |
| `batchsize` | int | Batch size for operations | `128` |
| `warmup` | int | Warmup time (legacy) | `0` |
| `warmup_runtime` | int | Warmup duration in seconds | `5` |
| `arrival` | string | Arrival pattern | `"regular"` |

### Plotting Options

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `show_rate_labels` | boolean | Show rate labels on plots | `false` |
| `stop_at_max_throughput` | boolean | Stop plotting after max throughput | `false` |
| `verbose` | boolean | Show detailed output | `false` |

## Usage Examples

### Run full benchmark suite
```bash
sudo python3 explore.py --config config.json
```

### Run minimal test
```bash
sudo python3 explore.py --config config_minimal.json
```

### Only regenerate plots from existing data
```bash
python3 explore.py --config config.json --plots-only
```

### Override config with command line
```bash
sudo python3 explore.py --min-rate 50 --max-rate 500 --step 50 --repetitions 2 --verbose
```

## Notes

- The script requires root privileges for scheduler parameter changes and BenchBase execution
- Use `--plots-only` mode to regenerate plots without running benchmarks
