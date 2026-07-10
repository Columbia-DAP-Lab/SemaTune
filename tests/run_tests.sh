#!/bin/bash
# Test runner script for parameter_manager tests
# Usage: sudo ./tests/run_tests.sh [test_name]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then 
    echo "Error: This script must be run with sudo"
    echo "Usage: sudo $0 [test_name]"
    exit 1
fi

# Check if pytest is installed
if ! python3 -c "import pytest" 2>/dev/null; then
    echo "pytest not found. Installing..."
    pip3 install pytest
fi

# Run tests
if [ $# -eq 0 ]; then
    echo "Running all parameter_manager tests..."
    python3 -m pytest tests/test_parameter_manager.py -v
else
    echo "Running specific test: $1"
    python3 -m pytest tests/test_parameter_manager.py -v -k "$1"
fi

