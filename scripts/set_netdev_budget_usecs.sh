#!/bin/bash
#
# Script to set the netdev_budget_usecs sysctl parameter
# Usage: $0 <timeout_us> [cores]
#   timeout_us: Netdev budget time in microseconds
#   cores: Optional core specification for IRQ binding like "0-3", "0,1,2", or "all" (default: all)
#

# Default netdev_budget_usecs value
DEFAULT_NETDEV_BUDGET_USECS=8000

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# If an argument is provided, use it as the new value; otherwise use default
if [ $# -eq 0 ]; then
  # No argument, reset to default
  echo "Resetting netdev_budget_usecs to default value: $DEFAULT_NETDEV_BUDGET_USECS"
  sysctl -w net.core.netdev_budget_usecs=$DEFAULT_NETDEV_BUDGET_USECS >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    CURRENT_VALUE=$(sysctl -n net.core.netdev_budget_usecs)
    echo "Current netdev_budget_usecs value: $CURRENT_VALUE"
    exit 0
  else
    echo "Error: Failed to set netdev_budget_usecs"
    exit 1
  fi
fi

TIMEOUT=$1
CORES=${2:-"all"}

# Validate timeout
if ! [[ $TIMEOUT =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid timeout. Please provide a positive integer (microseconds)."
  exit 1
fi

# Set sysctl
sysctl -w net.core.netdev_budget_usecs=$TIMEOUT >/dev/null 2>&1
if [ $? -eq 0 ]; then
  CURRENT_VALUE=$(sysctl -n net.core.netdev_budget_usecs)
  echo "Set netdev_budget_usecs to: $TIMEOUT (current: $CURRENT_VALUE)"
  
  # Note: IRQ binding would require additional implementation
  if [ "$CORES" != "all" ]; then
    echo "Note: IRQ binding to cores $CORES not implemented in this script"
  fi
  
  if [ "$CURRENT_VALUE" != "$TIMEOUT" ]; then
    echo "Warning: The value may not have been set correctly."
  fi
  exit 0
else
  echo "Error: Failed to set netdev_budget_usecs"
  exit 1
fi

exit 0


