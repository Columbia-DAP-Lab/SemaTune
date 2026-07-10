#!/bin/bash
#
# This script adjusts kernel scheduler parameters (latency, min_granularity, wakeup_granularity)
# using values provided directly in a tuple format.
#
# Usage: $0 <latency_ns>:<min_granularity_ns>:<wakeup_granularity_ns>
# Example: $0 30000000:3000000:4000000
#
# Requires root privileges.
# Note: This script uses paths under /sys/kernel/debug/sched/, which requires
# the debugfs filesystem to be mounted (typically at /sys/kernel/debug).
#

set -euo pipefail # Exit on error, unset var, pipefail

# --- Configuration ---
# Sysctl paths for scheduler parameters (using debugfs interface)
LATENCY_NS_PATH="/sys/kernel/debug/sched/latency_ns"
MIN_GRANULARITY_NS_PATH="/sys/kernel/debug/sched/min_granularity_ns"
WAKEUP_GRANULARITY_NS_PATH="/sys/kernel/debug/sched/wakeup_granularity_ns"

# --- Global Variables for Parsed Input ---
# These will be set by validate_input
TARGET_LATENCY_NS=""
TARGET_MIN_GRANULARITY_NS=""
TARGET_WAKEUP_GRANULARITY_NS=""

# --- Functions ---

# Function to check if the script is run as root
check_root() {
    if [[ "$EUID" -ne 0 ]]; then
        echo "Error: This script must be run as root to modify kernel parameters." >&2
        exit 1
    fi
}

