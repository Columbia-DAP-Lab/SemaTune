#!/bin/bash
# Script to run all config files in a directory N times
# Usage: ./run_configs_batch.sh <config_dir> <num_runs> [FILTER]
# Example: ./run_configs_batch.sh config/single_param/sphinx_lo_pwr_wrt_p99 3 --all

set -e  # Exit on error

# Function to draw a progress bar
draw_progress_bar() {
    local current=$1
    local total=$2
    local width=50
    
    # Calculate percentage
    local percent=$((current * 100 / total))
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    
    # Create bar string
    local bar="["
    for ((i=0; i<filled; i++)); do bar+="#"; done
    for ((i=0; i<empty; i++)); do bar+="."; done
    bar+="]"
    
    # Print bar (using \r to overwrite line if needed, but here we print new line for safety in logs)
    # For "always at bottom", we rely on this being the last thing printed
    printf "\nPROGRESS: %s %d%% (%d/%d runs completed)\n" "$bar" "$percent" "$current" "$total"
}

# Function to run main logic
main() {

# Default values
TUNER_FILTER="all"  # Options: all, extended, rest, or specific tuners
CUSTOM_TUNER_LIST=""

# Parse arguments
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --all)
      TUNER_FILTER="all"
      shift
      ;;
    --extended)
      TUNER_FILTER="extended"
      shift
      ;;
    --rest)
      TUNER_FILTER="rest"
      shift
      ;;
    --tuners)
      # Custom list of tuners, e.g., "fixed,llm,reasoning"
      CUSTOM_TUNER_LIST="$2"
      TUNER_FILTER="custom"
      shift 2
      ;;
    --llm-only)
      TUNER_FILTER="llm"
      shift
      ;;
    --fixed-only)
      TUNER_FILTER="fixed"
      shift
      ;;
    --mlos-only)
      TUNER_FILTER="mlos"
      shift
      ;;
    --reasoning-only)
      TUNER_FILTER="reasoning"
      shift
      ;;
    --bayesian-only)
      TUNER_FILTER="bayesian"
      shift
      ;;
    --dqn-only)
      TUNER_FILTER="dqn"
      shift
      ;;
    --qlearning-only)
      TUNER_FILTER="qlearning"
      shift
      ;;
    --mlos-trimming-only)
      TUNER_FILTER="mlos_trimming"
      shift
      ;;
    --llm-dual-only)
      TUNER_FILTER="llm_dual"
      shift
      ;;
    --workload-only)
      TUNER_FILTER="workload_change"
      shift
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}" # restore positional parameters

# Check arguments
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <config_directory> <num_runs> [FILTER]"
    echo ""
    echo "Filter options:"
    echo "  --all                 Run main tuners: llm, mlos, fixed (default)"
    echo "  --extended            Run all tuners: llm, mlos, fixed, reasoning, bayesian, dqn, qlearning"
    echo "  --rest                Run extended tuners only: reasoning, bayesian, dqn, qlearning"
    echo "  --tuners LIST         Run specific tuners in order (comma-separated, e.g., 'fixed,llm')"
    echo "  --llm-only            Run only LLM configs"
    echo "  --fixed-only          Run only fixed configs"
    echo "  --mlos-only           Run only MLOS configs"
    echo "  --reasoning-only      Run only LLM reasoning configs"
    echo "  --bayesian-only       Run only Bayesian configs"
    echo "  --dqn-only            Run only DQN configs"
    echo "  --qlearning-only      Run only Q-Learning configs"
    echo "  --mlos-trimming-only  Run only MLOS Trimming configs"
    echo "  --llm-dual-only       Run only LLM Dual Loop configs"
    echo "  --workload-only       Run only Workload Change configs"
    echo ""
    echo "Example: $0 config/single_param/sphinx_hi_p99 3 --all"
    exit 1
fi

CONFIG_DIR="$1"
NUM_RUNS="$2"

# Validate config directory exists
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Error: Directory '$CONFIG_DIR' does not exist"
    exit 1
fi

# Validate num_runs is a positive integer
if ! [[ "$NUM_RUNS" =~ ^[0-9]+$ ]] || [ "$NUM_RUNS" -lt 1 ]; then
    echo "Error: num_runs must be a positive integer"
    exit 1
