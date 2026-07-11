# One-rerun Results-Reproduced workflow

This directory maps every active empirical paper plot to exact SemaTune
configurations and provides one end-to-end command. The suite performs **one
fresh rerun per unique configuration**, not the five repetitions used for the
paper. A run shared by multiple plots is executed once.

> **Dedicated-machine warning:** live reproduction changes scheduler, network,
> VM, CPU-frequency, P-state, and C-state controls. Use only the paper-compatible
> dedicated/disposable bare-metal host. Do not run it on a shared machine.

## Commands

Read-only plan, including the number of unique configurations and windows:

```bash
reproduction/reproduce_all.sh --dry-run
```

Regenerate all figures from the checksummed archived histories without running
benchmarks or contacting a model provider:

```bash
reproduction/reproduce_all.sh --archived-only \
  --output-dir results/reproduced_archived
```

Run one fresh repetition of every unique configuration and then plot all seven
figures:

```bash
export GEMINI_API_KEY='your-key'
reproduction/reproduce_all.sh --run \
  --output-dir results/reproduced_one_run
```

Use `--plots 5` or `--plots 2,6` with any command to select plots. Selection is
dependency-aware: shared configurations are still executed only once. A second
invocation resumes completed histories by default. `--rerun-existing` is an
explicit opt-in to add another repetition and is not part of the one-rerun AE
workflow.

The nominal sum of configured benchmark windows is about 23.5 hours. Allow one
to several days for dependency setup, database resets, model calls, workload
startup, and slow benchmark windows. Evaluator active time is a few minutes.

## Plot-to-config map

[`experiment_manifest.json`](experiment_manifest.json) is the source of truth.
Every job records:

- its complete checked-in configuration;
- its fresh result directory;
- the archived history from which the effective configuration was recovered;
- the history checksum;
- every plot that consumes the result.

The extracted JSON preserves the paper run's workload, CPU allocation, window
duration/budget, parameter ranges, metrics, tuner, and model fields. Only
`results_dir` is redirected into the evaluator's output tree, provider-key
fields are set to `null`, and database passwords are left for environment-based
configuration.

| Plot | Paper result | Configurations selected | Reuse |
|---|---|---:|---|
| 1 | End-to-end performance | 91 | Fixed, SemaTune, trimming, MLOS, Bayesian, DQN, and Q-learning across 13 workloads. |
| 2 | Application vs System/IPC/Cache signals | 130 | Reuses Plot 1 App, Fixed, trimming, and MLOS runs. |
| 3 | Dual vs single loop and cost | 78 | Reuses Plot 1; adds single-Reasoning and single-Instant. Accepted-paper cost CSV is reused because the sampled-session source CSV was not retained. |
| 4 | Tuning robustness | 48 | Entirely reuses the Fixed/SemaTune/trimming/MLOS subset of Plot 1. |
| 5 | Parameter-count ablation | 75 | Uses `configs/parameter_count/`; eight-knob and Fixed results reuse common runs. The submitted provider-latency CSV is reused. |
| 6 | Cross-run memory | 21 | Adds 12 Top-1/Top-3 memory configs and reuses nine common Fixed/App/System no-memory results through result aliases. |
| 7 | Motivation | 9 | Entirely reuses Wikipedia signal configs from Plot 2 and TPC-C parameter configs from Plot 5. |

The per-plot counts overlap. The complete seven-plot union is 273 unique
configurations, not the sum of the table.

Examples for locating exact configs:

```bash
# Every config contributing to Plot 6
jq -r '.plots["6"].jobs[] as $id | .jobs[] | select(.id == $id) | .config' \
  reproduction/experiment_manifest.json

# Complete mapping for one configuration
jq '.jobs[] | select(.id == "common:sysbench_oltp_rw_hi_p99:sematune_app")' \
  reproduction/experiment_manifest.json
```

Configuration families are organized as:

```text
reproduction/configs/common/          Plots 1–4 and shared 8-knob runs
reproduction/configs/parameter_count/ Plot 5 and TPC-C portion of Plot 7
reproduction/configs/memory/          Memory-only additions for Plot 6
```

## Outputs and validation

A live output directory contains:

```text
raw/                    Histories in the layout consumed by paper plot scripts
logs/                   One log per unique configuration
run_configs/            Materialized configs with absolute output directories
run_status.json         Resume/progress/failure record
host_state_before.json  Captured host controls
restoration_report.json Restoration verification
plots/                  Paper-style PDFs and CSVs
```

`plot_all.sh` can be called independently:

```bash
reproduction/plot_all.sh \
  --results-dir results/reproduced_one_run/raw \
  --output-dir results/reproduced_one_run/plots \
  --validation fresh
```

Validation modes are intentionally distinct:

- `fresh` checks complete plot/method coverage, finite measurements, workload
  rows, and nonempty PDFs. One rerun cannot reproduce five-run variance.
- `measured` checks regenerated archived figures against the disclosed measured
  history values, including the documented Plots 3/4 paper-history differences.
- `paper` strictly checks the numerical values printed in the accepted paper and
  retains those two documented failures.

The plotting command never modifies checked-in histories or reference figures.

## Maintenance

`build_suite.py` deterministically re-extracts the effective configurations from
the checksummed archived histories. It is a maintainer command, not an evaluator
step:

```bash
python3 reproduction/build_suite.py
python3 reproduction/suite.py validate
```

No provider key is read from a file or written to a configuration, log manifest,
or history by this orchestration layer. Live model clients receive only the
caller's exported environment variable.
