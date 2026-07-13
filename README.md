# SemaTune Artifact

This artifact accompanies **“SemaTune: Semantic-Aware Online OS Tuning with
Large Language Models,”** accepted at the ACM SIGOPS 32nd Symposium on
Operating Systems Principles (SOSP ’26).

## Artifact Available checklist (< 5 minutes)

### TL;DR for Artifact Reviewers

1. Open the public
   [GitHub artifact snapshot](https://github.com/nebula-cu/os-param-tuning/tree/sosp-ae).
2. Confirm the permissive [MIT license](LICENSE).
3. Confirm that the paper title and venue appear above.

The Zenodo DOI `10.5281/zenodo.21285693` is reserved and will be assigned to
the public archive when artifact evaluation is complete.

- **Accessible:** the GitHub snapshot is public without authentication.
- **Reusable:** `LICENSE` permits use, modification, and redistribution.
- **Identified:** this README names the paper and SOSP ’26 venue.

## Artifact Functional checklist

### TL;DR for Artifact Reviewers

Use the supplied preconfigured CloudLab host; this is **highly recommended**.
The expected Artifact Functional run uses the real hosted LLM for every
LLM-based method. It demonstrates that all 14 selected tuner and signal paths
operate end to end; it is not paper-performance validation.

1. **SSH into the supplied host and enter the repository** (about 1 minute).

   ```bash
   cd /mydata/SemaTune-ae
   ```

   The host, database, dependencies, and bare-metal controls are already
   prepared. Read the [safety requirements](#environment-and-safety) before
   using another machine.

2. **Validate the complete Functional configuration without changing the
   host** (less than 1 minute).

   ```bash
   functional_example/run.sh --dry-run
   ```

   Expect `SYSBENCH_CONFIG_VALIDATION: PASS`, `SYSBENCH_PREFLIGHT: PASS`, and
   `DRY_RUN: PASS`. This makes no provider request and writes no result or host
   control.

3. **Run the real-LLM Functional workflow in a guaranteed-new directory**
   (approximately 35 minutes elapsed; a measured run took 34 minutes 35
   seconds).

   ```bash
   export GEMINI_API_KEY='<provided-key>'
   mkdir -p results
   FUNCTIONAL_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_real_XXXXXXXX)"

   functional_example/run.sh --quick --real-llm \
     --output-dir "$FUNCTIONAL_DIR"

   echo "Results: $FUNCTIONAL_DIR"
   ```

   The key is supplied for evaluators on the provided CloudLab machine. The
   runner reads it only from the environment. A complete run restores and
   byte-verifies host state, creates summaries, and plots automatically. If it
   is interrupted, resume the same directory:

   ```bash
   functional_example/run.sh --quick --real-llm --resume \
     --output-dir "$FUNCTIONAL_DIR"
   ```

4. **Inspect the generated evidence** (about 2–3 minutes).

   ```bash
   jq '.methods | length' "$FUNCTIONAL_DIR/sysbench_summary.json"  # expect 14
   column -s, -t < "$FUNCTIONAL_DIR/sysbench_summary.csv" | less -S
   jq . "$FUNCTIONAL_DIR/restoration_report.json"                 # expect PASS/PASS
   find "$FUNCTIONAL_DIR/raw" -mindepth 1 -maxdepth 1 -type d | sort
   ls -lh "$FUNCTIONAL_DIR/plots"
   ```

   The terminal should end with `HOST_STATE_RESTORE: PASS`,
   `HOST_STATE_VERIFY: PASS`, and `FUNCTIONAL_SYSBENCH_RUN: PASS`. Confirm that
   all 14 method directories and their tuning/stable rows are populated.

5. **Open the generated plots** (about 1 minute).

   ```bash
   ls -lh \
     "$FUNCTIONAL_DIR/plots/sysbench_tuning_vs_stable.pdf" \
     "$FUNCTIONAL_DIR/plots/functional_figure_6_equivalent.pdf" \
     "$FUNCTIONAL_DIR/plots/functional_figure_7_equivalent.pdf" \
     "$FUNCTIONAL_DIR/plots/functional_figure_8_equivalent.pdf" \
     "$FUNCTIONAL_DIR/plots/functional_figure_9_equivalent.pdf"
   ```

   These plots demonstrate operational paths only. Their host-dependent values
   are not used to validate the paper claims in the Results Reproduced section.

Detailed pointers:

- [Functional reviewer instructions](INSTRUCTIONS.md#artifact-functional-evaluator-instructions): expected terminal output, manual checks, resume, and fresh-host procedure.
- [Functional example reference](functional_example/README.md): all methods, schedules, model choices, configurations, and output schema.
- [Component and source map](docs/COMPONENTS.md): implementation directories and retained source files.
- [Installation guide](docs/FULL_INSTALL.md): base setup plus optional TailBench, SparkBench, and Mutilate dependencies.
- [Plot generation](docs/PLOTTING.md): Functional plot programs and paper-plot wrappers.

Recorded trace replay remains available as an optional provider-free diagnostic,
as described in the Functional reference. It does not replace the expected
real-LLM Artifact Functional run above.

## Results Reproduced checklist

### TL;DR for Artifact Reviewers

The goal is to validate the **observations behind claims C1–C4**, not to obtain
bit-for-bit or percentage-for-percentage equality. The paper aggregates five
runs over 13 workloads; this time-bounded workflow performs one fresh,
real-LLM run over Silo, TPC-C, and Sysbench OLTP-RW. LLM choices and system measurements
are noisy, so similar conclusions, not the exact submitted percentages, are the
expected outcome.

The scoped validation has 1,050 configured windows (1.46 nominal benchmark
hours) and is intended to finish within 10 hours including workload startup,
database resets, and provider latency. Budget several dollars for real-LLM API
usage; the exact amount depends on generated token counts and provider pricing.

The complete paper set contains SparkBench, Masstree, Mutilate, SIbench, Silo,
Sphinx, Sysbench CPU, Sysbench OLTP-RW, TPC-C, Twitter, Wikipedia, Xapian, and
YCSB. A single fresh full-workload C1–C4 workflow has 13,430 configured windows
(18.65 nominal benchmark hours) and can take several days after overhead.
Repeating that workflow five times, as in the paper methodology, exceeds 93
nominal benchmark hours before setup and provider delays and incurs additional
real-LLM cost. This is why the default reviewer workflow uses the representative
three-workload subset.

1. **SSH into the supplied preconfigured CloudLab host** (about 1 minute).

   ```bash
   cd /mydata/SemaTune-ae
   ```

   This host is strongly recommended because the databases, workload inputs,
   dependencies, API access, and bare-metal controls are prepared. Live runs
   change scheduler, network, VM, P-state, and C-state controls; the runner
   restores and byte-verifies them afterward.

2. **Inspect and validate the execution plan without changing the host** (less
   than 1 minute).

   ```bash
   reproduction/reproduce_claims.sh --dry-run
   ```

   Expect 21 unique configurations, 15 LLM configurations, three workloads,
   and 1,050 benchmark windows. The eight-knob SemaTune runs are shared across
   claims rather than repeated.

3. **Run the scoped claims workflow with the real hosted LLM** (nominal window
   time 1.46 hours; allow up to 10 hours for workload startup, database resets,
   and provider latency).

   ```bash
   export GEMINI_API_KEY='<provided-key>'
   mkdir -p results
   RUN_DIR="$(mktemp -d -p "$PWD/results" reproduced_core_real_XXXXXXXX)"

   reproduction/reproduce_claims.sh --run --output-dir "$RUN_DIR"

   echo "Results: $RUN_DIR"
   ```

   The key is supplied on the evaluator CloudLab machine. The command first
   regenerates Plots 1, 2, and 5 from the archived five-repeat evidence, then
   runs one fresh repetition. There is no mock/replay substitute for the fresh
   Results Reproduced run. If interrupted, invoke the same command with the
   same `RUN_DIR`; only structurally complete 50-window histories are skipped.

4. **Regenerate the fresh C1–C4 plots if desired** (less than 1 minute after the
   runs finish). A successful complete run already performs this step.

   ```bash
   .venv-functional/bin/python reproduction/plot_claims.py \
     --results-dir "$RUN_DIR/fresh/raw" \
     --output-dir "$RUN_DIR/fresh" \
     --report-dir "$RUN_DIR" \
     --archived-plots-dir "$RUN_DIR/archived/plots"
   ```

   This writes `c1_sematune_vs_default.pdf`, `c2_sematune_vs_mlos.pdf`,
   `c3_system_vs_mlos.pdf`, and `c4_parameter_scaling.pdf` under
   `$RUN_DIR/fresh/plots/`, with their source values under `fresh/tables/`.

5. **Inspect the report, tables, restoration evidence, and plots** (about 3–5
   minutes).

   ```bash
   cat "$RUN_DIR/claim_report.md"
   jq '.fresh_claims' "$RUN_DIR/claim_report.json"
   jq '.failures, .elapsed_seconds_this_invocation' "$RUN_DIR/fresh/run_status.json"
   jq . "$RUN_DIR/fresh/restoration_report.json"
   column -s, -t < "$RUN_DIR/fresh/tables/claim_summary.csv"
   ls -lh "$RUN_DIR/fresh/plots" "$RUN_DIR/archived/plots"
   ```

   The expected qualitative observations are:

   - **C1:** SemaTune has a positive stable aggregate relative to Default
     Parameters and is normally materially better.
   - **C2:** SemaTune App is significantly better than application-metric MLOS
     in the stable aggregate.
   - **C3:** SemaTune using only system metrics still outperforms
     application-metric MLOS.
   - **C4:** SemaTune remains operational and effective as the action space
     grows through the tested 2, 8, 16, and 41 parameters, including a finite
     and normally positive 41-parameter aggregate.

   `COMPLETE` means all required evidence is structurally valid. `CONSISTENT`
   means the fresh aggregate has the expected direction. A stochastic
   `DIVERGENT` result is reported rather than hidden; reviewers should inspect
   its workload rows and logs instead of requiring the exact paper percentage.

The optional full-workload C1–C4 command is
`reproduction/reproduce_claims.sh --run --full --output-dir DIR`. It selects
232 configurations and 13,430 windows (18.65 nominal window-hours) and can take
one to several days.

Detailed pointers:

- [Results Reproduced evaluator instructions](INSTRUCTIONS.md#results-reproduced-tldr): concise commands and output checks.
- [Claims workflow reference](reproduction/README.md): C1–C4 calculations, schedules, strict resume rules, full mode, and output layout.
- [Paper evidence map](artifact/PAPER_CLAIMS_AND_PLOTS.md): archived inputs, provenance, wrappers, and submitted references.
- [Plot generation](docs/PLOTTING.md): fresh claim plotter and canonical paper-plot commands.
- [Full installation](docs/FULL_INSTALL.md): fresh-host TailBench, BenchBase, Sysbench, and other workload preparation.

Other paper experiments remain available but are outside this time-bounded
C1–C4 workflow.

### What is in the artifact?

- `src/optimizer/`: optimization runtime, workload adapters, memory, and tuners.
- `functional_example/`: reduced Sysbench workflow, configs, traces, and plots.
- `reproduction/`: full experiment configurations and rerun orchestration.
- `all_results/paper_evaluation/`: archived paper-result histories.
- `scripts/artifact_plots/`: paper-plot wrappers and validators.
- `deps/`: pinned third-party benchmark sources, separate from authored code.

See the concise [component and source map](docs/COMPONENTS.md) for every
retained file under `src/optimizer/`. Third-party versions and relationships
are in [`deps/README.md`](deps/README.md).

### Environment and safety

The paper platform used x86-64 CloudLab bare metal with 2 × Intel Xeon Silver
4114, 192 GiB RAM, Ubuntu 22.04.2 LTS, and kernel `5.15.0-160-generic`. The
recorded Functional run used Ubuntu 22.04.2, kernel `5.15.0-177-generic`, 40
logical CPUs, and the same CPU family.

> **Safety warning:** live experiments change scheduler, busy-poll, Intel
> P-state, CPU-idle, and IRQ-affinity controls. Use only a dedicated/disposable
> bare-metal host with administrator access. The runner restores and
> byte-verifies the captured settings on exit.

Evaluators already have a configured host. Otherwise instantiate the
[parameterized CloudLab `small-lan` profile](https://www.cloudlab.us/p/PortalProfiles/small-lan&rerun_paramset=77c05171-9bff-4316-8832-cc0b265f4bdb)
on a Wisconsin `c220`. One node is sufficient for this Functional check; full
distributed experiments need two.

### Installation

On a fresh Ubuntu 22.04 x86-64 host:

```bash
scripts/setup.sh --base
```

This installs the locked Python environment, Sysbench, PostgreSQL and its local
database, Java 21, and BenchBase. Exact Python dependencies are in
`requirements.txt` and `requirements-bootstrap.lock`; OS inputs are in
`artifact/apt-packages.in.json`. Setup generates the ignored, mode-600
`functional_example/site.env`; no manual database address or disk path is
needed for the minimal workflow.

For TailBench, SparkBench, the local Mutilate build, storage requirements, and
component commands, see [full dependency installation](docs/FULL_INSTALL.md).

### What does the Functional run do?

It runs Sysbench OLTP read/write with Fixed; MLOS App/IPC/Cache; Bayesian; DQN;
Q-learning; SemaTune Single; SemaTune App/System/IPC; and SemaTune-Trim
App/IPC/Cache. Each method uses 5 tuning plus 5 stable 10-second windows over
the eight OS parameters used in paper Figures 6–8. The paper configuration is
30 tuning plus 20 stable windows and evaluates broader parameter spaces.

All Functional LLM roles deliberately use Gemini 2.5 Flash-Lite to reduce API
cost. This degraded model and reduced schedule prove that the selected paths
are operational; their performance is host-dependent and is **not** used to
validate paper results.

No API key is required for recorded-response replay:

```bash
functional_example/run.sh --quick --trace-replay \
  --output-dir results/functional_sysbench_trace
```

The runner automatically selects the committed trace for each LLM method.
`--resume` preserves completed methods. DQN/Q-learning messages reducing the
grid from 10 to 2 points are expected and cap their action spaces at 1,000.

The measured 14-method real-provider run took 34 minutes 35 seconds. Its result
directory contained 3.5 MB of files (4.6 MB allocated); the Python environment
used 1.6 GB allocated.

### Short reference links

- [Reviewer execution and inspection](INSTRUCTIONS.md)
- [Component/file map](docs/COMPONENTS.md)
- [Full TailBench, SparkBench, and Mutilate installation](docs/FULL_INSTALL.md)
- [Defining and running a custom Sysbench workload](docs/CUSTOM_SYSBENCH.md)
- [Functional and paper plot generation](docs/PLOTTING.md)
- [Paper claims, exact inputs, and provenance](artifact/PAPER_CLAIMS_AND_PLOTS.md)
- [Full rerun workflow](reproduction/README.md)

### Remaining Functional TODOs

- [ ] Before release, exclude obsolete, duplicate, unrelated, generated, and
  local-only files from the published payload.
- [ ] Measure setup time, peak RAM, and total installed disk usage for the
  minimal workflow on the validation machine.
- [ ] Automate and validate the two-node Mutilate deployment, generated network
  configuration, and end-to-end smoke test. The pinned local binary already
  builds with `scripts/setup.sh --full`.

The reduced inputs are `functional_example/sysbench_*.json`, mapped by
`functional_example/sysbench_suite.json`; full inputs are under
`reproduction/configs/`. The paper makes no mechanized-proof claim requiring a
proof checker.
