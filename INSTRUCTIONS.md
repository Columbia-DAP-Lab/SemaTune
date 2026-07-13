# Evaluator instructions

## Results Reproduced TL;DR

Use the supplied preconfigured CloudLab machine; this is **highly recommended**.
The time-bounded workflow asks reviewers to validate four consecutive claims
using one fresh repetition on Silo, TPC-C, and Sysbench, and first regenerates
the corresponding paper evidence from the archived five-repeat histories.

```bash
cd /mydata/SemaTune-ae

reproduction/reproduce_claims.sh --dry-run

export GEMINI_API_KEY='<provided-key>'
mkdir -p results
RUN_DIR="$(mktemp -d -p "$PWD/results" reproduced_core_real_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --output-dir "$RUN_DIR"
echo "Results: $RUN_DIR"
```

The dry run should report 21 unique configurations, 15 LLM configurations, and
1,050 benchmark windows. The live command resumes complete jobs automatically.
Only histories with all required finite measurements and the exact method phase
schedule are reused.

After the run, inspect:

```bash
RESULTS="$RUN_DIR"
cat "$RESULTS/claim_report.md"
jq '.fresh_claims' "$RESULTS/claim_report.json"
jq '.failures, .elapsed_seconds_this_invocation' "$RESULTS/fresh/run_status.json"
jq . "$RESULTS/fresh/restoration_report.json"
ls -lh "$RESULTS/fresh/plots" "$RESULTS/fresh/tables" "$RESULTS/archived/plots"
```

Open the four PDFs in `fresh/plots/` and compare them with the regenerated paper
plots in `archived/plots/`. `COMPLETE` means structurally valid evidence was
produced. `CONSISTENT` or `DIVERGENT` reports whether the one-repeat aggregate
has the claimed direction; it is not an exact-percentage acceptance test.

The paper used five repetitions over 13 workloads. Hosted LLM choices and
system measurements are nondeterministic, so the fresh three-workload run is
expected to reproduce the observations, not necessarily 72.49%, 153.3%, 93.7%,
or 155.9% exactly. The four claims, calculations, schedules, outputs, and plot
programs are documented in [`reproduction/README.md`](reproduction/README.md).
Other paper experiments are outside this time-bounded workflow.

The complete all-experiment workflow remains available, but its 273 unique
configurations can take several days:

```bash
export GEMINI_API_KEY='<provided-key>'
reproduction/reproduce_all.sh --run \
  --output-dir results/reproduced_full
```

An intermediate full-workload workflow limited to the dependencies of C1–C4
(paper Plots 1, 2, and 5) is also available. It contains 232 unique
configurations and can still take several days:

```bash
reproduction/reproduce_claims.sh --dry-run --full
mkdir -p results
FULL_DIR="$(mktemp -d -p "$PWD/results" reproduced_claims_full_real_XXXXXXXX)"
reproduction/reproduce_claims.sh --run --full --output-dir "$FULL_DIR"
```

## Artifact Functional evaluator instructions

## TL;DR

This workflow is a minimal operational demonstration of all selected tuners. It
is **not** a reproduction or validation of the paper's performance results.

1. SSH into the preconfigured CloudLab machine supplied by the authors. This is
   highly recommended because the host, database, dependencies, API key, and
   required bare-metal controls are already prepared.
2. Optionally inspect the [artifact component map](docs/COMPONENTS.md),
   [`src/optimizer/`](src/optimizer/), and
   [`functional_example/`](functional_example/).
3. From the repository root, run the real-provider Functional suite (about 35
   minutes on the validation host):

   ```bash
   functional_example/run.sh --dry-run
   export GEMINI_API_KEY='<provided-key>'
   mkdir -p results
   FUNCTIONAL_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_real_XXXXXXXX)"
   functional_example/run.sh --quick --real-llm \
     --output-dir "$FUNCTIONAL_DIR"
   echo "Results: $FUNCTIONAL_DIR"
   ```

4. Plot again if desired; a successful complete run already invokes this command:

   ```bash
   functional_example/plot.sh \
     --results-dir "$FUNCTIONAL_DIR" \
     --output-dir "$FUNCTIONAL_DIR/plots"
   ```

5. Manually inspect `$FUNCTIONAL_DIR`. It should contain
   populated configs, logs, raw histories for all 14 methods, summary tables,
   restoration evidence, and plots. The plots demonstrate that each selected
   tuner path ran; do not use their performance values to validate paper claims.

To avoid provider calls entirely, omit the key and replace `--real-llm` with
`--trace-replay`. The runner automatically selects the committed trace for each
LLM-based method.

