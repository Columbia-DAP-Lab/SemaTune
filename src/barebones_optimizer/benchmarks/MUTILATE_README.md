# Mutilate Benchmark Setup and Usage Guide

This guide explains how to set up and run the distributed mutilate benchmark with the barebones OS parameter tuner.

## Overview

The mutilate benchmark is a distributed benchmark that requires:
- **Server side** (where tuning happens): Runs memcached, applies OS parameters, collects power/CPU metrics, coordinates with client
- **Client side** (remote server): Generates load using mutilate, measures performance, sends metrics back

The client connects once at the start and stays connected, restarting load generation per optimization window.

## Prerequisites

### Server Side
- Python 3.6+
- memcached installed and accessible in PATH
- Root/sudo access (for parameter tuning and metrics collection)
- Network access to receive client connections on port 19876
- mutilate repository available (for NIC IRQ pinning utilities, optional)

### Client Side
- Python 3.6+
- mutilate binary compiled and available
- Network access to connect to server on port 19876
- Ability to reach server's memcached instance (default: port 11211)

## Setup Steps

### 1. Server Side Setup

No special setup required beyond installing the barebones optimizer dependencies. The server will:
- Start memcached automatically
- Listen for client connections on port 19876
- Apply OS parameters automatically
- Collect power, C-state, CPU, and perf metrics

### 2. Client Side Setup

#### Step 2.1: Deploy the Client Script

Copy `src/barebones_optimizer/mutilate_client.py` to the remote client machine.

#### Step 2.2: Configure Server IP

Edit `mutilate_client.py` and update the `SERVER_HOST` variable at the top of the file:

```python
SERVER_HOST = "128.105.144.26"  # Change this to your server IP
# For internal network (10.x.x.x), use the server's internal IP:
# SERVER_HOST = "10.10.1.1"  # Example for internal network
```

#### Step 2.3: Ensure mutilate Binary is Available

Make sure the mutilate binary is accessible. The default path is `~/mutilate/mutilate`, but you can:
- Set the path in the server configuration (see Configuration section)
- Or modify the default in `mutilate_client.py` if needed

#### Step 2.4: Test Client Connection (Optional)

You can test the client connection by running:

```bash
python3 mutilate_client.py
```

It should connect to the server and wait for windows. You can interrupt it with Ctrl+C.

## Configuration

### Using Internal Network (10.x.x.x)

If you want to use an internal network interface (e.g., 10.10.1.x) instead of the external network:

1. **Server Configuration**: Set both `mutilate_client_host` and `mutilate_target` to use internal IPs:
   - `mutilate_client_host`: Client's internal IP (e.g., `10.10.1.2`)
   - `mutilate_target`: Server's internal IP with port (e.g., `10.10.1.1:11211`)

2. **Client Script**: Update `SERVER_HOST` in `mutilate_client.py` to the server's internal IP (e.g., `10.10.1.1`)

**Example for internal network setup:**
- Server (node0): `10.10.1.1`
- Client (node1): `10.10.1.2`

```json
{
  "benchmark": "mutilate",
  "mutilate_client_host": "10.10.1.2",
  "mutilate_target": "10.10.1.1:11211",
  ...
}
```

And in `mutilate_client.py`:
```python
SERVER_HOST = "10.10.1.1"
```

**Note**: The TCP server (port 19876) and memcached (port 11211) bind to `0.0.0.0` by default, so they will accept connections on all interfaces including the internal network.

### Server Configuration File

Add the following mutilate-specific settings to your configuration file (e.g., `config/simple_config.json`):

```json
{
  "benchmark": "mutilate",
  
  "mutilate_client_host": "128.105.144.30",
  "mutilate_target": "127.0.0.1:11211",
  "mutilate_threads": 8,
  "mutilate_clients": 8,
  "mutilate_qps": 500000,
  "mutilate_iadist": "fixed:0",
  "mutilate_depth": 1,
  "mutilate_bin_path": "~/mutilate/mutilate",
  "mutilate_memcached_bin": "memcached",
  
  "pin_to_cores": null,
  "window_duration": 60,
  "max_iterations": 10,
  ...
}
```

