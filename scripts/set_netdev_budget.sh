#!/bin/bash
#
# Script to set the netdev_budget sysctl parameter
# Usage: $0 <budget> [cores]
#   budget: Netdev budget (packets per NAPI poll)
#   cores: Optional core specification for IRQ binding like "0-3", "0,1,2", or "all" (default: all)
#

# Default netdev_budget value
DEFAULT_NETDEV_BUDGET=300

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# If an argument is provided, use it as the new value; otherwise use default
if [ $# -eq 0 ]; then
  # No argument, reset to default
  echo "Resetting netdev_budget to default value: $DEFAULT_NETDEV_BUDGET"
  sysctl -w net.core.netdev_budget=$DEFAULT_NETDEV_BUDGET >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    CURRENT_VALUE=$(sysctl -n net.core.netdev_budget)
    echo "Current netdev_budget value: $CURRENT_VALUE"
    exit 0
  else
    echo "Error: Failed to set netdev_budget"
    exit 1
  fi
fi

BUDGET=$1
CORES=${2:-"all"}

# Validate budget
if ! [[ $BUDGET =~ ^[0-9]+$ ]]; then
  echo "Error: Invalid budget. Please provide a positive integer."
  exit 1
fi

# Set sysctl
sysctl -w net.core.netdev_budget=$BUDGET >/dev/null 2>&1
if [ $? -eq 0 ]; then
  CURRENT_VALUE=$(sysctl -n net.core.netdev_budget)
  echo "Set netdev_budget to: $BUDGET (current: $CURRENT_VALUE)"
  
  # Note: IRQ binding would require additional implementation
  if [ "$CORES" != "all" ]; then
    echo "Note: IRQ binding to cores $CORES not implemented in this script"
  fi
  
  if [ "$CURRENT_VALUE" != "$BUDGET" ]; then
    echo "Warning: The value may not have been set correctly."
  fi
  exit 0
else
  echo "Error: Failed to set netdev_budget"
  exit 1
fi

exit 0


