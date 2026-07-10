#!/bin/bash
#
# Script to set the busy_read sysctl parameter
# Usage: $0 <timeout_us> [cores]
#   timeout_us: Busy read timeout in microseconds
#   cores: Optional core specification for IRQ binding like "0-3", "0,1,2", or "all" (default: all)
#

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check arguments
if [ $# -eq 0 ]; then
  echo "Usage: $0 <timeout_us> [cores]"
  echo "  timeout_us: Busy read timeout in microseconds"
  echo "  cores: Optional core specification for IRQ binding like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 0"
  echo "Example: $0 50 0-3"
  exit 1
fi

TIMEOUT=$1
CORES=${2:-"all"}

# Validate timeout
if ! [[ $TIMEOUT =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid timeout. Please provide a positive integer (microseconds)."
  exit 1
fi

# Set sysctl
sysctl -w net.core.busy_read=$TIMEOUT >/dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "Set net.core.busy_read=$TIMEOUT"
  
  # Note: IRQ binding would require additional implementation
  # For now, just report the cores specification
  if [ "$CORES" != "all" ]; then
    echo "Note: IRQ binding to cores $CORES not implemented in this script"
  fi
  
  exit 0
else
  echo "Error: Failed to set net.core.busy_read"
  exit 1
fi

exit 0


