#!/bin/bash
#
# Script to set the maximum C-state allowed
# Usage: $0 <level> [cores]
#   level: unlimited, none, poll, C1, C1E, C2, C6, C6only
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
  echo "Usage: $0 <level> [cores]"
  echo "  level: unlimited, none, poll, C1, C1E, C2, C6, C6only"
  echo "  cores: Optional core specification like \"0-3\", \"0,1,2\", or \"all\" (default: all)"
  echo ""
  echo "Example: $0 unlimited"
  echo "Example: $0 C6 0-3"
  exit 1
fi

LEVEL=$1
CORES=${2:-"all"}

# Parse cores specification
if [ "$CORES" == "all" ]; then
  CORES_SET=""
else
  CORES_SET="$CORES"
fi

# Handle "unlimited" - enable all C-states
if [ "$LEVEL" == "unlimited" ]; then
  CPU_ROOT="/sys/devices/system/cpu"
  ENABLED_COUNT=0
  
  for cpu_dir in "$CPU_ROOT"/cpu[0-9]*; do
    if [ ! -d "$cpu_dir" ]; then
      continue
    fi
    
    CPU_NUM=$(basename "$cpu_dir" | sed 's/cpu//')
    
    # Check if this CPU should be modified based on cores
    if [ -n "$CORES_SET" ] && [ "$CORES_SET" != "all" ]; then
      FOUND=0
      IFS=',' read -ra CORE_LIST <<< "$CORES_SET"
      for core_spec in "${CORE_LIST[@]}"; do
        if [[ "$core_spec" == *"-"* ]]; then
          START=$(echo "$core_spec" | cut -d'-' -f1)
          END=$(echo "$core_spec" | cut -d'-' -f2)
          if [ "$CPU_NUM" -ge "$START" ] && [ "$CPU_NUM" -le "$END" ]; then
            FOUND=1
            break
          fi
        else
          if [ "$CPU_NUM" == "$core_spec" ]; then
            FOUND=1
            break
          fi
        fi
      done
      [ $FOUND -eq 0 ] && continue
    fi
    
    CPUIDLE_DIR="$cpu_dir/cpuidle"
    if [ ! -d "$CPUIDLE_DIR" ]; then
      continue
    fi
    
    for state_dir in "$CPUIDLE_DIR"/state[0-9]*; do
      if [ ! -d "$state_dir" ]; then
        continue
      fi
      
      DISABLE_FILE="$state_dir/disable"
      if [ -f "$DISABLE_FILE" ]; then
        echo "0" > "$DISABLE_FILE" 2>/dev/null
        if [ $? -eq 0 ]; then
          ENABLED_COUNT=$((ENABLED_COUNT + 1))
        fi
      fi
    done
  done
  
  if [ $ENABLED_COUNT -gt 0 ]; then
    if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
      echo "Set cstate_max=unlimited (all cores) - enabled $ENABLED_COUNT C-states"
    else
      echo "Set cstate_max=unlimited (cores: $CORES_SET) - enabled $ENABLED_COUNT C-states"
    fi
    exit 0
  else
    echo "Error: Failed to enable C-states"
    exit 1
  fi
fi

# Handle other C-state levels
# Parse C-state number
C_NUM=""
if [ "$LEVEL" == "none" ] || [ "$LEVEL" == "poll" ] || [ "$LEVEL" == "0" ]; then
  C_NUM=0
elif [ "$LEVEL" == "C6only" ]; then
  C_NUM=999  # Special marker
elif [ "$LEVEL" == "C1E" ]; then
  C_NUM=2
else
  # Extract number from C1, C2, C6, etc.
  C_NUM=$(echo "$LEVEL" | sed 's/[^0-9]//g')
  if [ -z "$C_NUM" ]; then
    C_NUM=1
  fi
fi

CPU_ROOT="/sys/devices/system/cpu"
DISABLED_COUNT=0
ENABLED_COUNT=0

