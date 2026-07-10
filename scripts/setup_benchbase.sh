#!/bin/bash

PROJECT_TOP="$(git rev-parse --show-toplevel)"

# BenchBase directory path
BENCHBASE_DIR="$PROJECT_TOP/deps/benchbase"  # Replace this with the actual path
RESULTS_DIR="$PROJECT_TOP/results"

cd "$BENCHBASE_DIR"

# Check if --old flag is passed
if [ "$1" = "--old" ]; then
    echo "Checking out old version of BenchBase (54d30feb)..."
    git checkout 54d30feb
else
    echo "Checking out main branch of BenchBase..."
    git checkout main
fi

# Build BenchBase for PostgreSQL
export BENCHBASE_PROFILE=postgres
./mvnw clean package -P postgres -DskipTests

# Extract BenchBase PostgreSQL package
cd "$BENCHBASE_DIR/target"
tar xvzf benchbase-postgres.tgz
