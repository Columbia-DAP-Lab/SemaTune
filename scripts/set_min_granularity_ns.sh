#!/bin/bash
#
# Script to set the kernel's scheduler min_granularity_ns parameter
# If no value is provided, resets to the default value (3000000 ns)
#

# Default min_granularity_ns value
DEFAULT_MIN_GRANULARITY_NS=3000000

# Path to the sched_min_granularity_ns parameter
MIN_GRANULARITY_PATH="/sys/kernel/debug/sched/min_granularity_ns"

# Check if the script is run with sudo/root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script requires root privileges to modify kernel parameters."
  echo "Please run with sudo or as root."
  exit 1
fi

# Check if the parameter file exists
if [ ! -f "$MIN_GRANULARITY_PATH" ]; then
  echo "Error: Scheduler parameter file not found: $MIN_GRANULARITY_PATH"
  echo "This script may not be compatible with your system."
  exit 1
fi

# If an argument is provided, use it as the new value; otherwise use default
if [ $# -eq 0 ]; then
  # No argument, reset to default
  echo "Resetting scheduler min_granularity_ns to default value: $DEFAULT_MIN_GRANULARITY_NS ns"
  echo $DEFAULT_MIN_GRANULARITY_NS > $MIN_GRANULARITY_PATH
  
  # Verify the change
  CURRENT_VALUE=$(cat $MIN_GRANULARITY_PATH)
  echo "Current min_granularity_ns value: $CURRENT_VALUE ns"
else
  # Use the provided value
  NEW_VALUE=$1
  
  # Validate that the input is a positive integer
  if ! [[ $NEW_VALUE =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid input. Please provide a positive integer."
    exit 1
  fi
  
  echo "Setting scheduler min_granularity_ns to: $NEW_VALUE ns"
  echo $NEW_VALUE > $MIN_GRANULARITY_PATH
  
  # Verify the change
  CURRENT_VALUE=$(cat $MIN_GRANULARITY_PATH)
  echo "Current min_granularity_ns value: $CURRENT_VALUE ns"
  
  if [ "$CURRENT_VALUE" != "$NEW_VALUE" ]; then
    echo "Warning: The value may not have been set correctly."
  fi
fi

exit 0
