#!/bin/bash

echo "Running Sysbench optimization experiments..."

# 1. Fixed Default
echo "=========================================="
echo "Running: Fixed Default (latency=24ms, granularity=3ms)"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_fixed_default.json' --cleanup-before-start --stream-output --individual

# 2. Bayesian Maximize Goodput  
echo "=========================================="
echo "Running: Bayesian Maximize Goodput"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_bayesian_goodput.json' --cleanup-before-start --stream-output --individual

# 3. Bayesian Minimize P95 Latency
echo "=========================================="
echo "Running: Bayesian Minimize P95 Latency"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_bayesian_min_p95.json' --cleanup-before-start --stream-output --individual

# 4. Bayesian Maximize IPC
echo "=========================================="
echo "Running: Bayesian Maximize IPC"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_bayesian_max_ipc.json' --cleanup-before-start --stream-output --individual

# 5. Bayesian Minimize Context Switches
echo "=========================================="
echo "Running: Bayesian Minimize Context Switches"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_bayesian_min_context_switches.json' --cleanup-before-start --stream-output --individual

# 6. Bayesian Maximize Cache Misses
echo "=========================================="
echo "Running: Bayesian Maximize Cache Misses"
echo "=========================================="
sudo python3 ./src/optimizer/main.py -c 'config/optimizer/optimizer_sysbench_bayesian_max_cache_misses.json' --cleanup-before-start --stream-output --individual

echo "All experiments completed!" 