### Experiments Quickstart

Short guide for running optimizations and plotting results in this repo.

---

### 1. Run a single optimizer (no batch)

From the repo root (`/mydata/os-param-tuning`), run:

```bash
sudo PYTHONPATH="$PWD/src" OS_PARAM_TUNING_ROOT="$PWD" \
  python3 -m barebones_optimizer.main --config <CONFIG_PATH>
```

Examples (sysbench CPU P99 / throughput-style configs):

```bash
sudo PYTHONPATH="$PWD/src" OS_PARAM_TUNING_ROOT="$PWD" \
  python3 -m barebones_optimizer.main --config config/single_param/sysbench_cpu_p99/sysbench_cpu_config_fixed.json

sudo PYTHONPATH="$PWD/src" OS_PARAM_TUNING_ROOT="$PWD" \
  python3 -m barebones_optimizer.main --config config/single_param/sysbench_cpu_p99/sysbench_cpu_config_bayesian.json
```

Results go under `results/<scenario>/<tuner>/optimization_history_*.json` plus window `perf_stat` files.

---

### 2. Run many configs in a batch

Use `run_configs_batch.sh` to run all configs in a directory `N` times, with optional tuner filters.

- **All main tuners except LLM reasoning** for `sysbench_cpu_p99`:

```bash
./run_configs_batch.sh config/single_param/sysbench_cpu_p99 1 \
  --tuners llm,mlos,fixed,bayesian,dqn,qlearning
```

- **Only `fixed`**:

```bash
./run_configs_batch.sh config/single_param/sysbench_cpu_p99 1 --fixed-only
```

The script:

- Archives any existing `results/` to `results_YYYYMMDD_HHMMSS/`
- Creates a new batch dir `results_YYYYMMDD_HHMMSS/`
- Preserves each config’s `results_dir` under that batch directory

You can point plotting scripts either at the live `results/` tree or at a specific `results_YYYYMMDD_HHMMSS/` batch.

---

### 3. Plot cross-benchmark relative improvements

Use `plot_all_benchmark_results_relative.py` to compare tuners vs `fixed` across all benchmarks.

From repo root:

```bash
python3 plot_all_benchmark_results_relative.py results
```

Or for a specific batch directory:

```bash
python3 plot_all_benchmark_results_relative.py results_YYYYMMDD_HHMMSS
```

This discovers benchmarks and tuners automatically and writes:

- `relative_objective_improvement.png`
- `relative_improvement_throughput.png`
- `relative_improvement_p99_latency.png`
- `relative_improvement_power.png`
- `relative_improvement_avg_latency.png`

---

### 4. Plot parameter trajectories (per benchmark)

Use `scripts/plot_comparison.py` to plot how parameters (or metrics) evolve over tuning iterations for each tuner.

Example: sysbench CPU throughput, `min_granularity_ns`:

```bash
python3 scripts/plot_comparison.py \
  -d results/sysbench_cpu_tput/fixed \
  -d results/sysbench_cpu_tput/bayesian \
  -d results/sysbench_cpu_tput/dqn \
  -d results/sysbench_cpu_tput/qlearning \
  -p min_granularity_ns \
  -o sysbench_cpu_tput_min_granularity_comparison.png
```

Key flags:

- `-d / --directory`: directory with `optimization_history_*.json`
- `-p / --parameter`: parameter or metric name (e.g., `min_granularity_ns`, `throughput`)
- `-o / --output`: output PNG path
- `--start` / `--end`: optional iteration range filters

---

### 5. Plot metric distributions with violins (per benchmark)

Use `scripts/plot_violin.py` to compare metric distributions (e.g., throughput or P99 latency) across tuners.

Histories are usually stored under tuner subdirectories, so use `-f` with a glob:

```bash
python3 scripts/plot_violin.py \
  -f results/sysbench_cpu_tput/*/optimization_history_sysbench_cpu_*.json \
  -m throughput \
  -o sysbench_cpu_tput_violin_throughput.png
```

Examples:

- Throughput:

```bash
python3 scripts/plot_violin.py \
  -f results/sysbench_cpu_tput/*/optimization_history_sysbench_cpu_*.json \
  -m throughput \
  -o sysbench_cpu_tput_violin_throughput.png
```

- P99 latency:

