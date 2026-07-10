# Mutilate Benchmark Quick Start Guide

## Overview
This directory contains configurations for running mutilate benchmarks with the barebones optimizer.
Mutilate is a **distributed** benchmark that requires two machines:
- **Server (this machine)**: Runs memcached and the optimizer
- **Client (remote machine)**: Generates load and measures performance

## Prerequisites

### On This Server
- ✅ Memcached is installed and running (we already set this up)
- ✅ Port 19876 open for client connections
- ✅ Port 11211 open for memcached

### On Remote Client
- Need IP address of the client machine
- Client needs `mutilate_client.py` script
- Client needs mutilate binary installed

## Quick Setup Steps

### 1. Update Configuration Files

**IMPORTANT**: Replace `REPLACE_WITH_CLIENT_IP` in all config files with your actual client IP address:

```bash
# Example: if client IP is 192.168.1.100
sed -i 's/REPLACE_WITH_CLIENT_IP/192.168.1.100/g' config/mutilate_high_load_latency/*.json
sed -i 's/REPLACE_WITH_CLIENT_IP/192.168.1.100/g' config/mutilate_low_load_power/*.json
```

**IMPORTANT**: Ensure `mutilate_target` points to the **SERVER IP** (this machine), not localhost!
```bash
# Example: if server IP is 128.105.145.4
sed -i 's/"mutilate_target": "127.0.0.1:11211"/"mutilate_target": "128.105.145.4:11211"/g' config/mutilate_high_load_latency/*.json config/mutilate_low_load_power/*.json
```

### 2. Setup Client Machine

Copy the client script to your remote client machine:
```bash
scp src/barebones_optimizer/mutilate_client.py user@CLIENT_IP:~/
```

On the client machine, edit `mutilate_client.py` and update the SERVER_HOST:
```python
SERVER_HOST = "YOUR_SERVER_IP"  # Replace with this server's IP (e.g., 128.105.145.4)
```

**Verify Mutilate Binary Path**:
Check where `mutilate` is installed on the client:
```bash
which mutilate
# or
find ~ -name mutilate
```
If it's not at `~/mutilate/mutilate`, update the path in the config files on the server:
```bash
sed -i 's|"~/mutilate/mutilate"|"/full/path/to/mutilate"|g' config/mutilate_high_load_latency/*.json
```

### 3. Start the Benchmark

**Order is CRITICAL**: Server must start first!

#### On Server (this machine):
```bash
# Start the optimizer - it will wait for client connection
sudo python3 -m barebones_optimizer.main --config config/mutilate_high_load_latency/mutilate_config_fixed.json
```

You should see: `"Waiting for client connection on port 19876..."`

#### On Client (remote machine):
```bash
# Once server is waiting, start the client
python3 mutilate_client.py
```

The client will connect and the benchmark will run automatically!

## Available Configurations

### High-Load Latency Optimization
Located in: `config/mutilate_high_load_latency/`

**Goal**: Minimize p99 latency under high load
**Load**: 500K QPS, 8 threads, 8 clients, exponential inter-arrival, depth=4

- `mutilate_config_fixed.json` - Grid/random search
- `mutilate_config_llm.json` - LLM-based tuning
- `mutilate_config_mlos.json` - Bayesian optimization

### Low-Load Power Optimization
Located in: `config/mutilate_low_load_power/`

**Goal**: Minimize power consumption with p99 latency < 5ms
**Load**: 100K QPS, 4 threads, 4 clients, exponential inter-arrival, depth=1

- `mutilate_config_fixed.json` - Grid/random search
- `mutilate_config_llm.json` - LLM-based tuning
- `mutilate_config_mlos.json` - Bayesian optimization

## Configuration Parameters

Key mutilate-specific parameters:

| Parameter | Description | High-Load | Low-Load |
|-----------|-------------|-----------|----------|
| `mutilate_qps` | Target queries per second | 500000 | 100000 |
| `mutilate_threads` | Worker threads | 8 | 4 |
| `mutilate_clients` | Client connections | 8 | 4 |
| `mutilate_iadist` | Inter-arrival distribution | exponential:10 | exponential:10 |
| `mutilate_depth` | Pipeline depth | 4 | 1 |

**Note**: Use `migration_cost_ns` instead of `sched_migration_cost_ns` for kernel parameter tuning.

## Metrics Collected

The benchmark collects comprehensive metrics:

### Performance Metrics (from client)
- Latency: avg, p95, p99 (milliseconds)
- Throughput: queries per second
- Goodput: successful queries per second

### System Metrics (from server)
- Power: RAPL socket and DRAM power (Watts)
- C-state residency: POLL, C1, C1E, C6 percentages
- CPU utilization: Load percentage
- Perf counters: IPC, cache misses, branch misses, etc.

## Troubleshooting

### Client can't connect
- Make sure server is started **first** and shows "Waiting for client..."
- Check firewall: `sudo ufw allow 19876`
- Verify network: `ping CLIENT_IP`

### Memcached fails to start
- Check if already running: `ps aux | grep memcached`
- Kill existing: `sudo killall memcached`
- Test manually: `memcached -u root -t 4 -m 1024 -l 0.0.0.0 -p 11211`

### No performance data
- Check client logs for errors
- Verify mutilate binary path on client
- Test connectivity: `telnet SERVER_IP 11211`
- **Important**: Ensure `mutilate_target` in config is set to SERVER IP, not localhost!

## Results

Results are saved in the `results_dir` specified in each config:
- `results/mutilate_hi_p99/[tuner]/` - High-load results
- `results/mutilate_lo_pwr_wrt_p99/[tuner]/` - Low-load results

Each directory contains:
- `history.json` - Detailed metrics for each iteration
- CSV files with parameter values and metrics
- Log files

## Example Run

```bash
# 1. Start server (terminal 1 on this machine)
sudo python3 -m barebones_optimizer.main --config config/mutilate_high_load_latency/mutilate_config_fixed.json

# 2. Wait for "Waiting for client connection on port 19876..."

# 3. Start client (terminal on remote machine)
python3 mutilate_client.py

# 4. Watch the optimization run!
# Results will be in results/mutilate_hi_p99/fixed/
```

## More Information

For detailed information, see:
- Main mutilate documentation: `src/barebones_optimizer/benchmarks/MUTILATE_README.md`
- Benchmark implementation: `src/barebones_optimizer/benchmarks/mutilate_benchmark.py`
- Client script: `src/barebones_optimizer/mutilate_client.py`