## Reviewer checks

### 1. Inspect the artifact before executing it

- Read the [component and source map](docs/COMPONENTS.md) to validate the
  relationship between the implementation, Functional example, reproduction
  configurations, archived data, and paper plots.
- Inspect the descriptions of `src/optimizer/`, its benchmark adapters, memory
  subsystem, and tuners in [`docs/COMPONENTS.md`](docs/COMPONENTS.md).
- Read [Environment and safety](README.md#environment-and-safety). The live run
  changes scheduler, busy-poll, P-state, C-state, and IRQ-affinity controls.
- Inspect [Installation](README.md#installation)
  and the pinned third-party [source map](deps/README.md).

### 2. Validate the configuration without changing the host

Run:

```bash
functional_example/run.sh --dry-run
```

Expect `SYSBENCH_CONFIG_VALIDATION: PASS`, `SYSBENCH_PREFLIGHT: PASS`, and
`DRY_RUN: PASS`. The dry run performs no benchmark, provider request, database
change, output write, or kernel write.

### 3. Execute the Functional example

Run the real-provider command from the TL;DR. It executes Sysbench OLTP
read/write with Fixed; MLOS App/IPC/Cache; Bayesian; DQN; Q-learning; SemaTune
Single; SemaTune App/System/IPC; and SemaTune-Trim App/IPC/Cache. Every method
uses 5 tuning and 5 stable windows over the eight controls used in paper
Figures 6–8. See [What the Functional run does](README.md#what-does-the-functional-run-do)
for the model and scope limitations.

The terminal should finish with:

```text
HOST_STATE_RESTORE: PASS
HOST_STATE_VERIFY: PASS
FUNCTIONAL_SYSBENCH_RUN: PASS (.../results/functional_sysbench_real)
```

If interrupted, rerun the same real-provider command with `--resume` and the
same `FUNCTIONAL_DIR`; completed methods are preserved.

### 4. Inspect generated results

Run:

```bash
RESULTS="$FUNCTIONAL_DIR"
find "$RESULTS" -maxdepth 2 -type f | sort
jq '.methods | length' "$RESULTS/sysbench_summary.json"
column -s, -t < "$RESULTS/sysbench_summary.csv" | less -S
jq . "$RESULTS/restoration_report.json"
ls -lh "$RESULTS/plots"
```

Validate manually that:

- `sysbench_summary.json` reports 14 methods;
- `sysbench_summary.csv` has populated tuning and stable rows for every method;
- `raw/<method>/` contains a completed optimization history for every method;
- `logs/<method>.log` contains benchmark windows and no terminal error;
- `configs/` contains the materialized configurations actually executed;
- `restoration_report.json` reports byte-identical restoration; and
- `plots/` contains non-empty PDF, PNG, and CSV outputs for the aggregate
  Functional plot and Figure 6/7/8/9 equivalents.

Open the PDFs or PNGs and check that axes, labels, legends, bars/markers, and
method series are populated and readable. These are demonstration-only,
host-dependent plots. For paper-result validation, follow the mapping in
[`artifact/PAPER_CLAIMS_AND_PLOTS.md`](artifact/PAPER_CLAIMS_AND_PLOTS.md), not
the Functional plots. The Functional plot programs are linked beside each
output in the [Functional checklist](README.md#artifact-functional-checklist); wrappers,
programs, reference PDFs, and commands for all seven paper plots are in
[`docs/PLOTTING.md`](docs/PLOTTING.md).

## If you do not use the provided machine

1. Instantiate the
   [parameterized CloudLab `small-lan` profile](https://www.cloudlab.us/p/PortalProfiles/small-lan&rerun_paramset=77c05171-9bff-4316-8832-cc0b265f4bdb)
   using a Wisconsin `c220` node. One node is sufficient for this Functional
   workflow; full distributed experiments require two.
2. SSH into the node and obtain the artifact using the recursively materialized
   archive or a recursive Git checkout.
3. Confirm Ubuntu 22.04, x86-64 bare metal, CPUs 0–19, administrator access,
   and the controls listed in [Environment and safety](README.md#environment-and-safety).
4. Install the minimal environment:

   ```bash
   scripts/setup.sh --base
   ```

5. Export your Gemini key and follow the TL;DR commands. An equivalent dedicated
   Ubuntu 22.04 x86-64 bare-metal host may be used, but results remain
   host-dependent.

The broader TailBench, SparkBench, and Mutilate dependency installation is not
needed for this Sysbench check. See
[`docs/FULL_INSTALL.md`](docs/FULL_INSTALL.md) if it
is required for additional experiments.