```bash
python3 scripts/plot_violin.py \
  -f results/sysbench_cpu_tput/*/optimization_history_sysbench_cpu_*.json \
  -m p_99_latency \
  -o sysbench_cpu_tput_violin_p99.png
```

Key flags:

- `-f / --files`: list of history JSON files (shell globs expand)
- `-m / --metric`: metric name (`throughput`, `goodput`, `p_99_latency`, `latency_p95`, etc.)
- `-o / --output`: output PNG path
- `--trim`: drop first and last values per run (optional)
- `--start` / `--end`: iteration filtering (optional)

---

### 6. Generate config directories

Use `scripts/generate_full_param_from_single.py` to derive new config sets from an existing base directory (e.g. `config/single_param`). It copies every JSON config, replacing `parameter_ranges`, `parameters_to_tune`, and cleaning up `fixed_parameters` (removes any parameter that is now tunable).

**From a JSON params file:**

```bash
python3 scripts/generate_full_param_from_single.py \
  --base config/single_param \
  --output config/full_param \
  --params config/params_full.json
```

`params_full.json` structure:

```json
{
  "parameter_ranges": {
    "min_granularity_ns": [100000, 100000000],
    "cstate_max": ["POLL", "C6", "C3", "C1E", "C1"],
    ...
  }
}
```

(`parameters_to_tune` is inferred from the keys if omitted.)

**Inline parameters:**

```bash
python3 scripts/generate_full_param_from_single.py \
  --base config/single_param \
  --output config/two_param \
  --param "min_granularity_ns=[100000,100000000]" \
  --param "latency_ns=[100000,100000000]"
```

**Filter scenarios** with `--include` / `--exclude` globs:

```bash
python3 scripts/generate_full_param_from_single.py \
  --base config/single_param \
  --output config/two_param_lo \
  --params params_two.json \
  --include "*lo_pwr*"
```

---

### 7. Generate all plots for one benchmark (e.g. YCSB hi_p99)

Use `scripts/plot_ycsb_hi_p99_results.sh` to produce every plot type for a single benchmark from existing results:

```bash
./scripts/plot_ycsb_hi_p99_results.sh results plots/ycsb_hi_p99
```

- **Arg 1**: results directory (default: `results`)
- **Arg 2**: output directory for PNGs (default: `plots/ycsb_hi_p99`)

This writes:

1. **Relative improvement** (section 3): `relative_objective_improvement.png`, `relative_improvement_throughput.png`, `relative_improvement_p99_latency.png`, `relative_improvement_power.png`, `relative_improvement_avg_latency.png`
2. **Parameter trajectory** (section 4): `ycsb_hi_p99_min_granularity_comparison.png`
3. **Violins** (section 5): `ycsb_hi_p99_violin_throughput.png`, `ycsb_hi_p99_violin_p99.png`

---

### 8. Full-parameter and Parallel Coordinates Plots

New scripts support plotting all parameters at once, visualizing constraint violations, and exploring high-dimensional spaces.

**A. Vertical Stack Comparison (all parameters):**

Plots every tuned parameter in stacked subplots (one column). Use `--all-params`.
Add `--show-violations` to mark constraint violations with `x`.
Add `--violations-output FILE` to plot cumulative violations over time.

```bash
python3 scripts/plot_comparison.py \
  -d results/sysbench_cpu_tput \
  -o plots/sysbench_cpu_comparison.png \
  --all-params \
  --show-violations \
  --violations-output plots/sysbench_cpu_violations.png
```

**B. Violin Plots with Violation Counts:**

Add `--show-violations` to append violation counts to x-axis labels.

```bash
python3 scripts/plot_violin.py \
  -d results/sysbench_cpu_tput \
  -o plots/sysbench_cpu_violin.png \
  --show-violations
```

**C. Parallel Coordinates Plot:**

Visualizes the relationship between all parameters and the metric. Each line is a trial, colored by tuner type.

```bash
python3 scripts/plot_parallel_coordinates.py \
  -d results/sysbench_cpu_tput \
  -o plots/sysbench_cpu_parallel.png \
  -t "Parallel Coordinates - Sysbench CPU"
```

**Batch Generation:**

Use `scripts/plot_all_results_dirs.sh` to generate these plots for all benchmark directories in a results folder. It automatically uses `--all-params` and `--show-violations`.

```bash
./scripts/plot_all_results_dirs.sh all_results/results_config_full_param_20260205_233742
```
