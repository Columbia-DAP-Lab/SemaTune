#!/bin/bash
#
# Script to set the Energy-Performance Preference (EPP)
# Usage: $0 <epp_value> [cores]
#   epp_value: default, performance, balance_performance, balance_power, power
#   cores: Optional core specification like "0-3", "0,1,2", or "all" (default: all)
#
# Note: This script automatically sets scaling_governor to powersave before setting EPP
#

# Valid EPP values
VALID_EPP=("default" "performance" "balance_performance" "balance_power" "power")

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check arguments
if [ $# -eq 0 ]; then
  echo "Usage: $0 <epp_value> [cores]"
  echo "  epp_value: default, performance, balance_performance, balance_power, power"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 performance"
  echo "Example: $0 balance_power 0-3"
  exit 1
fi

EPP_VALUE=$1
CORES=${2:-"all"}

# Validate EPP value
VALID=0
for e in "${VALID_EPP[@]}"; do
  if [ "$EPP_VALUE" == "$e" ]; then
    VALID=1
    break
  fi
done

if [ $VALID -eq 0 ]; then
  echo "Error: Invalid EPP value '$EPP_VALUE'"
  echo "Valid EPP values: ${VALID_EPP[*]}"
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

# Set EPP for each policy (requires powersave governor first)
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
  EPP_FILE="$policy/energy_performance_preference"
  
  # First set governor to powersave (required for EPP)
  if [ -f "$GOV_FILE" ]; then
    echo "powersave" > "$GOV_FILE" 2>/dev/null
    if [ $? -ne 0 ]; then
      continue
    fi
  else
    continue
  fi
  
  # Now set EPP
  if [ -f "$EPP_FILE" ]; then
    echo "$EPP_VALUE" > "$EPP_FILE" 2>/dev/null
    if [ $? -eq 0 ]; then
      SUCCESS=1
      POLICIES_SET=$((POLICIES_SET + 1))
    fi
  fi
done

if [ $SUCCESS -eq 1 ]; then
  if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
    echo "Set EPP to '$EPP_VALUE' (all cores, $POLICIES_SET policies)"
  else
    echo "Set EPP to '$EPP_VALUE' (cores: $CORES_SET, $POLICIES_SET policies)"
  fi
else
  echo "Error: Failed to set EPP"
  exit 1
fi

exit 0


