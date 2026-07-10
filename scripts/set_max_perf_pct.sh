#!/bin/bash
#
# Script to set the maximum performance percentage
# Usage: $0 <percentage> [cores]
#   percentage: Maximum performance percentage (0-100)
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
  echo "Usage: $0 <percentage> [cores]"
  echo "  percentage: Maximum performance percentage (0-100)"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 100"
  echo "Example: $0 90 0-3"
  exit 1
fi

PERCENT=$1
CORES=${2:-"all"}

# Validate percentage
if ! [[ $PERCENT =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid percentage. Please provide a positive integer (0-100)."
  exit 1
fi

if [ "$PERCENT" -lt 0 ] || [ "$PERCENT" -gt 100 ]; then
  echo "Error: Percentage must be between 0 and 100."
  exit 1
fi

# Set max_perf_pct (intel_pstate is global)
PERF_PATH="/sys/devices/system/cpu/intel_pstate/max_perf_pct"
if [ -f "$PERF_PATH" ]; then
  echo "$PERCENT" > "$PERF_PATH" 2>/dev/null
  if [ $? -eq 0 ]; then
    if [ "$CORES" == "all" ]; then
      echo "Set max_perf_pct to ${PERCENT}% (global)"
    else
      echo "Set max_perf_pct to ${PERCENT}% (global, cores: $CORES)"
    fi
    exit 0
  else
    echo "Error: Failed to set max_perf_pct"
    exit 1
fi
else
  echo "Error: intel_pstate not available on this system."
  exit 1
fi

exit 0


