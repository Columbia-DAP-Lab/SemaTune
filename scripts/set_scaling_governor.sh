#!/bin/bash
#
# Script to set the CPU frequency scaling governor
# Usage: $0 <governor> [cores]
#   governor: performance, powersave, ondemand, conservative, userspace, schedutil
#   cores: Optional core specification like "0-3", "0,1,2", or "all" (default: all)
#

# Valid governors
VALID_GOVERNORS=("performance" "powersave" "ondemand" "conservative" "userspace" "schedutil")

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check arguments
if [ $# -eq 0 ]; then
  echo "Usage: $0 <governor> [cores]"
  echo "  governor: performance, powersave, ondemand, conservative, userspace, schedutil"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 performance"
  echo "Example: $0 powersave 0-3"
  exit 1
fi

GOVERNOR=$1
CORES=${2:-"all"}

# Validate governor
VALID=0
for g in "${VALID_GOVERNORS[@]}"; do
  if [ "$GOVERNOR" == "$g" ]; then
    VALID=1
    break
  fi
done

if [ $VALID -eq 0 ]; then
  echo "Error: Invalid governor '$GOVERNOR'"
  echo "Valid governors: ${VALID_GOVERNORS[*]}"
  exit 1
fi

# Check if cpufreq is available
POLROOT="/sys/devices/system/cpu/cpufreq"
if [ ! -d "$POLROOT" ]; then
  echo "Error: cpufreq not available on this system."
  exit 1
fi

# Parse cores specification
if [ "$CORES" == "all" ]; then
  CORES_SET=""
else
  CORES_SET="$CORES"
fi

# Set governor for each policy
SUCCESS=0
POLICIES_SET=0

for policy in "$POLROOT"/policy*; do
  if [ ! -d "$policy" ]; then
    continue
  fi
  
  # Check if this policy should be modified based on cores
  if [ -n "$CORES_SET" ] && [ "$CORES_SET" != "all" ]; then
    AFFECTED_CPUS_FILE="$policy/affected_cpus"
    if [ -f "$AFFECTED_CPUS_FILE" ]; then
      AFFECTED_CPUS=$(cat "$AFFECTED_CPUS_FILE" | tr ' ' '\n' | tr '\n' ' ')
      FOUND=0
      IFS=',' read -ra CORE_LIST <<< "$CORES_SET"
      for core_spec in "${CORE_LIST[@]}"; do
        if [[ "$core_spec" == *"-"* ]]; then
          START=$(echo "$core_spec" | cut -d'-' -f1)
          END=$(echo "$core_spec" | cut -d'-' -f2)
          for cpu in $AFFECTED_CPUS; do
            if [ "$cpu" -ge "$START" ] && [ "$cpu" -le "$END" ]; then
              FOUND=1
              break
            fi
          done
        else
          for cpu in $AFFECTED_CPUS; do
            if [ "$cpu" == "$core_spec" ]; then
              FOUND=1
              break
            fi
          done
        fi
        [ $FOUND -eq 1 ] && break
      done
      [ $FOUND -eq 0 ] && continue
    fi
  fi
  
  GOV_FILE="$policy/scaling_governor"
  if [ -f "$GOV_FILE" ]; then
    echo "$GOVERNOR" > "$GOV_FILE" 2>/dev/null
    if [ $? -eq 0 ]; then
      SUCCESS=1
      POLICIES_SET=$((POLICIES_SET + 1))
    fi
  fi
done

if [ $SUCCESS -eq 1 ]; then
  if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
    echo "Set scaling_governor to '$GOVERNOR' (all cores, $POLICIES_SET policies)"
  else
    echo "Set scaling_governor to '$GOVERNOR' (cores: $CORES_SET, $POLICIES_SET policies)"
  fi
else
  echo "Error: Failed to set scaling_governor"
  exit 1
fi

exit 0