fi

# Get absolute path of config directory
CONFIG_DIR=$(realpath "$CONFIG_DIR")

# Find all JSON config files recursively
# Note: We use find to get all files, then we will sort/filter them
ALL_CONFIG_FILES=$(find "$CONFIG_DIR" -name "*.json" -type f | sort)

# Check if any config files were found
if [ -z "$ALL_CONFIG_FILES" ]; then
    echo "Error: No JSON config files found in '$CONFIG_DIR'"
    exit 1
fi

# Filter and Order Configs
FINAL_CONFIG_LIST=""

if [ "$TUNER_FILTER" = "custom" ]; then
    # Parse comma-separated list
    IFS=',' read -ra TUNER_ARRAY <<< "$CUSTOM_TUNER_LIST"
    
    # For each tuner in the custom list (preserving order)
    for tuner in "${TUNER_ARRAY[@]}"; do
        # Find configs matching this tuner
        # We assume tuner name is part of the filename (e.g., _fixed.json)
        # We iterate through ALL_CONFIG_FILES to find matches
        for config in $ALL_CONFIG_FILES; do
            config_name=$(basename "$config")
            # Check if config matches tuner name
            # We look for exact tuner match in filename logic usually used
            # e.g. "fixed" matches "*_fixed.json" or just contains "fixed"
            # Check if config matches tuner name exactly as a suffix (e.g. _llm.json)
            # This prevents "llm" from matching "llm_reasoning"
            if [[ "$config_name" == *"_${tuner}.json" ]]; then
                FINAL_CONFIG_LIST="$FINAL_CONFIG_LIST $config"
            fi
        done
    done
    
    # Remove duplicates (in case a file matches multiple, though unlikely with standard naming)
    # Actually, if user says "fixed,fixed", maybe they want it twice? Assuming unique files for now.
    # To be safe, we can deduplicate if needed, but simple concatenation is fine for "run in order".
    
else
    # Standard filters
    MAIN_TUNERS=("llm" "mlos" "fixed")
    EXTENDED_TUNERS=("reasoning" "bayesian" "dqn" "qlearning")
    
    for config in $ALL_CONFIG_FILES; do
        config_name=$(basename "$config")
        SKIP=0
        
        if [ "$TUNER_FILTER" = "all" ]; then
            SKIP=1
            for tuner in "${MAIN_TUNERS[@]}"; do
                if [[ "$config_name" == *"$tuner"* ]]; then
                    SKIP=0
                    break
                fi
            done
        elif [ "$TUNER_FILTER" = "extended" ]; then
            SKIP=0
        elif [ "$TUNER_FILTER" = "rest" ]; then
            SKIP=1
            for tuner in "${EXTENDED_TUNERS[@]}"; do
                if [[ "$config_name" == *"$tuner"* ]]; then
                    SKIP=0
                    break
                fi
            done
        else
            # Specific tuner filter
            if [[ "$config_name" == *"$TUNER_FILTER"* ]]; then
                SKIP=0
            else
                SKIP=1
            fi
        fi
        
        if [ $SKIP -eq 0 ]; then
            FINAL_CONFIG_LIST="$FINAL_CONFIG_LIST $config"
        fi
    done
fi

# Check if we have any configs left
if [ -z "$FINAL_CONFIG_LIST" ]; then
    echo "Error: No config files matched the filter '$TUNER_FILTER'"
    exit 1
fi