### Configuration Parameters

- **`mutilate_client_host`** (required): IP address of the remote client machine (use internal IP if using internal network)
- **`mutilate_target`** (default: "127.0.0.1:11211"): Memcached server address (server:port). **Important**: For distributed setup, this should be the server's IP address (not localhost). Use internal IP if using internal network (e.g., "10.10.1.1:11211")
- **`mutilate_threads`** (default: 8): Number of mutilate worker threads
- **`mutilate_clients`** (default: 8): Number of mutilate client connections
- **`mutilate_qps`** (default: 500000): Target queries per second
- **`mutilate_iadist`** (default: "fixed:0"): Request inter-arrival distribution (e.g., "exponential:10", "fixed:0")
- **`mutilate_depth`** (default: 1): Pipeline depth
- **`mutilate_bin_path`** (default: "~/mutilate/mutilate"): Path to mutilate binary on client
- **`mutilate_memcached_bin`** (default: "memcached"): Path to memcached binary on server

### CPU Pinning (Optional)

If you want to pin memcached to specific cores, set `pin_to_cores` in the config:

```json
{
  "pin_to_cores": "0-7",
  ...
}
```

or

```json
{
  "pin_to_cores": "0,1,2,3",
  ...
}
```

## Running the Benchmark

**IMPORTANT**: The optimizer must be started **FIRST** on the server before the client connects. The optimizer starts the TCP server during initialization.

### Step 1: Start the Optimizer (Server)

On the server machine, run the optimizer first:

```bash
# Single-loop optimizer
python3 src/barebones_optimizer/main.py -c config/simple_config.json

# Or dual-loop optimizer
python3 src/barebones_optimizer/dual_loop_main.py -c config/simple_config.json
```

The optimizer will:
1. Start memcached
2. **Start listening on port 19876 for client connection**
3. Wait for client to connect (this is where it will pause)
4. Once connected, run optimization windows
5. Collect metrics from both server and client
6. Apply parameter adjustments
7. Clean up when done

**You should see a message like**: `"Waiting for client connection on port 19876..."`

### Step 2: Start the Client

**After** the optimizer is running and waiting for connection, start the client script on the remote client machine:

```bash
python3 mutilate_client.py
```

The client will:
1. Connect to the server
2. Wait for configuration
3. Display configuration received
4. Wait for windows to start

**Keep both running** - the client will automatically handle each optimization window.

### Order Summary

1. ✅ Start optimizer on **server** (it will wait for client)
2. ✅ Start client on **remote machine** (it will connect)
3. ✅ Both run together until optimization completes

## Execution Flow

For each optimization window:

1. **Server side**:
   - Starts system metrics collection (power, C-state, CPU, perf)
   - Signals client to start load generation
   - Waits for window duration
   - Signals client to stop
   - Receives performance data from client
   - Parses and aggregates all metrics

2. **Client side**:
   - Receives start signal
   - Runs mutilate epochs continuously (~1 second each)
   - Collects performance samples (latency, throughput)
   - Monitors for end signal
   - Sends aggregated performance data to server

## Metrics Collected

### Server-Side Metrics
- **Power**: RAPL socket and DRAM power (Watts)
- **C-state residency**: POLL, C1, C1E, C6 percentages (focused cores)
- **CPU utilization**: Load percentage (focused cores and socket 0)
- **Perf stat**: Cycles, instructions, cache misses, branch misses, IPC, etc.

### Client-Side Metrics
- **Latency**: Average, p95, p99 (in microseconds, converted to milliseconds)
- **Throughput**: Queries per second
- **Goodput**: Assumed equal to throughput (all requests successful)

All metrics are saved in the optimization history JSON file.

## Troubleshooting

### Client Cannot Connect to Server

**Problem**: Client fails to connect with "Connection refused" or timeout.

