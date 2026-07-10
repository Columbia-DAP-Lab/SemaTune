#!/bin/bash
# Script to pin PostgreSQL processes to specific CPU cores
# Usage: pin-postgres.sh <core(s)>
# Example: pin-postgres.sh 4          # Pin to core 4
#          pin-postgres.sh 4,5,6      # Pin to cores 4, 5, and 6
#          pin-postgres.sh 4-8        # Pin to cores 4 through 8

if [ $# -eq 0 ]; then
    echo "Error: No CPU cores specified"
    echo "Usage: $0 <core(s)>"
    echo "Examples: $0 4         # Pin to core 4"
    echo "          $0 4,5,6     # Pin to cores 4, 5, and 6"
    echo "          $0 4-8       # Pin to cores 4 through 8"
    exit 1
fi

CORES="$1"

echo "Pinning PostgreSQL processes to CPU core(s): $CORES"

for pid in $(ps -C postgres -o pid=); do
    echo "Pinning PostgreSQL process $pid to core(s) $CORES"
    sudo taskset -cp "$CORES" "$pid"
done

echo "PostgreSQL processes pinned to core(s): $CORES"
