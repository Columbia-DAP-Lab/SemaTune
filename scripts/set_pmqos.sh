#!/bin/bash
#
# Script to set PM QoS resume latency
# Usage: $0 <latency_us> [cores]
#   latency_us: Resume latency in microseconds
#   cores: Optional core specification like "0-3", "0,1,2", or "all" (default: all)
#

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check arguments
if [ $# -eq 0 ]; then
  echo "Usage: $0 <latency_us> [cores]"
  echo "  latency_us: Resume latency in microseconds"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 0"
  echo "Example: $0 10 0-3"
  exit 1
fi

LATENCY=$1
CORES=${2:-"all"}

# Validate latency
if ! [[ $LATENCY =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid latency. Please provide a positive integer (microseconds)."
  exit 1
fi

# Parse cores specification
if [ "$CORES" == "all" ]; then
  CORES_SET=""
else
  CORES_SET="$CORES"
fi

# Set PM QoS for each CPU
SUCCESS=0
CPUS_SET=0
CPU_ROOT="/sys/devices/system/cpu"

for cpu_dir in "$CPU_ROOT"/cpu[0-9]*; do
  if [ ! -d "$cpu_dir" ]; then
    continue
  fi
  
  # Extract CPU number
  CPU_NUM=$(basename "$cpu_dir" | sed 's/cpu//')
  
  # Check if this CPU should be modified based on cores
  if [ -n "$CORES_SET" ] && [ "$CORES_SET" != "all" ]; then
    FOUND=0
    IFS=',' read -ra CORE_LIST <<< "$CORES_SET"
    for core_spec in "${CORE_LIST[@]}"; do
      if [[ "$core_spec" == *"-"* ]]; then
        START=$(echo "$core_spec" | cut -d'-' -f1)
        END=$(echo "$core_spec" | cut -d'-' -f2)
        if [ "$CPU_NUM" -ge "$START" ] && [ "$CPU_NUM" -le "$END" ]; then
          FOUND=1
          break
        fi
      else
        if [ "$CPU_NUM" == "$core_spec" ]; then
          FOUND=1
          break
        fi
      fi
    done
    [ $FOUND -eq 0 ] && continue
  fi
  
  PMQOS_FILE="$cpu_dir/power/pm_qos_resume_latency_us"
  if [ -f "$PMQOS_FILE" ]; then
    echo "$LATENCY" > "$PMQOS_FILE" 2>/dev/null
    if [ $? -eq 0 ]; then
      SUCCESS=1
      CPUS_SET=$((CPUS_SET + 1))
    fi
  fi
done

if [ $SUCCESS -eq 1 ]; then
  if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
    echo "Set pm_qos_resume_latency_us to ${LATENCY}µs (all cores, $CPUS_SET CPUs)"
  else
    echo "Set pm_qos_resume_latency_us to ${LATENCY}µs (cores: $CORES_SET, $CPUS_SET CPUs)"
  fi
else
  echo "Error: Failed to set pm_qos_resume_latency_us"
  exit 1
fi

exit 0