# Function to validate user input (tuple format)
validate_input() {
    if [[ $# -ne 1 ]]; then
        echo "Usage: $0 <latency_ns>:<min_granularity_ns>:<wakeup_granularity_ns>" >&2
        echo "Example: $0 30000000:3000000:4000000" >&2
        exit 1
    fi

    local input_tuple="$1"
    local val_lat val_min val_wake

    # Parse the tuple
    IFS=':' read -r val_lat val_min val_wake <<< "$input_tuple"

    if [[ -z "$val_lat" || -z "$val_min" || -z "$val_wake" ]]; then
        echo "Error: Invalid input format. Expected <latency_ns>:<min_granularity_ns>:<wakeup_granularity_ns>" >&2
        echo "Received: '$input_tuple'" >&2
        exit 1
    fi

    # Validate each part as a positive integer
    if ! [[ "$val_lat" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Target latency '$val_lat' must be a positive integer (nanoseconds)." >&2
        exit 1
    fi
    if ! [[ "$val_min" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Target min granularity '$val_min' must be a positive integer (nanoseconds)." >&2
        exit 1
    fi
    if ! [[ "$val_wake" =~ ^[1-9][0-9]*$ ]]; then
        echo "Error: Target wakeup granularity '$val_wake' must be a positive integer (nanoseconds)." >&2
        exit 1
    fi

    # Script-level minimum for latency as a basic sanity check.
    local min_allowed_latency_ns=100 # 1ms
    if [[ "$val_lat" -lt "$min_allowed_latency_ns" ]]; then
        echo "Error: Target latency $val_lat ns is too low. Please specify a value >= $min_allowed_latency_ns ns (1ms)." >&2
        exit 1
    fi

    # Assign to global variables
    TARGET_LATENCY_NS="$val_lat"
    TARGET_MIN_GRANULARITY_NS="$val_min"
    TARGET_WAKEUP_GRANULARITY_NS="$val_wake"

    # Optional: Basic check for typical kernel constraints. Kernel will enforce these anyway.
    if [[ "$TARGET_MIN_GRANULARITY_NS" -gt "$TARGET_WAKEUP_GRANULARITY_NS" ]]; then
        echo "Warning: Input min_granularity_ns ($TARGET_MIN_GRANULARITY_NS) is greater than wakeup_granularity_ns ($TARGET_WAKEUP_GRANULARITY_NS)." >&2
        echo "The kernel typically expects min_granularity <= wakeup_granularity." >&2
    fi
    if [[ "$TARGET_WAKEUP_GRANULARITY_NS" -gt "$TARGET_LATENCY_NS" ]]; then
         echo "Warning: Input wakeup_granularity_ns ($TARGET_WAKEUP_GRANULARITY_NS) is greater than latency_ns ($TARGET_LATENCY_NS)." >&2
         echo "The kernel typically expects wakeup_granularity <= latency_ns." >&2
    fi
}

# Function to read a sysctl value
# Arguments: $1 = path to sysctl file, $2 = description of the value
read_sysctl_value() {
    local path="$1"
    local description="$2"
    if [[ ! -f "$path" ]]; then
        echo "Error: Sysctl path '$path' ($description) not found." >&2
        echo "Ensure debugfs is mounted and the path is correct." >&2
        exit 1
    fi
    local value
    value=$(cat "$path")
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then # Check if it's an unsigned integer
        echo "Error: Could not read a valid integer from '$path' ($description). Value read: '$value'" >&2
        exit 1
    fi
    echo "$value"
}

# Function to write a sysctl value
# Arguments: $1 = path to sysctl file, $2 = value to write, $3 = description
write_sysctl_value() {
    local path="$1"
    local value_to_write="$2"
    local description="$3"
    echo "Attempting to set $description ($path) to $value_to_write ns..."
    if ! echo "$value_to_write" > "$path"; then
        echo "Error: Failed to write $value_to_write to $path ($description)." >&2
        echo "Kernel might have rejected the value due to constraints or permissions." >&2
        # The script will exit due to 'set -e' if the echo command fails.
        exit 1 # Explicit exit for clarity, though set -e would handle it.
    fi
    echo "$description set successfully to $value_to_write ns."
}

# --- Main Script ---

# 1. Check for root privileges
check_root

# 2. Validate input and parse tuple
validate_input "$@"
# TARGET_LATENCY_NS, TARGET_MIN_GRANULARITY_NS, TARGET_WAKEUP_GRANULARITY_NS are now set globally.

# 3. Read current live scheduler values
echo "--- Current Scheduler Values (Live from System) ---"
current_latency_ns=$(read_sysctl_value "$LATENCY_NS_PATH" "current sched_latency_ns")
current_min_granularity_ns=$(read_sysctl_value "$MIN_GRANULARITY_NS_PATH" "current sched_min_granularity_ns")
current_wakeup_granularity_ns=$(read_sysctl_value "$WAKEUP_GRANULARITY_NS_PATH" "current sched_wakeup_granularity_ns")

echo "Current sched_latency_ns            : $current_latency_ns ns"
echo "Current sched_min_granularity_ns   : $current_min_granularity_ns ns"
echo "Current sched_wakeup_granularity_ns: $current_wakeup_granularity_ns ns"
echo

# 4. Display proposed new values (directly from user input)
echo "--- Proposed New Scheduler Values (from your input) ---"
echo "Target sched_latency_ns            : $TARGET_LATENCY_NS ns"
echo "Target sched_min_granularity_ns   : $TARGET_MIN_GRANULARITY_NS ns"
echo "Target sched_wakeup_granularity_ns: $TARGET_WAKEUP_GRANULARITY_NS ns"
echo
echo "Note: The kernel enforces its own constraints (e.g., min_granularity <= wakeup_granularity <= latency)."
echo "If your input values violate these, the kernel may adjust them or reject the change."
echo

# 5. Confirmation before applying
read -r -p "Apply these changes? (yes/NO): " confirmation
if [[ "${confirmation,}" != "yes" ]]; then # Convert to lowercase for robust comparison
    echo "Changes aborted by user."
    exit 0
fi

# 6. Apply changes
echo
echo "--- Applying Changes ---"
# Order of setting:
# It's generally advisable to set latency first if increasing, or last if decreasing.
# The kernel will ultimately validate all interdependencies.

write_sysctl_value "$LATENCY_NS_PATH" "$TARGET_LATENCY_NS" "sched_latency_ns"
write_sysctl_value "$MIN_GRANULARITY_NS_PATH" "$TARGET_MIN_GRANULARITY_NS" "sched_min_granularity_ns"
write_sysctl_value "$WAKEUP_GRANULARITY_NS_PATH" "$TARGET_WAKEUP_GRANULARITY_NS" "sched_wakeup_granularity_ns"

echo
echo "--- Verification: Scheduler Values After Update ---"
# Read back the values to see what the kernel actually set
final_latency_ns=$(read_sysctl_value "$LATENCY_NS_PATH" "final sched_latency_ns")
final_min_granularity_ns=$(read_sysctl_value "$MIN_GRANULARITY_NS_PATH" "final sched_min_granularity_ns")
final_wakeup_granularity_ns=$(read_sysctl_value "$WAKEUP_GRANULARITY_NS_PATH" "final sched_wakeup_granularity_ns")

echo "Actual sched_latency_ns            : $final_latency_ns ns"
echo "Actual sched_min_granularity_ns   : $final_min_granularity_ns ns"
echo "Actual sched_wakeup_granularity_ns: $final_wakeup_granularity_ns ns"

# Check if the final values match the intended target values
if [[ "$final_latency_ns" -ne "$TARGET_LATENCY_NS" ]] || \
   [[ "$final_min_granularity_ns" -ne "$TARGET_MIN_GRANULARITY_NS" ]] || \
   [[ "$final_wakeup_granularity_ns" -ne "$TARGET_WAKEUP_GRANULARITY_NS" ]]; then
    echo
    echo "Warning: One or more parameters after update do not exactly match your target input values." >&2
    echo "This is often due to kernel-enforced constraints, minimum/maximum limits, or internal adjustments." >&2
    echo "  Target Latency: $TARGET_LATENCY_NS ns, Actual: $final_latency_ns ns"
    echo "  Target Min Granularity: $TARGET_MIN_GRANULARITY_NS ns, Actual: $final_min_granularity_ns ns"
    echo "  Target Wakeup Granularity: $TARGET_WAKEUP_GRANULARITY_NS ns, Actual: $final_wakeup_granularity_ns ns"
else
    echo
    echo "All scheduler parameters updated and verified successfully to your target input values."
fi

exit 0
