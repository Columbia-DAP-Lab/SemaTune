# Detailed evaluator instructions

The root [README](README.md) is the short review path. This file gives the
command variants, resume rules, expected outputs, and troubleshooting details.
Run live workflows only on the supplied dedicated CloudLab host.

## Before executing

Open a shell at the repository root. All commands in this file are run from
there unless stated otherwise.

```bash
mkdir -p results
```

The supplied host already contains the database, dependencies, and site
settings. The API key is assumed to be exported before every real-provider
command. On another host, complete [Fresh-host setup](#fresh-host-setup) first.
Live workflows recreate test tables and change scheduler, network, VM, P-state,
C-state, and IRQ controls. Do not use a shared host or valuable database. Each
runner captures, restores, and verifies the controls it changes.

Missing matching `perf` tools now produces a warning. App/System paths remain
usable, but IPC/Cache results are operational checks only until
`linux-tools-$(uname -r)` is installed.

## Artifact Functional

This is a 14-method Sysbench operational check, not paper-performance
validation. It uses 5 tuning and 5 stable windows per method and normally takes
30–50 minutes.

### Validate without changing the host

```bash
functional_example/run.sh --dry-run
```

Expect `SYSBENCH_CONFIG_VALIDATION: PASS`, `SYSBENCH_PREFLIGHT: PASS`, and
`DRY_RUN: PASS`. This mode makes no provider, database, output, or kernel write.

### Run with the hosted model

```bash
FUNCTIONAL_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_real_XXXXXXXX)"
functional_example/run.sh --quick --real-llm \
  --output-dir "$FUNCTIONAL_DIR"
```

Resume after interruption without replacing completed methods:

```bash
functional_example/run.sh --quick --real-llm --resume \
  --output-dir "$FUNCTIONAL_DIR"
```

For diagnosis, add `--method ID` to a resume command. Valid IDs are printed by
`functional_example/run.sh --help`.

### Provider-free Functional option

```bash
TRACE_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_trace_XXXXXXXX)"
functional_example/run.sh --quick --trace-replay \
  --output-dir "$TRACE_DIR"
```

This exercises the same tuner paths with committed responses. It does not test
new response generation by the hosted model. Do not overlap real and replay
runs; they share a database lock.

### Check Functional evidence

```bash
jq '.methods | length' "$FUNCTIONAL_DIR/sysbench_summary.json"  # 14
jq . "$FUNCTIONAL_DIR/restoration_report.json"                 # PASS/PASS
find "$FUNCTIONAL_DIR/raw" -type f | sort
ls -lh "$FUNCTIONAL_DIR/plots"
```

Success ends with `HOST_STATE_RESTORE: PASS`, `HOST_STATE_VERIFY: PASS`, and
`FUNCTIONAL_SYSBENCH_RUN: PASS`. A short-run method may perform worse than
Fixed; that is not a Functional failure. Rerun only the plotting step with:

```bash
functional_example/plot.sh \
  --results-dir "$FUNCTIONAL_DIR" \
  --output-dir "$FUNCTIONAL_DIR/plots"
```

Method schedules, trace files, and the output schema are in
[functional_example/README.md](functional_example/README.md).

## Results Reproduced: paper Plots 6, 7, and 10

The base workflow uses one repetition on Silo, TPC-C, and Sysbench OLTP-RW to
test the direction of C1–C4. It is intentionally a subset because the complete
paper matrix exceeds the evaluator time budget. Accept preservation of the
claim direction; exact equality with the paper's five-repeat, 13-workload
percentages is not expected.

### Inspect the plans

These commands are read-only:

```bash
reproduction/reproduce_claims.sh --dry-run
reproduction/reproduce_claims.sh --dry-run --extended
reproduction/reproduce_claims.sh --dry-run --full
```

Expected configuration/window counts are 21/1,050 (default), 60/3,360
(`--extended`), and 164/9,480 (`--full`). The full dry run emits an explicit
multi-day and hosted-model quota warning.

### Run one tier

```bash
RUN_DIR="$PWD/results/reproduced_core"

# Base claim validation:
reproduction/reproduce_claims.sh --run --clean \
  --output-dir "$RUN_DIR"

# Or, if evaluation time permits, populate more Plot 6/7/10 methods:
reproduction/reproduce_claims.sh --run --extended --clean \
  --output-dir "$RUN_DIR"

# Or run the 11-workload Plot 6/7 matrix (multi-day):
reproduction/reproduce_claims.sh --run --full --clean \
  --output-dir "$RUN_DIR"
```

Choose only one command above for a new result tree. The API key is read from
the existing environment.

Use `--clean` only on the first command of a new sequence. It moves an existing
tree to `results/archive/reproduced_core-<UTC timestamp>` and creates the path
read by the plotters. To resume, repeat the failed command with the same output
directory and omit `--clean`. A history is reused only if its window count,
phase markers, finite metrics, applied parameters, and required dual-loop
metadata pass validation. `--keep-going` records a failure and continues with
independent jobs.

The fast base tier contains 21 configurations needed for the four directional
comparisons and renders them in the paper Plot 6/7/10 layouts, preserving
unavailable method positions as empty. If time permits, `--extended` selects
60 configurations: Plot 6 adds Bayes, DQN,
Q-Learning, TuxBot-Trim, and MLOS; Plot 7 includes the requested App, System,
IPC, and Cache variants; Plot 10 compares all three tuner families.

`--full` selects 164 configurations and 9,480 windows. It runs the extended
Plot 6/7 matrix across all 11 selected workloads while keeping Plot 10 on Silo,
TPC-C, and Sysbench. Expect several days of execution and substantial
hosted-model quota. In extended/full Plot 10, TuxBot uses 2/8/16/41 knobs and
TuxBot-Trim/MLOS use 2/8/16. No tier schedules 4 knobs; Trim/MLOS at 41 are
omitted because their optimizer latency stalls.

### Check claims, restoration, and plots

```bash
jq . "$RUN_DIR"/fresh/restoration_report*.json
jq '.failures, .elapsed_seconds_this_invocation' \
  "$RUN_DIR/fresh/run_status.json"
ls -lh "$RUN_DIR/fresh/plots" "$RUN_DIR/fresh/tables"
```

Read `claim_report.md`, `three_app_plots_6_7_report.md`, and
`c4_method_report.md`. `COMPLETE` means all
required histories passed structural checks. `CONSISTENT` or `DIVERGENT`
states whether the aggregate preserved the claimed direction; divergence is
reported rather than replaced.

Primary PDFs:

| Paper plot | Output below `$RUN_DIR/fresh/plots/` |
|---|---|
| Plot 6 | `retry_aggregate_improvement_geomean_with_and_without_xapian.pdf` |
| Plot 7 | `retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf` |
| Plot 10 | `ablation_param_geomean.pdf` |

The matching CSVs contain aggregate inputs. Per-workload evidence is under
`fresh/tables/`; exact histories, logs, and materialized configurations are
under `fresh/raw/`, `fresh/logs/`, and `fresh/run_configs/`. Regenerated
accepted-paper reference plots are under `$RUN_DIR/archived/plots/`.

### Hosted-model outage: replay the committed baseline

Use this only if availability, quota, rate limits, or model/account access
prevents the real-provider base run:

```bash
reproduction/replay_claims.sh --dry-run
reproduction/replay_claims.sh --run
cat results/reproduced_core/replay_comparison.md
```

The wrapper verifies `reproduction/trace_baselines/c1_c4_provider/`, removes
provider keys from the child environment, archives an older canonical result,
and replays all 1,050 windows with zero API calls. Resume with:

```bash
reproduction/replay_claims.sh --run --resume
```

Inspect `replay_comparison.csv` for per-workload/method/knob/phase factors and
`replay_comparison.json` for source hashes and action matching. Replay validates
recorded decisions and execution, not generation of new decisions. It covers
the 21-job base only, not the later Trim/IPC/Cache extensions.

## Additional execution options

### Archived evidence only

Regenerate and validate the checked-in evidence for Plots 6, 7, and 10 without
benchmarks or provider calls:

```bash
reproduction/reproduce_claims.sh --archived-only \
  --output-dir results/reproduced_archived
```

### Eleven-workload C1–C3 extension

This runs Fixed, MLOS App, TuxBot App, and TuxBot System across 11 BenchBase,
Sysbench, and TailBench workloads: 44 configurations and 2,200 windows.

```bash
reproduction/reproduce_c123_families.sh --dry-run
reproduction/reproduce_c123_families.sh --run \
  --output-dir "$RUN_DIR" --keep-going
```

Omit `--clean` to reuse the completed three-workload provider baseline. Use
`--clean` only for a standalone all-new 44-configuration run. This extension
requires the provider and has no trace-replay mode.

All paper Plots 6–12 select 273 unique configurations and can take several
days. Select any subset by paper number:

```bash
reproduction/reproduce_all.sh --dry-run --plots 6,7,10
reproduction/reproduce_all.sh --run --plots 6,7,10 \
  --output-dir results/reproduced_selected --keep-going
```

### Plot without rerunning workloads

For compatible completed fresh histories:

```bash
reproduction/plot_all.sh \
  --results-dir results/reproduced_selected/raw \
  --output-dir results/reproduced_selected/plots \
  --plots 6,7,10 --validation fresh
```

For all checked-in archived histories:

```bash
OUT=/tmp/tuxbot-paper-plots
SEMATUNE_VALIDATION_MODE=measured \
  scripts/artifact_plots/generate_all.sh "$OUT"
```

The compatibility wrapper basenames `generate_plot_1.sh` through
`generate_plot_7.sh` predate final paper numbering; public selectors and
validators use paper Plots 6–12. See [docs/PLOTTING.md](docs/PLOTTING.md) for
the exact wrapper/program/reference mapping.

## Fresh-host setup

The evaluator path targets Ubuntu 22.04 x86-64 bare metal with CPUs 0–19 and
root or passwordless sudo. Install the minimal Functional/reproduction stack:

```bash
scripts/setup.sh --base
```

This installs locked Python and OS dependencies, prepares the local PostgreSQL
test role/database, and creates the ignored mode-600
`functional_example/site.env`. Allow 20–40 minutes, 16 GiB RAM, and 5 GiB free.
Full TailBench, SparkBench, and Mutilate setup is optional and documented in
[docs/FULL_INSTALL.md](docs/FULL_INSTALL.md); Spark data needs at least 300 GiB
free.

The exact paper/validation machines are recorded in
[artifact/VALIDATION_ENVIRONMENT.md](artifact/VALIDATION_ENVIRONMENT.md).
Component ownership, third-party modifications, data curation, resource use,
and expected warnings are documented in [docs/COMPONENTS.md](docs/COMPONENTS.md),
[artifact/THIRD_PARTY_MODIFICATIONS.md](artifact/THIRD_PARTY_MODIFICATIONS.md),
[artifact/paper_plot_inputs.json](artifact/paper_plot_inputs.json), and
[docs/FUNCTIONAL_REVIEWER_NOTES.md](docs/FUNCTIONAL_REVIEWER_NOTES.md).

## Failure interpretation

- Provider errors: resume the same directory; use committed replay if the
  service remains unavailable.
- `perf` warning: install matching kernel tools before interpreting IPC/Cache
  values quantitatively.
- Incomplete history: repeat the same wrapper; strict resume reruns only that
  job.
- `DIVERGENT`: inspect disaggregated CSVs and logs; it is a reported stochastic
  observation, not an orchestration failure.
- Missing or failed host restoration: treat the run as failed even if plots
  exist.
- Database connection failure: regenerate site settings with
  `scripts/setup.sh --base`; never point the suite at a shared database.