# Count total runs
# Convert space-separated string to array to count
read -r -a CONFIG_ARRAY <<< "$FINAL_CONFIG_LIST"
NUM_CONFIGS=${#CONFIG_ARRAY[@]}
TOTAL_RUNS=$((NUM_CONFIGS * NUM_RUNS))

echo "=========================================="
echo "Batch Config Runner"
echo "=========================================="
echo "Config Directory: $CONFIG_DIR"
echo "Number of runs per config: $NUM_RUNS"
echo "Config files selected: $NUM_CONFIGS"
echo "Total runs: $TOTAL_RUNS"
echo "Tuner filter: $TUNER_FILTER"
if [ "$TUNER_FILTER" = "custom" ]; then
    echo "Custom order: $CUSTOM_TUNER_LIST"
fi
echo "==========================================="
echo ""
echo "🗂️  All results will be saved to a single batch directory with subdirectories for each config"
echo "📝 Logs will be saved within the batch directory"
echo "📦 Old 'results' directory has been archived (if it existed)"
echo ""

# Log file
SUMMARY_LOG="$LOG_DIR/summary.log"

echo "Batch run started at $(date)" > "$SUMMARY_LOG"
echo "Config Directory: $CONFIG_DIR" >> "$SUMMARY_LOG"
echo "Number of runs per config: $NUM_RUNS" >> "$SUMMARY_LOG"
echo "----------------------------------------" >> "$SUMMARY_LOG"
echo "" >> "$SUMMARY_LOG"

# Track progress
CURRENT_RUN=0

# Get unique directories (benchmarks)
CONFIG_DIRS=$(for config in $FINAL_CONFIG_LIST; do dirname "$config"; done | sort -u)

for GROUP_DIR in $CONFIG_DIRS; do
    GROUP_NAME=$(basename "$GROUP_DIR")
    echo "=========================================="
    echo "Benchmark Group: $GROUP_NAME"
    echo "=========================================="
    
    # Get configs for this group, preserving order from FINAL_CONFIG_LIST
    GROUP_CONFIGS=""
    for config in $FINAL_CONFIG_LIST; do
        if [ "$(dirname "$config")" == "$GROUP_DIR" ]; then
            GROUP_CONFIGS="$GROUP_CONFIGS $config"
        fi
    done
    
    # Run configs in this group NUM_RUNS times (interleaved)
    for RUN_NUM in $(seq 1 $NUM_RUNS); do
        for CONFIG_FILE in $GROUP_CONFIGS; do
            CONFIG_NAME=$(basename "$CONFIG_FILE")
            CURRENT_RUN=$((CURRENT_RUN + 1))
            
            echo ""
            echo "[$CURRENT_RUN/$TOTAL_RUNS] Running $CONFIG_NAME (run $RUN_NUM/$NUM_RUNS)..."
            echo "Start time: $(date)"
            
            # Create log file for this run
            RUN_LOG="$LOG_DIR/${CONFIG_NAME%.json}_run${RUN_NUM}.log"
            
            # Record to summary log
            echo "[$CURRENT_RUN/$TOTAL_RUNS] $CONFIG_NAME (run $RUN_NUM/$NUM_RUNS) - Started at $(date)" >> "$SUMMARY_LOG"
            
            # Extract the results_dir from the config file (if set)
            # This extracts the subdirectory structure from the original config
            ORIGINAL_RESULTS_DIR=$(python3 -c "import json; import sys; config = json.load(open('$CONFIG_FILE')); print(config.get('results_dir', 'results'))" 2>/dev/null || echo "results")
            
            # Create a subdirectory path: batch_results_dir/config_subdirs
            # Remove any leading 'results/' from the path to avoid duplication
            CONFIG_SUBDIR=$(echo "$ORIGINAL_RESULTS_DIR" | sed 's|^results/||')
            
            # Combine with batch results directory
            RUN_RESULTS_DIR="$BATCH_RESULTS_DIR/$CONFIG_SUBDIR"
            
            # Create a temporary config file with modified results_dir
            TEMP_CONFIG="/tmp/batch_config_${BATCH_TIMESTAMP}_${CONFIG_NAME%.json}_run${RUN_NUM}.json"
            python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    config = json.load(f)
config['results_dir'] = '$RUN_RESULTS_DIR'
with open('$TEMP_CONFIG', 'w') as f:
    json.dump(config, f, indent=2)
"
            
            echo "  Results will be saved to: $RUN_RESULTS_DIR"
            
            # Run the optimizer (output to log file ONLY, keep stdout clean for progress bar)
            START_TIME=$(date +%s)
            set +e  # Temporarily disable exit on error to capture status
            
            # Use PYTHONPATH to ensure local code is used, and set OS_PARAM_TUNING_ROOT explicitly
            REPO_ROOT=$(pwd)
            
            # NOTE: We redirect stdout/stderr to the log file to keep the console clean for the progress bar
            echo "  > Executing optimizer... (logs in $RUN_LOG)"
            touch "$RUN_LOG"
            KILLED_STALE=0
            if [[ "$CONFIG_NAME" == *"mlos"* ]]; then
                # MLOS only: run in background and kill if log not updated for 60s (MLOS can hang)
                ( sudo PYTHONPATH="$REPO_ROOT/src" OS_PARAM_TUNING_ROOT="$REPO_ROOT" python3 -m barebones_optimizer.main --config "$TEMP_CONFIG" >> "$RUN_LOG" 2>&1 ) &
                OPT_PID=$!
                LOG_STALE_SEC=60
                CHECK_INTERVAL=15
                while kill -0 "$OPT_PID" 2>/dev/null; do
                    sleep "$CHECK_INTERVAL"
                    if ! kill -0 "$OPT_PID" 2>/dev/null; then break; fi
                    now=$(date +%s)
                    log_mtime=$(stat -c %Y "$RUN_LOG" 2>/dev/null || echo 0)
                    if [ "$log_mtime" -gt 0 ] && [ $((now - log_mtime)) -ge "$LOG_STALE_SEC" ]; then
                        echo "  ⚠ [MLOS] Log not updated for ${LOG_STALE_SEC}s, sending SIGKILL (PID $OPT_PID) and moving on"
                        echo "[$(date)] BATCH RUNNER: [MLOS] Log not updated for ${LOG_STALE_SEC}s, sending SIGKILL (PID $OPT_PID) and moving to next run." >> "$RUN_LOG"
                        kill -9 "$OPT_PID" 2>/dev/null || true
                        pkill -9 -P "$OPT_PID" 2>/dev/null || true
                        KILLED_STALE=1
                        break
                    fi
                done
                wait "$OPT_PID" 2>/dev/null
                EXIT_CODE=$?
            else
                # All other tuners: run synchronously (no timeout)
                sudo PYTHONPATH="$REPO_ROOT/src" OS_PARAM_TUNING_ROOT="$REPO_ROOT" python3 -m barebones_optimizer.main --config "$TEMP_CONFIG" >> "$RUN_LOG" 2>&1
                EXIT_CODE=$?
            fi
            set -e  # Re-enable exit on error

            # Clean up temporary config
            rm -f "$TEMP_CONFIG"

            END_TIME=$(date +%s)
            DURATION=$((END_TIME - START_TIME))
            
            if [ $KILLED_STALE -eq 1 ]; then
                echo "  ✗ KILLED (log not updated for ${LOG_STALE_SEC}s) after ${DURATION}s"
                echo "  ✗ FAILED (killed, log stale ${LOG_STALE_SEC}s) after ${DURATION}s - Ended at $(date)" >> "$SUMMARY_LOG"
                echo "    Last 5 lines of log:"
                tail -n 5 "$RUN_LOG" | sed 's/^/    /'
            elif [ $EXIT_CODE -eq 0 ]; then
                echo "  ✓ Completed successfully in ${DURATION}s"
                echo "  ✓ Completed successfully in ${DURATION}s - Ended at $(date)" >> "$SUMMARY_LOG"
            else
                if grep -qE "LLMHTTPStatusError|LLMTimeoutExhaustedError|Fatal LLM error|Fatal LLM HTTP status error|LLM request timed out .* consecutive times|LLM API returned HTTP" "$RUN_LOG"; then
                    echo "  ✗ KILLED due to LLM tuner failure (HTTP error or timeout exhaustion) after ${DURATION}s (exit code: $EXIT_CODE)"
                    echo "  ✗ FAILED (killed: LLM tuner fatal error/timeouts) after ${DURATION}s (exit code: $EXIT_CODE) - Ended at $(date)" >> "$SUMMARY_LOG"
                else
                    echo "  ✗ FAILED after ${DURATION}s (exit code: $EXIT_CODE)"
                    echo "  ✗ FAILED after ${DURATION}s (exit code: $EXIT_CODE) - Ended at $(date)" >> "$SUMMARY_LOG"
                fi
                # Show last few lines of log on failure
                echo "    Last 5 lines of log:"
                tail -n 5 "$RUN_LOG" | sed 's/^/    /'
            fi
            
            # Draw progress bar at the bottom
            draw_progress_bar "$CURRENT_RUN" "$TOTAL_RUNS"
            
            echo ""
        done
    done
    
    echo "Completed all runs for group $GROUP_NAME"
    echo ""
done

echo "=========================================="
echo "Batch run completed!"
echo "=========================================="
echo "Results saved to: $BATCH_RESULTS_DIR"
echo "Logs saved to: $LOG_DIR"
echo "Summary log: $SUMMARY_LOG"
echo ""

# Print summary statistics
echo "Summary:" >> "$SUMMARY_LOG"
echo "----------------------------------------" >> "$SUMMARY_LOG"
echo "Batch run completed at $(date)" >> "$SUMMARY_LOG"
echo "Total runs: $TOTAL_RUNS" >> "$SUMMARY_LOG"
echo "Success count: $(grep -c '✓ Completed' "$SUMMARY_LOG" || echo 0)" >> "$SUMMARY_LOG"
echo "Failure count: $(grep -c '✗ FAILED' "$SUMMARY_LOG" || echo 0)" >> "$SUMMARY_LOG"

cat "$SUMMARY_LOG"

}  # End of main function

