#!/bin/bash
#
# Script to enable or disable CPU turbo boost
# Usage: $0 <on|off|1|0>
#   on/1: Enable turbo boost
#   off/0: Disable turbo boost
#

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check arguments
if [ $# -eq 0 ]; then
  echo "Usage: $0 <on|off|1|0>"
  echo "  on/1: Enable turbo boost"
  echo "  off/0: Disable turbo boost"
  echo ""
  echo "Example: $0 on"
  echo "Example: $0 off"
  exit 1
fi

TURBO_ARG=$1

# Parse argument
if [ "$TURBO_ARG" == "on" ] || [ "$TURBO_ARG" == "1" ]; then
  TURBO_VAL="0"  # 0 = turbo on
  TURBO_STR="ON"
elif [ "$TURBO_ARG" == "off" ] || [ "$TURBO_ARG" == "0" ]; then
  TURBO_VAL="1"  # 1 = turbo off
  TURBO_STR="OFF"
else
  echo "Error: Invalid argument. Use 'on', 'off', '1', or '0'"
  exit 1
fi

# Set turbo (intel_pstate)
TURBO_PATH="/sys/devices/system/cpu/intel_pstate/no_turbo"
if [ -f "$TURBO_PATH" ]; then
  echo "$TURBO_VAL" > "$TURBO_PATH" 2>/dev/null
  if [ $? -eq 0 ]; then
    echo "Set turbo boost to $TURBO_STR"
    exit 0
  else
    echo "Error: Failed to set turbo boost"
    exit 1
  fi
else
  echo "Error: intel_pstate not available on this system."
  exit 1
fi

exit 0


