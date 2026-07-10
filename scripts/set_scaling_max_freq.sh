#!/bin/bash
#
# Script to set the maximum CPU frequency
# Usage: $0 <freq_khz> [cores]
#   freq_khz: Maximum frequency in kHz (0 = use system default)
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
  echo "Usage: $0 <freq_khz> [cores]"
  echo "  freq_khz: Maximum frequency in kHz (0 = use system default)"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 3500000"
  echo "Example: $0 0 0-3"
  exit 1
fi

FREQ=$1
CORES=${2:-"all"}

# Validate frequency
if ! [[ $FREQ =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid frequency. Please provide a positive integer (kHz)."
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

# Set maximum frequency for each policy
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
  
  MAX_FREQ_FILE="$policy/scaling_max_freq"
  if [ -f "$MAX_FREQ_FILE" ]; then
    if [ "$FREQ" == "0" ]; then
      # Use system default - read from cpuinfo_max_freq
      CPUINFO_MAX="$policy/cpuinfo_max_freq"
      if [ -f "$CPUINFO_MAX" ]; then
        DEFAULT_FREQ=$(cat "$CPUINFO_MAX")
        echo "$DEFAULT_FREQ" > "$MAX_FREQ_FILE" 2>/dev/null
      else
        continue
      fi
    else
      echo "$FREQ" > "$MAX_FREQ_FILE" 2>/dev/null
    fi
    
    if [ $? -eq 0 ]; then
      SUCCESS=1
      POLICIES_SET=$((POLICIES_SET + 1))
    fi
  fi
done

if [ $SUCCESS -eq 1 ]; then
  if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
    echo "Set scaling_max_freq to ${FREQ}kHz (all cores, $POLICIES_SET policies)"
  else
    echo "Set scaling_max_freq to ${FREQ}kHz (cores: $CORES_SET, $POLICIES_SET policies)"
  fi
else
  echo "Error: Failed to set scaling_max_freq"
  exit 1
fi

exit 0


