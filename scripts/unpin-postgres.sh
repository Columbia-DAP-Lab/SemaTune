#!/bin/bash
# Script to reset PostgreSQL process CPU affinity
# This script resets PostgreSQL processes to use all available CPU cores

echo "Resetting PostgreSQL process CPU affinity to all cores"

for pid in $(ps -C postgres -o pid=); do
    all_cores="0-$(( $(nproc --all) - 1 ))"
    echo "Resetting PostgreSQL process $pid to use all cores: $all_cores"
    sudo taskset -cp "$all_cores" "$pid"
done

echo "PostgreSQL process CPU affinity reset to all cores"
