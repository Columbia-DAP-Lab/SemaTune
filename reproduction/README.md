# Results Reproduced workflow

## TL;DR: scoped C1–C4 evaluation

The recommended evaluator workflow targets four headline claims within the
available evaluation time. It regenerates the paper evidence from the archived
five-repeat histories, then performs one fresh repetition on Silo, TPC-C, and
Sysbench.

```bash
reproduction/reproduce_claims.sh --dry-run

mkdir -p results
RUN_DIR="$PWD/results/reproduced_core"
reproduction/reproduce_claims.sh --run --clean --output-dir "$RUN_DIR"
echo "Results: $RUN_DIR"
```

The API key is assumed to already be exported. The dry run is read-only and
should report 21 unique configurations, 15 LLM configurations, and 1,050
benchmark windows. At five seconds per window, the nominal benchmark time is
1.46 hours; workload startup, database resets, and hosted-model calls add
overhead. The workflow is designed for less than ten hours on the supplied
host, but actual time remains host- and provider-dependent.

The default is the faster validation path and renders its measured values in
the paper Plot 6/7/10 layouts, leaving unavailable method positions empty. If
evaluation time permits, two larger modes use the same entry point:

| Mode | Configurations / windows | Coverage |
|---|---:|---|
| default | 21 / 1,050 | Minimum three-workload C1–C4 direction check. |
| `--extended` | 60 / 3,360 | Fully populated three-workload Plots 6/7/10, including Bayes, DQN, Q-Learning and all IPC/Cache variants. |
| `--full` | 164 / 9,480 | The extended Plot 6/7 matrix over all 11 workloads; Plot 10 remains a three-workload sweep. |

Plot 10 has TuxBot at 2/8/16/41 knobs and TuxBot-Trim/MLOS at 2/8/16.
No tier schedules a 4-knob point or Trim/MLOS at 41; the latter are omitted
because high-dimensional optimizer latency stalls. Inspect either larger plan
with `reproduce_claims.sh --dry-run --extended` or `--dry-run --full`.

`--clean` atomically moves an existing canonical output tree to
`results/archive/reproduced_core-<UTC timestamp>` before any new output is
written. Omit `--clean` to resume an interrupted run in place.

To rerun the same 21 jobs from the committed provider-backed
Actor/Speculator decisions without an API key, use the packaged wrapper:

```bash
reproduction/replay_claims.sh --dry-run
reproduction/replay_claims.sh --run
```

The wrapper validates `trace_baselines/c1_c4_provider`, removes provider keys,
archives an older canonical output, and writes new replay plots and
`replay_comparison.{md,json,csv}` under `results/reproduced_core`. Resume an
interrupted replay with `reproduction/replay_claims.sh --run --resume`.

The real-provider workflow remains the preferred evaluation. However, a valid
key can be temporarily unusable because of provider availability, quota, rate
limits, or model/account access. If this prevents a new live run from
completing, use the committed wrapper above. The portable baseline contains all
15 TuxBot response streams, source-history hashes, baseline claim values, and
per-workload factors; it does not contain replay measurements.

Replay reruns all 1,050 workload windows without hosted-model requests, but it
does not retest generation of new decisions by the provider. The lower-level
`reproduce_claims.sh --trace-replay-from DIR` remains available to maintainers
who explicitly want to extract traces from another complete provider run.

### Claims

| ID | Claim | Archived evidence | Latest one-repeat observation | Paper plot |
|---|---|---|---|---|
| C1 | TuxBot improves stable performance over Default Parameters. | Submitted 72.49%; measured regeneration 73.01% over 13 workloads. | 11 workloads: 1.4580× (+45.80%), consistent. | Plot 6 |
| C2 | TuxBot outperforms application-metric MLOS. | Submitted 153.3%; measured regeneration 154.09%. | 11 workloads: 2.3334× (+133.34%), consistent. | Plot 6 |
| C3 | System-metric TuxBot outperforms application-metric MLOS. | Submitted 93.7%; measured regeneration 100.19%. | 11 workloads: 2.3761× (+137.61%), consistent. | Plot 7 |
| C4 | TuxBot remains effective at 41 knobs. | Submitted +155.9%; complete Plot 10 and latency CSV regenerate. | Three workloads at 41 knobs: 1.1952× (+19.52%), consistent. | Plot 10 |

