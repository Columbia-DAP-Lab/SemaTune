# Batch Results Directory Structure

After running the updated `run_configs_batch.sh` script, the results will be organized as follows:

## Example: Running 10 configs in one batch

```bash
./run_configs_batch.sh config/single_param/sphinx_lo_pwr_wrt_p99 1
```

This will create:

```
results_20251124_171924/                    # Single timestamped batch directory
├── logs/                                    # Batch-level logs
│   ├── master.log                          # Complete output from batch run
│   ├── summary.log                         # Summary of all runs
│   ├── tailbench_sphinx_config_llm_run1.log
│   ├── tailbench_sphinx_config_fixed_run1.log
│   └── tailbench_sphinx_config_mlos_run1.log
├── sphinx_lo_pwr_wrt_p99/                  # Subdirectory for this benchmark scenario
│   ├── llm/                                # Config-specific subdirectory (from config's results_dir)
│   │   ├── optimization_history_tailbench_20251124_171930.json
│   │   ├── window_1_perf_stat.txt
│   │   ├── window_2_perf_stat.txt
│   │   └── ...
│   ├── fixed/                              # Another config's results
│   │   ├── optimization_history_tailbench_20251124_172045.json
│   │   ├── window_1_perf_stat.txt
│   │   └── ...
│   └── mlos/                               # Yet another config's results
│       ├── optimization_history_tailbench_20251124_172200.json
│       ├── window_1_perf_stat.txt
│       └── ...
└── <other_benchmark_dirs>/                 # If you ran configs from multiple benchmarks
    └── ...
```

## Key Changes

1. **Single batch directory**: All results from one script invocation go into `results_YYYYMMDD_HHMMSS/`

2. **Preserved subdirectory structure**: Each config's original `results_dir` path (e.g., `results/sphinx_lo_pwr_wrt_p99/llm/`) is preserved as a subdirectory within the batch directory

3. **Easy comparison**: All results from one batch run are in one place, making it easy to compare across different configs

4. **No timestamp duplication**: The timestamp is only at the batch level, not for each individual run

## How It Works

The script:
1. Creates a timestamped batch results directory at script start
2. For each config file:
   - Extracts the `results_dir` from the config JSON
   - Strips the `results/` prefix to avoid duplication
   - Creates a modified config with `results_dir` = `results_YYYY.../config_subdir/`
   - Runs the optimizer with this modified config
3. All results end up in the single batch directory
