# Agent Guide: OS Parameter Tuning Framework

## Overview
This document serves as a "context bootstrap" for future AI agents working on this codebase. It outlines the current state of implementation, key architectural decisions, and supported features as of February 2026.

## 1. Codebase Structure
The project is located at `/mydata/os-param-tuning/`. Key directories:

- `src/barebones_optimizer/`: Core Python package.
    - `main.py`: Entry point. Handles signal setup and orchestrates the run.
    - `config.py`: Defines `SimpleConfig` dataclass. The **single source of truth** for all configuration fields.
    - `optimizer.py`: `SimpleOptimizer` (Single Loop) implementation.
    - `dual_loop_optimizer.py`: `SimpleDualLoopOptimizer` (Dual Loop) implementation.
    - `tuners/`: Contains Tuner implementations (`llm.py`, `mlos_core_tuner.py`, `random_tuner.py`, `llm_trimming.py`).
    - `benchmarks/`: Benchmark wrappers (`benchbase.py` for TPCC/YCSB, `tailbench.py` for Tailbench apps).
- `config/`: Configuration files.
    - `single_param/`: Configurations tuning a single OS parameter (or small set) for specific benchmarks. Organized by `<benchmark>_<scenario>/`.
    - `full_param/`: Configurations tuning the full set of OS parameters.
- `scripts/`: Helper scripts.
    - `generate_missing_configs.py`: Generates variants (IPC, Indirect, Dual Loop, etc.) from base configs.
    - `generate_full_param_from_single.py`: Propagates single-param configs to full-param versions.
    - `generate_workload_configs.py`: Generates workload change variants.
- `run_configs_batch.sh`: Main script for batch execution of experiments.

## 2. Key Features & Implementation Status

### Optimization Strategies (`tuner_type` / `tuning_mode`)
1.  **Single Loop (Default)**:
    - Iterative optimization using a single Tuner (LLM, MLOS, Random).
    - `SimpleOptimizer` class.
    - Config: `"tuner_type": "llm"` or `"mlos"`.

2.  **Dual Loop (Actor-Speculator)**:
    - **Actor**: Reasoning Agent (e.g., `gemini-2.5-flash`) that plans high-level moves.
    - **Speculator**: Quick Agent (e.g., `gemini-2.5-flash-lite`) that explores rapidly within window.
    - `SimpleDualLoopOptimizer` class.
    - Config: `"tuner_type": "llm"`, `"tuning_mode": "outside-of-window"` (typically).

3.  **Trimming Phase**:
    - **Purpose**: Narrow down parameter ranges before main optimization.
    - **Strategies** (`trimming_strategy` in `SimpleConfig`):
        - `"single_loop"`: Actor/Trimmer model runs sequentially.
        - `"dual_loop"`: Speculator explores, Actor observes and trims.
    - Config: `"trimming_enabled": true`, `"trimming_cycles": 5`.

4.  **Indirect Optimization**:
    - **Purpose**: Optimize for a target (e.g., P99 latency) by aligning "Metric Signatures" (IPC, cache misses, power) rather than just the noisy target metric.
    - Config: `"use_indirect_optimization": true`, `"llm_additional_metrics": [...]`.

### Workload Changes
- **Purpose**: Simulate non-stationary environments.
- **Mechanism**: `BenchBaseBenchmark` changes workload parameters (e.g., rate) periodically.
- **Config**: `"workload_change_type": "cyclic_halving"`, `"workload_change_interval": 5`.

### Optimization Gist
- **Purpose**: Persist context between runs.
- **Mechanism**: At the end of a run, the LLM generates a summary ("gist") which can be fed into the next run via `previous_run_gist`.

## 3. Configuration Management
- **Schema**: Defined in `src/barebones_optimizer/config.py` (`SimpleConfig`).
- **Loading**: `SimpleConfig.load(path)` parses JSON config files.
- **Generation**: Use `scripts/generate_missing_configs.py` to create derived configurations from base templates.

## 4. Supported Benchmarks
- **BenchBase**: `src/barebones_optimizer/benchmarks/benchbase.py` (Supports TPCC, YCSB, SiBench).
- **Tailbench**: `src/barebones_optimizer/benchmarks/tailbench.py` (Supports Xapian, Masstree, Silo, Sphinx, Moses, etc.).
- **Sysbench**: Legacy support in `src/barebones_optimizer/benchmarks/sysbench.py`.

## 5. How to Run
- **Single Run**:
  ```bash
  sudo python3 src/barebones_optimizer/main.py -c /path/to/config.json
  ```
- **Batch Run**:
  ```bash
  ./run_configs_batch.sh config/single_param 3 --all
  ```

## 6. Developing / Extending
- **Adding a Config Field**: Add to `SimpleConfig` in `config.py`.
- **Adding a Benchmark**: Inherit from `BenchmarkInterface` in `benchmark.py` and implement `execute_window`.
- **Modifying Tuner Logic**: Edit `src/barebones_optimizer/tuners/llm.py` (for prompt engineering) or `mlos_core_tuner.py`.

## 7. Known Issues / Gotchas
- **Permissions**: `sudo` is often required for OS parameter tuning and `perf` access.
- **XML Parsing**: `BenchBaseBenchmark` relies on specific XML structures for config modification.
- **Path Resolution**: Use absolute paths in configs to avoid ambiguity.

## 8. Advisor-Requested Experiments (Generic Commands)

To run the "Metric Signature" and "IPC Only" experiments, use the following generic command templates. Replace `<benchmark>` (e.g., `sphinx`) and `<scenario>` (e.g., `hi_p99`) with the appropriate values.

1.  **Metric Signature Experiment** (Indirect Optimization):
    ```bash
    sudo python3 src/barebones_optimizer/main.py -c config/single_param/<benchmark>_<scenario>/tailbench_<benchmark>_config_signature.json
    ```

2.  **IPC Only Experiment**:
    ```bash
    sudo python3 src/barebones_optimizer/main.py -c config/single_param/<benchmark>_<scenario>/tailbench_<benchmark>_config_ipc.json
    ```

3.  **Baseline Experiment** (Comparison):
    ```bash
    sudo python3 src/barebones_optimizer/main.py -c config/single_param/<benchmark>_<scenario>/tailbench_<benchmark>_config_llm.json
    ```