C1 and C2 share Plot 6, C3 maps to Plot 7, and C4 maps to Plot 10. The scoped
plot workflow therefore produces three paper-equivalent PDFs, not one PDF per
claim. The fresh values above are the audited 2026-07-14 one-repeat results;
generated report JSON remains the source of truth.

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

### Live C1–C3 extension for BenchBase, Sysbench, and TailBench

The provider-only family extension adds the remaining paper workloads exposed
through those three adapters while reusing complete provider-backed jobs in the
same result tree:

```bash
reproduction/reproduce_c123_families.sh --dry-run
reproduction/reproduce_c123_families.sh --run \
  --output-dir results/reproduced_core --keep-going
```

Use `--clean` for an all-new 44-configuration run. Before any benchmark starts,
the wrapper moves the previous output under `results/archive/` and recreates the
canonical directory consumed by the plotter. It refuses to clean an archive
path and does not combine `--clean` with `--seed-live-from`.

[`c123_family_manifest.json`](c123_family_manifest.json) selects Fixed, MLOS
App, TuxBot App, and TuxBot System for 11 workloads: four TailBench, five
BenchBase, and two Sysbench workloads. It therefore evaluates C1–C3 with 44
configurations and 2,200 windows, but selects no parameter-count jobs for C4.
With the completed Silo/TPC-C/Sysbench OLTP-RW provider baseline, 32 jobs and
1,600 windows remain (2.22 nominal benchmark-window hours). Database loading,
fresh JVM/process startup, provider latency, and a bounded retry for transient
BenchBase timeouts make wall time longer; the exact same command resumes only
incomplete jobs.

The output stays under `results/reproduced_core`: raw histories share
`fresh/raw`, while the extension writes `c123_family_report.{md,json}`, the two
paper-equivalent `fresh/plots/retry_*_with_and_without_xapian.pdf` files, and
disaggregated `fresh/tables/c123_*.csv` tables. Claim direction is reported twice: across all
11 workloads and across the 10 workloads excluding Xapian. Throughput workloads
use candidate/default factors; latency workloads use default/candidate factors.
The PDFs reproduce evaluation Plots 6 and 7, not one PDF per claim. The full
paper method grid and exact page geometry are preserved; unavailable methods
occupy empty bar slots. The shared `fresh/run_status.json` may include jobs
from the provider baseline outside this manifest; `c123_family_report.json`
records the scoped 44-configuration/2,200-window count.

Sphinx can emit an empty interval while the TailBench client starts. The strict
validator permits only the manifest-declared maximum of two *leading tuning*
windows when both request count and aggregation count are exactly zero. A later
zero, a frozen-phase zero, or any non-finite primary metric still fails.

If the canonical directory was replaced by the packaged replay, supply the
saved provider run once with `--seed-live-from DIR`. The wrapper validates the
three provider-backed baseline workloads, archives the current canonical tree,
copies the live baseline back, and rejects replay/live mixing. Omit the seed
option on subsequent resume commands. This extension does not generate or
consume response traces.

### C4 TuxBot/MLOS/TuxBot-Trim follow-on

C4 uses only Silo, TPC-C, and Sysbench OLTP-RW. It compares TuxBot,
TuxBot-Trim, and MLOS at 2, 8, and 16 knobs and TuxBot alone at 41; it does
not sweep the additional C1–C3 workloads or schedule Trim/MLOS at 41 knobs.

```bash
reproduction/reproduce_c4_methods.sh --dry-run
reproduction/reproduce_c4_methods.sh --run \
  --output-dir results/reproduced_core --keep-going
```

[`c4_method_manifest.json`](c4_method_manifest.json) contains 33 configurations
and 1,650 windows. Eighteen configurations (900 windows) resume from the
completed provider baseline. The remaining 15 MLOS/TuxBot-Trim configurations
contain 750 windows. MLOS itself is provider-free, while TuxBot-Trim uses ten
Gemini-assisted trimming cycles per run.

If C1–C3 is still running, queue C4 with that wrapper's PID. The queue validates
the completed C1–C3 evidence before it permits the C4 process to start:

```bash
nohup reproduction/queue_c4_methods.sh \
  --wait-for-pid "$C123_PID" \
  --output-dir results/reproduced_core --keep-going \
  > results/reproduced_core/c4_methods_queue.log 2>&1 &
```