for cpu_dir in "$CPU_ROOT"/cpu[0-9]*; do
  if [ ! -d "$cpu_dir" ]; then
    continue
  fi
  
  CPU_NUM=$(basename "$cpu_dir" | sed 's/cpu//')
  
  # Check if this CPU should be modified based on cores
  if [ -n "$CORES_SET" ] && [ "$CORES_SET" != "all" ]; then
    FOUND=0
    IFS=',' read -ra CORE_LIST <<< "$CORES_SET"
    for core_spec in "${CORE_LIST[@]}"; do
      if [[ "$core_spec" == *"-"* ]]; then
        START=$(echo "$core_spec" | cut -d'-' -f1)
        END=$(echo "$core_spec" | cut -d'-' -f2)
        if [ "$CPU_NUM" -ge "$START" ] && [ "$CPU_NUM" -le "$END" ]; then
          FOUND=1
          break
        fi
      else
        if [ "$CPU_NUM" == "$core_spec" ]; then
          FOUND=1
          break
        fi
      fi
    done
    [ $FOUND -eq 0 ] && continue
  fi
  
  CPUIDLE_DIR="$cpu_dir/cpuidle"
  if [ ! -d "$CPUIDLE_DIR" ]; then
    continue
  fi
  
  for state_dir in "$CPUIDLE_DIR"/state[0-9]*; do
    if [ ! -d "$state_dir" ]; then
      continue
    fi
    
    DISABLE_FILE="$state_dir/disable"
    NAME_FILE="$state_dir/name"
    
    if [ ! -f "$DISABLE_FILE" ]; then
      continue
    fi
    
    # Read C-state name
    C_NAME=""
    if [ -f "$NAME_FILE" ]; then
      C_NAME=$(cat "$NAME_FILE" | tr '[:lower:]' '[:upper:]' | tr -d ' ')
    fi
    
    # Parse C-number from name
    STATE_C_NUM=""
    if [ "$C_NAME" == "POLL" ]; then
      STATE_C_NUM=0
    elif [ "$C_NAME" == "C1" ]; then
      STATE_C_NUM=1
    elif [ "$C_NAME" == "C1E" ]; then
      STATE_C_NUM=2
    else
      STATE_C_NUM=$(echo "$C_NAME" | grep -oE 'C[0-9]+' | sed 's/C//')
      if [ -z "$STATE_C_NUM" ]; then
        STATE_C_NUM=""
      fi
    fi
    
    # Decide whether to disable
    if [ "$C_NUM" == "999" ]; then
      # C6only mode
      if [ "$C_NAME" == "POLL" ] || [ "$STATE_C_NUM" == "6" ]; then
        echo "0" > "$DISABLE_FILE" 2>/dev/null && ENABLED_COUNT=$((ENABLED_COUNT + 1))
      else
        echo "1" > "$DISABLE_FILE" 2>/dev/null && DISABLED_COUNT=$((DISABLED_COUNT + 1))
      fi
    elif [ "$C_NUM" == "0" ]; then
      # none/poll mode
      if [ "$C_NAME" == "POLL" ]; then
        echo "0" > "$DISABLE_FILE" 2>/dev/null && ENABLED_COUNT=$((ENABLED_COUNT + 1))
      else
        echo "1" > "$DISABLE_FILE" 2>/dev/null && DISABLED_COUNT=$((DISABLED_COUNT + 1))
      fi
    elif [ -z "$STATE_C_NUM" ] || [ "$STATE_C_NUM" == "0" ]; then
      echo "0" > "$DISABLE_FILE" 2>/dev/null && ENABLED_COUNT=$((ENABLED_COUNT + 1))
    elif [ "$STATE_C_NUM" -gt "$C_NUM" ]; then
      echo "1" > "$DISABLE_FILE" 2>/dev/null && DISABLED_COUNT=$((DISABLED_COUNT + 1))
    else
      echo "0" > "$DISABLE_FILE" 2>/dev/null && ENABLED_COUNT=$((ENABLED_COUNT + 1))
    fi
  done
done

if [ $ENABLED_COUNT -gt 0 ] || [ $DISABLED_COUNT -gt 0 ]; then
  if [ "$CORES_SET" == "all" ] || [ -z "$CORES_SET" ]; then
    echo "Set cstate_max=$LEVEL (all cores) - enabled $ENABLED_COUNT, disabled $DISABLED_COUNT"
  else
    echo "Set cstate_max=$LEVEL (cores: $CORES_SET) - enabled $ENABLED_COUNT, disabled $DISABLED_COUNT"
  fi
  exit 0
else
  echo "Error: Failed to set cstate_max"
  exit 1
fi

exit 0


