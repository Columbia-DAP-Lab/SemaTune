#!/bin/bash
#
# Run all sysbench optimization configs
# Continues on failure (doesn't stop)
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Set PYTHONPATH to include src directory for proper module resolution
export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"

# List of all config files to run
CONFIGS=(
    "config/sysbench_cpu_1param_cstate_fixed.json"
    "config/sysbench_cpu_1param_llm.json"
    "config/sysbench_cpu_1param_mlos.json"
    "config/sysbench_cpu_1param_cstate_mlos.json"
    "config/sysbench_cpu_2param_llm.json"
    "config/sysbench_cpu_2param_mlos.json"
    "config/sysbench_oltp_rw_1param_cstate_fixed.json"
    "config/sysbench_oltp_rw_1param_llm.json"
    "config/sysbench_oltp_rw_1param_mlos.json"
    "config/sysbench_oltp_rw_1param_cstate_llm.json"
    "config/sysbench_oltp_rw_1param_cstate_mlos.json"
    "config/sysbench_oltp_rw_2param_fixed.json"
    "config/sysbench_oltp_rw_2param_llm.json"
    "config/sysbench_oltp_rw_2param_mlos.json"
)

# Optional: include the copy file if it exists
if [ -f "config/sysbench_oltp_rw_1param_llm copy.json" ]; then
    CONFIGS+=("config/sysbench_oltp_rw_1param_llm copy.json")
fi

# Track results
SUCCESSFUL=()
FAILED=()
TOTAL=${#CONFIGS[@]}
CURRENT=0

echo "=========================================="
echo "Running $TOTAL sysbench optimization configs"
echo "=========================================="
echo ""

# Run each config
for config in "${CONFIGS[@]}"; do
    CURRENT=$((CURRENT + 1))
    config_name=$(basename "$config" .json)
    
    echo "=========================================="
    echo "[$CURRENT/$TOTAL] Running: $config_name"
    echo "=========================================="
    echo "Config: $config"
    echo ""
    
    # Run the optimizer (continue on failure)
    # Set PYTHONPATH explicitly and ensure we run from project root
    # Change to project root directory so git commands work
    # Set OS_PARAM_TUNING_ROOT so get_repo_root() works even with sudo
    PYTHONPATH_VAL="$PROJECT_ROOT/src:${PYTHONPATH:-}"
    if (cd "$PROJECT_ROOT" && sudo env PYTHONPATH="$PYTHONPATH_VAL" OS_PARAM_TUNING_ROOT="$PROJECT_ROOT" python3 -m optimizer.main --config "$config"); then
        SUCCESSFUL+=("$config_name")
        echo ""
        echo "✓ SUCCESS: $config_name"
    else
        FAILED+=("$config_name")
        echo ""
        echo "✗ FAILED: $config_name (continuing...)"
    fi
    
    echo ""
    echo "----------------------------------------"
    echo ""
done

# Print summary
echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo "Total configs: $TOTAL"
echo "Successful: ${#SUCCESSFUL[@]}"
echo "Failed: ${#FAILED[@]}"
echo ""

if [ ${#SUCCESSFUL[@]} -gt 0 ]; then
    echo "Successful configs:"
    for config in "${SUCCESSFUL[@]}"; do
        echo "  ✓ $config"
    done
    echo ""
fi

if [ ${#FAILED[@]} -gt 0 ]; then
    echo "Failed configs:"
    for config in "${FAILED[@]}"; do
        echo "  ✗ $config"
    done
    echo ""
    exit 1
fi

echo "All configs completed successfully!"
exit 0