# Archive old 'results' directory if it exists (ONCE at the start)
if [ -d "results" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    ARCHIVE_DIR="results_${TIMESTAMP}"
    echo "📦 Archiving existing 'results' directory to: $ARCHIVE_DIR"
    mv results "$ARCHIVE_DIR"
    echo "   ✓ Archived successfully"
    echo ""
fi

# Create shared timestamped results directory for this batch run
# Name: results_<config_dir_with_underscores>_<timestamp>
CONFIG_DIR_FOR_NAME=""
for arg in "$@"; do
  case "$arg" in
    --*) ;;
    *) CONFIG_DIR_FOR_NAME="$arg"; break ;;
  esac
done
CONFIG_DIR_SLUG=$(echo "${CONFIG_DIR_FOR_NAME:-config}" | tr '/' '_')
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BATCH_RESULTS_DIR="results_${CONFIG_DIR_SLUG}_${TIMESTAMP}"

# Create batch results directory with logs subdirectory
mkdir -p "$BATCH_RESULTS_DIR/logs"
LOG_DIR="$BATCH_RESULTS_DIR/logs"

echo "📁 Created batch results directory: $BATCH_RESULTS_DIR"
echo "📝 Logs will be saved to: $LOG_DIR"
echo ""

# Export for use in main function
export BATCH_RESULTS_DIR
export BATCH_TIMESTAMP=$TIMESTAMP