The final PDF is generated by the paper Plot 10 program with the paper's exact
style. `--measured-trim-only` is applied internally: historical Trim count
overrides, workload adjustments, and TPC-C proxy values are disabled. Every
populated TuxBot, MLOS, and TuxBot-Trim point in
`fresh/tables/ablation_param_geomean_per_workload.csv` must therefore come from
a real history. TuxBot-Trim@41 and MLOS@41 are explicitly empty and reported as
not run because their high-dimensional optimizer iterations take too long for
the scoped reviewer run.

The extended Trim implementation emits integer-valued categories as JSON
integer enums. For provider compatibility, an exact allowed numeric string
such as `"0"` is coerced before validation; other malformed candidates remain
rejected. One malformed response is retried within a job, and the queue later
resumes only failed/incomplete jobs.

### Composable Plots 6 and 7 helper

The recommended evaluator entry point is `reproduce_claims.sh --extended`.
Maintainers who already ran the older C4 component can instead run only the
Plots 6/7 comparison and reuse its completed App histories:

```bash
reproduction/reproduce_three_app_plots_6_7.sh --dry-run
reproduction/reproduce_three_app_plots_6_7.sh --run \
  --output-dir results/reproduced_core --keep-going
```

[`three_app_plots_6_7_manifest.json`](three_app_plots_6_7_manifest.json) declares 30
strict configurations and 1,500 windows: ten methods on each of Silo, TPC-C,
and Sysbench OLTP-RW. The methods are Fixed; TuxBot App, System, and IPC;
TuxBot-Trim App, IPC, and Cache; and MLOS App, IPC, and Cache. Following C4,
15 configurations resume and the 15 previously uncovered IPC/Cache signal
configurations execute. Every job requires exactly 50 ordered measurement
windows, correct phase markers, finite optimization metrics and rewards, and
complete Actor/Speculator metadata when applicable.

Plot 6 compares TuxBot App, TuxBot-Trim App, and MLOS App. Plot 7 contains all
nine tuner/signal combinations. This component-only wrapper leaves Bayesian,
DQN, and Q-Learning positions empty; `--extended` populates them while
preserving the submitted method grid and bar width. The wrapper writes
`three_app_plots_6_7_report.{md,json}` and regenerates
the canonical paper-equivalent Plots 6/7 PDFs and CSVs under `fresh/plots/`.
It validates every requested method over all three workloads and checks the
exact reference PDF MediaBoxes before reporting success.

For a process-safe continuation after an already-running C4 pass, maintainers
can use `finish_plots_6_7_10_queue.sh --wait-for-pid PID`. It retries only incomplete
histories, finishes Plot 10 first, and starts Plots 6/7 only after Plot 10 passes.

### Extended and full C1–C4 workflows

To populate every requested method in Plots 6, 7, and 10 on Silo, TPC-C, and
Sysbench OLTP-RW, use `--extended`:

```bash
EXTENDED_DIR="$(mktemp -d -p "$PWD/results" reproduced_claims_extended_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --extended --output-dir "$EXTENDED_DIR"
```

This adds Bayes, DQN, and Q-Learning to Plot 6 and every requested IPC/Cache
variant to Plot 7. It selects 60 configurations and 3,360 windows.

To run the same Plot 6/7 method matrix on all 11 selected workloads, use
`--full` with another new output directory:

```bash
mkdir -p results
FULL_DIR="$(mktemp -d -p "$PWD/results" reproduced_claims_full_real_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --full --output-dir "$FULL_DIR"
echo "Results: $FULL_DIR"
```

Check the plan first with `reproduction/reproduce_claims.sh --dry-run --full`.
It selects 164 unique configurations and 9,480 benchmark windows. The command
prints a prominent warning because the run can take several days and consume
substantial hosted-model quota. Plot 10 remains the three-workload sweep; C4
is not extended to the other eight workloads. Fresh histories and plots are
written under `FULL_DIR/fresh/`; archived comparisons remain under
`FULL_DIR/archived/plots/`.

### Runs, phases, and resume

[`claim_manifest.json`](claim_manifest.json) selects exactly 21 unique jobs:

- Fixed, MLOS App, TuxBot App, and TuxBot System on each workload;
- additional 2-, 16-, and 41-knob TuxBot App runs on each workload; and
- the shared TuxBot App runs as the eight-knob C4 point.

