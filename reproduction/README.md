# Results Reproduced workflow

## TL;DR: scoped C1–C4 evaluation

The recommended evaluator workflow targets four headline claims within the
available evaluation time. It regenerates the paper evidence from the archived
five-repeat histories, then performs one fresh repetition on Silo, TPC-C, and
Sysbench.

```bash
reproduction/reproduce_claims.sh --dry-run

export GEMINI_API_KEY='<provided-key>'
mkdir -p results
RUN_DIR="$(mktemp -d -p "$PWD/results" reproduced_core_real_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --output-dir "$RUN_DIR"
echo "Results: $RUN_DIR"
```

The API key is provided on the preconfigured CloudLab machine. The dry run is
read-only and should report 21 unique configurations, 15 LLM configurations,
and 1,050 benchmark windows. At five seconds per window, the nominal benchmark
time is 1.46 hours; workload startup, database resets, and hosted-model calls
add overhead. The workflow is designed for less than ten hours on the supplied
host, but actual time remains host- and provider-dependent.

### Claims

| ID | Claim | Archived evidence | Fresh observation |
|---|---|---|---|
| C1 | SemaTune improves stable-phase performance over Default Parameters. | Submitted 72.49%; measured regeneration 73.01% over 13 workloads. | SemaTune App versus Fixed stable aggregate over the three-workload subset. |
| C2 | SemaTune outperforms MLOS. | Submitted 153.3%; measured regeneration 154.09%. | SemaTune App versus MLOS aggregate ordering and ratio. |
| C3 | SemaTune using only system metrics outperforms MLOS using application metrics. | Submitted 93.7%; measured regeneration 100.19%, preserving the conclusion. | System-metric SemaTune versus application-metric MLOS. |
| C4 | SemaTune remains effective as the action space grows to 41 knobs. | Regenerates the complete parameter-scaling plot and validates the submitted latency CSV. | SemaTune at 2, 8, 16, and 41 knobs; the eight-knob runs are reused from C1/C2. |

The paper values aggregate five repetitions over 13 workloads. The fresh run
uses one repetition over three workloads. LLM decisions and host measurements
are nondeterministic, so reviewers should validate whether the observation is
preserved rather than require exact percentage equality.

`claim_report.md` and `claim_report.json` report two independent fields:

- `COMPLETE`: every required history and output passed structural validation.
- `CONSISTENT` or `DIVERGENT`: the single fresh aggregate does or does not have
  the direction stated by the claim.

A divergent stochastic observation is disclosed for reviewer interpretation;
it is never silently replaced with the paper value. Other paper experiments
remain in the complete workflow but are outside this time-bounded claim set.

### Optional full C1–C4 workflow

To run the canonical full-workload dependencies for paper Plots 1, 2, and 5,
use `--full` with another new output directory:

```bash
export GEMINI_API_KEY='<provided-key>'
mkdir -p results
FULL_DIR="$(mktemp -d -p "$PWD/results" reproduced_claims_full_real_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --full --output-dir "$FULL_DIR"
echo "Results: $FULL_DIR"
```

Check the plan first with `reproduction/reproduce_claims.sh --dry-run --full`.
It selects 232 unique configurations and 13,430 benchmark windows. The nominal
window time alone is 18.65 hours, and the complete run can take one to several
days. Fresh histories and plots are written under `FULL_DIR/full/`; archived
comparison plots remain under `FULL_DIR/archived/plots/`. This canonical plot
selection includes supporting methods required by Plots 1, 2, and 5, but does
not select the dual-versus-single cost experiment.

### Runs, phases, and resume

[`claim_manifest.json`](claim_manifest.json) selects exactly 21 unique jobs:

- Fixed, MLOS App, SemaTune App, and SemaTune System on each workload;
- additional 2-, 16-, and 41-knob SemaTune App runs on each workload; and
- the shared SemaTune App runs as the eight-knob C4 point.

SemaTune runs 30 tuning and 20 stable windows. MLOS preserves its paper
behavior and tunes for all 50 windows. Fixed holds one static configuration for
50 observations. For a uniform comparison, every method is summarized over
windows 1–30 and 31–50.

Repeating the same command resumes automatically. A result is skipped only when
it has an accepted completion marker, exactly 50 numbered measurement windows,
the correct phase markers, finite primary metrics and rewards, applied
parameters, and—where applicable—complete dual-loop metadata. Truncated,
malformed, non-finite, or interrupted histories remain available for diagnosis
but are not reused. `--rerun-existing` explicitly repeats valid jobs.

### Outputs and inspection

```text
results/reproduced_core/
├── claim_report.md              Human-readable C1–C4 result
├── claim_report.json            Machine-readable factors and statuses
├── archived/plots/              Regenerated paper Plots 1, 2, and 5 + latency CSV
└── fresh/
    ├── raw/                     Complete optimization histories
    ├── logs/                    One log per selected configuration
    ├── run_configs/             Exact materialized configurations
    ├── run_status.json          Resume state and elapsed times
    ├── host_state_before.json   Captured host controls
    ├── restoration_report.json  Byte-verification result
    ├── plots/                   Four fresh C1–C4 PDFs
    └── tables/                  Phase, factor, claim, and scaling CSVs
```

Inspect the results with:

```bash
RESULTS=results/reproduced_core
cat "$RESULTS/claim_report.md"
jq '.fresh_claims' "$RESULTS/claim_report.json"
jq '.failures, .elapsed_seconds_this_invocation' "$RESULTS/fresh/run_status.json"
jq . "$RESULTS/fresh/restoration_report.json"
ls -lh "$RESULTS/fresh/plots" "$RESULTS/fresh/tables" "$RESULTS/archived/plots"
```

Fresh plotting and calculation are implemented by
[`plot_claims.py`](plot_claims.py). Archived evidence is generated by
[`plot_all.sh`](plot_all.sh), which invokes the canonical paper wrappers for
Plots 1, 2, and 5 and validates `latency_by_params.csv`. No error bars are
invented for the single fresh repetition.

> **Dedicated-machine warning:** live reproduction changes scheduler, network,
> VM, CPU-frequency, P-state, and C-state controls. Use only the paper-compatible
> dedicated/disposable bare-metal host. Do not run it on a shared machine.

The preconfigured CloudLab host is strongly recommended. Otherwise use the
[parameterized `small-lan` profile](https://www.cloudlab.us/p/PortalProfiles/small-lan&rerun_paramset=77c05171-9bff-4316-8832-cc0b265f4bdb)
on Wisconsin `c220` nodes and follow the
[full installation instructions](../docs/FULL_INSTALL.md). The complete
distributed experiments require two nodes.

## Complete all-plot workflow

The complete workflow maps every active empirical paper plot to exact SemaTune
configurations. It performs one fresh rerun per unique configuration, not the
five repetitions used for the paper. A run shared by multiple plots is executed
once.

### Commands

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