# Run main function and pipe output to both console and master log file
# Note: We pipe to tee, so the progress bar will also be in the master log, which is fine
main "$@" 2>&1 | tee "$LOG_DIR/master.log"
MAIN_EXIT=${PIPESTATUS[0]}

# Post-batch plotting: run plot_comparison and plot_violin for each benchmark in the batch
if [ -d "$BATCH_RESULTS_DIR" ]; then
    PLOTS_DIR="$BATCH_RESULTS_DIR/plots"
    mkdir -p "$PLOTS_DIR"
    echo ""
    echo "=========================================="
    echo "Post-batch plotting"
    echo "=========================================="
    for BENCH_DIR in "$BATCH_RESULTS_DIR"/*/; do
        BENCH_NAME=$(basename "$BENCH_DIR")
        [ "$BENCH_NAME" = "logs" ] && continue
        [ "$BENCH_NAME" = "plots" ] && continue
        HISTORIES=("$BENCH_DIR"*/optimization_history_*.json)
        [ ! -f "${HISTORIES[0]}" ] && continue
        echo "  Plotting benchmark: $BENCH_NAME"
        python3 scripts/plot_comparison.py -f "${HISTORIES[@]}" -p min_granularity_ns -o "$PLOTS_DIR/${BENCH_NAME}_comparison.png" 2>/dev/null || true
        python3 scripts/plot_violin.py -f "${HISTORIES[@]}" -o "$PLOTS_DIR/${BENCH_NAME}_violin.png" 2>/dev/null || true
    done
    echo "  Plots saved to: $PLOTS_DIR"
    echo "=========================================="
    echo ""
fi

# Capture the exit code from main function
exit "$MAIN_EXIT"