**Solutions**:
1. **Make sure optimizer is running FIRST on server** - The optimizer must be started before the client attempts to connect
2. Verify server IP address in `mutilate_client.py` is correct
3. Check firewall: ensure port 19876 is open on server
4. Verify server is running and listening: `netstat -tlnp | grep 19876` (should show LISTEN state)
5. Test network connectivity: `ping <server_ip>`
6. Check server logs - you should see "Waiting for client connection on port 19876..." before client connects

### Memcached Fails to Start

**Problem**: Server logs show "Memcached failed to start!"

**Solutions**:
1. Check if memcached is installed: `which memcached`
2. Check if another memcached instance is running: `ps aux | grep memcached`
3. Try manually starting memcached: `memcached -u root -t 4 -m 1024 -l 0.0.0.0 -p 11211`
4. Check if port 11211 is already in use: `netstat -tlnp | grep 11211`

### Client Cannot Reach Memcached

**Problem**: Client shows errors or zero throughput.

**Solutions**:
1. Verify `mutilate_target` in config matches server's memcached address
2. Test connectivity from client: `telnet <server_ip> 11211`
3. Check firewall: ensure port 11211 is open on server
4. Verify memcached is listening on correct interface: `netstat -tlnp | grep 11211`

### No Performance Metrics Received

**Problem**: Server shows "Received 0 performance samples from client".

**Solutions**:
1. Check client logs for mutilate execution errors
2. Verify mutilate binary path is correct on client
3. Test mutilate manually on client:
   ```bash
   ~/mutilate/mutilate -s <server_ip>:11211 --noload -T 4 -c 4 -q 10000 -t 1
   ```
4. Check network latency between client and server

### Client Disconnects During Execution

**Problem**: Server logs show "Client disconnected" errors.

**Solutions**:
1. Check network stability between client and server
2. Increase socket timeout in `mutilate_benchmark.py` if needed
3. Check for firewall or NAT timeout issues
4. Verify client script is still running

### Permission Errors

**Problem**: "Permission denied" errors when starting memcached or collecting metrics.

**Solutions**:
1. Run optimizer with sudo: `sudo python3 src/barebones_optimizer/main.py ...`
2. Ensure memcached can run as root (or configure user appropriately)
3. Check file permissions for metrics collection paths

## Example Configuration File

Here's a complete example configuration file for mutilate:

```json
{
  "benchmark": "mutilate",
  "pin_to_cores": null,
  "mutilate_client_host": "10.10.1.2",  // For internal network, use client's internal IP (e.g., "10.10.1.2")
  "mutilate_target": "10.10.1.1:11211",  // For distributed setup, use server IP (e.g., "10.10.1.1:11211" for internal network)
  "mutilate_threads": 8,
  "mutilate_clients": 8,
  "mutilate_qps": 500000,
  "mutilate_iadist": "fixed:0",
  "mutilate_depth": 1,
  "mutilate_bin_path": "~/mutilate/mutilate",
  "mutilate_memcached_bin": "memcached",
  "tuner_type": "llm",
  "parameter_ranges": {
    "min_granularity_ns": [100000, 50000000]
  },
  "fixed_parameters": {
    "latency_ns": 24000000,
    "wakeup_granularity_ns": 4000000
  },
  "optimization_metric": "throughput",
  "optimization_goal": "maximize",
  "max_iterations": 10,
  "window_duration": 60,
  "results_dir": "results",
  "llm_api_key": null,
  "llm_model_name": "gemini-2.5-pro"
}
```

## Notes

- **The optimizer must be started FIRST** on the server - it will wait for the client to connect
- Once connected, the client will automatically restart load generation for each window
- All metrics are collected automatically - no manual intervention needed
- The optimizer will clean up memcached and close client connection when done
- Results are saved in the `results_dir` specified in the configuration
- Set `GEMINI_API_KEY` in the environment for LLM runs; do not store a key in
  the configuration.
- If connection fails, verify the optimizer is running and waiting for connections before starting the client

## Support

For issues or questions, refer to the main project documentation or check the logs:
- Server logs: `barebones_optimizer.log` or `dual_loop_barebones_optimizer.log`
- Client output: printed to stdout