TuxBot runs 30 tuning and 20 stable windows. MLOS preserves its paper
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
├── replay_comparison.md         Concise live-versus-replay conclusion
├── replay_comparison.csv        Claim and per-workload/phase factors
├── replay_comparison.json       Per-job action and source-hash audit
├── archived/plots/              Regenerated paper Plots 6, 7, and 10 + latency CSV
└── fresh/
    ├── raw/                     Complete optimization histories
    ├── logs/                    One log per selected configuration
    ├── run_configs/             Exact materialized configurations
    ├── replay_traces/           One hashed trace per TuxBot configuration
    ├── run_status.json          Resume state and elapsed times
    ├── host_state_before.json   Captured host controls
    ├── restoration_report.json  Byte-verification result
    ├── plots/                   Four fresh C1–C4 PDFs
    └── tables/                  Phase, factor, claim, and scaling CSVs
```

The checked-in input to this output tree is:

```text
reproduction/trace_baselines/c1_c4_provider/
├── README.md
├── manifest.json                 Job map, checksums, models, and scope
├── claim_report.json             Provider-run C1–C4 baseline
├── tables/improvement_factors.csv
└── traces/                       15 provider-response JSON traces
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

For disaggregated replay evidence, use `replay_comparison.csv` for every
workload/method/knob-count/phase factor and `replay_comparison.json` for per-job
action matching. `fresh/tables/phase_metrics.csv` links each job/phase mean to
its raw history; `fresh/tables/improvement_factors.csv` contains the
per-workload factors; and `fresh/replay_traces/*.json`, `fresh/logs/`, and
`fresh/run_configs/` expose the exact decisions, execution logs, and materialized
inputs.

Fresh plotting and calculation are implemented by
[`plot_claims.py`](plot_claims.py). Archived evidence is generated by
[`plot_all.sh`](plot_all.sh), which invokes the canonical paper wrappers for
Plots 6, 7, and 10 and validates `latency_by_params.csv`. No error bars are
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

The complete workflow maps every active empirical paper plot to exact TuxBot
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
reproduction/reproduce_all.sh --run \
  --output-dir results/reproduced_one_run
```

Use `--plots 10` or `--plots 7,6` with any command to select plots. Selection is
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
| 6 | End-to-end performance | 91 | Fixed, TuxBot, trimming, MLOS, Bayesian, DQN, and Q-learning across 13 workloads. |
| 7 | Application vs System/IPC/Cache signals | 130 | Reuses Plot 6 App, Fixed, trimming, and MLOS runs. |
| 8 | Dual vs single loop and cost | 78 | Reuses Plot 6; adds single-Reasoning and single-Instant. Accepted-paper cost CSV is reused because the sampled-session source CSV was not retained. |
| 9 | Tuning robustness | 48 | Entirely reuses the Fixed/TuxBot/trimming/MLOS subset of Plot 6. |
| 10 | Parameter-count ablation | 75 | Uses `configs/parameter_count/`; eight-knob and Fixed results reuse common runs. The submitted provider-latency CSV is reused. |
| 11 | Cross-run memory | 21 | Adds 12 Top-1/Top-3 memory configs and reuses nine common Fixed/App/System no-memory results through result aliases. |
| 12 | Motivation | 9 | Entirely reuses Wikipedia signal configs from Plot 7 and TPC-C parameter configs from Plot 10. |

The per-plot counts overlap. The complete seven-plot union is 273 unique
configurations, not the sum of the table.

Examples for locating exact configs:

```bash
# Every config contributing to Plot 11
jq -r '.plots["11"].jobs[] as $id | .jobs[] | select(.id == $id) | .config' \
  reproduction/experiment_manifest.json

# Complete mapping for one configuration
jq '.jobs[] | select(.id == "common:sysbench_oltp_rw_hi_p99:sematune_app")' \
  reproduction/experiment_manifest.json
```

Configuration families are organized as:

```text
reproduction/configs/common/          Plots 6–9 and shared 8-knob runs
reproduction/configs/parameter_count/ Plot 10 and TPC-C portion of Plot 12
reproduction/configs/memory/          Memory-only additions for Plot 11
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
  history values, including the documented Plots 8/9 paper-history differences.
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
