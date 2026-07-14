# TuxBot Artifact

This artifact accompanies **“TuxBot: Semantic-Aware Online OS Tuning with
Large Language Models,”** accepted at the ACM SIGOPS 32nd Symposium on
Operating Systems Principles (SOSP ’26).

## Artifact Available (< 5 minutes)

### TL;DR for Artifact Reviewers

1. Open the reviewer-shared
   [GitHub artifact snapshot](https://github.com/nebula-cu/os-param-tuning/tree/sosp-ae).
2. Confirm the permissive [MIT license](LICENSE).
3. Confirm that the paper title and venue appear above.

The Zenodo DOI `10.5281/zenodo.21285693` is reserved and will be
assigned to the public archive after artifact evaluation is complete.

## Artifact Functional (< 40 minutes)

### TL;DR for Artifact Reviewers

Use the supplied preconfigured CloudLab host; this is **highly recommended**.
The expected Artifact Functional run uses the real hosted LLM for every
LLM-based method. It demonstrates that all 14 selected tuner and signal paths
operate end to end; it is not paper-performance validation.

1. **SSH into the supplied host and open a shell at the repository root**
   (about 1 minute). All commands below are run from the repository root.

   The host, database, dependencies, and bare-metal controls are already
   prepared. Read the [safety requirements](#environment-and-safety) before
   using another machine.

2. **Validate the complete Functional configuration without changing the host** (less than 1 minute).

   ```bash
   functional_example/run.sh --dry-run
   ```

   Expect `SYSBENCH_CONFIG_VALIDATION: PASS`, `SYSBENCH_PREFLIGHT: PASS`, and
   `DRY_RUN: PASS`. This makes no provider request and writes no result or host
   control.

3. **Run the real-LLM Functional workflow in a guaranteed-new directory** (approximately 35 minutes).

   ```bash
   mkdir -p results
   FUNCTIONAL_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_real_XXXXXXXX)"

   functional_example/run.sh --quick --real-llm \
     --output-dir "$FUNCTIONAL_DIR"
   ```

   The API key is supplied on the provided host and is read only from the
   environment. If interrupted, resume the same directory:

   ```bash
   functional_example/run.sh --quick --real-llm --resume \
     --output-dir "$FUNCTIONAL_DIR"
   ```

4. **Inspect the generated evidence** (about 2–3 minutes).

   ```bash
   jq '.methods | length' "$FUNCTIONAL_DIR/sysbench_summary.json"  # expect 14
   jq . "$FUNCTIONAL_DIR/restoration_report.json"                 # expect PASS/PASS
   ls -lh "$FUNCTIONAL_DIR/plots"
   ```

   Success ends with `HOST_STATE_RESTORE: PASS`, `HOST_STATE_VERIFY: PASS`,
   and `FUNCTIONAL_SYSBENCH_RUN: PASS`. Generated performance is host-dependent
   and is not used to validate the paper claims.

For detailed commands, trace replay, resume modes, fresh-host setup, and
troubleshooting, go to [INSTRUCTIONS.md](INSTRUCTIONS.md). Method/output details
are in [functional_example/README.md](functional_example/README.md); environment,
safety, and resources are in [artifact/VALIDATION_ENVIRONMENT.md](artifact/VALIDATION_ENVIRONMENT.md)
and [docs/FUNCTIONAL_REVIEWER_NOTES.md](docs/FUNCTIONAL_REVIEWER_NOTES.md).

## Results Reproduced (<10 hours)

The recommended base run validates the **direction of C1–C4** once on Silo,
TPC-C, and Sysbench OLTP-RW. These workloads cover the three evaluator-ready
benchmark families while keeping the run below ten hours. Repeating the full
paper matrix would take several days and substantially more hosted-model quota;
therefore, the reviewer workflow intentionally uses this time-bounded subset.
Results should preserve each claim's direction, but are not expected to match
the exact five-repeat paper percentages.

### Claim map

| Claim | Accepted-paper observation | Paper plot |
|---|---|---|
| C1 | TuxBot improves stable performance over Default Parameters (+72.49%). | Plot 6 |
| C2 | TuxBot outperforms application-metric MLOS (+153.3%). | Plot 6 |
| C3 | System-metric TuxBot outperforms application-metric MLOS (+93.7%). | Plot 7 |
| C4 | TuxBot remains effective at 41 knobs (+155.9%). | Plot 10 |

A prior one-repeat validation preserved every direction: C1 +45.80%, C2
+133.34%, C3 +137.61% over 11 workloads, and C4 +19.52% at 41 knobs over the
three-workload sweep. These are stochastic observations, not replacement paper
values.

The API key is assumed to already be exported. Validate the plan, then run the
base tier:

```bash
reproduction/reproduce_claims.sh --dry-run
RUN_DIR="$PWD/results/reproduced_core"
reproduction/reproduce_claims.sh --run --clean --output-dir "$RUN_DIR"
```

The base tier selects 21 configurations and 1,050 windows. `--clean` moves an
older result tree under `results/archive/`; omit it when resuming.

If evaluation time permits, populate the complete paper Plot 6/7 method grid
and compare TuxBot, TuxBot-Trim, and MLOS throughout Plot 10 with the
60-configuration three-workload tier instead:

```bash
reproduction/reproduce_claims.sh --dry-run --extended
reproduction/reproduce_claims.sh --run --extended --clean \
  --output-dir "$RUN_DIR"
```

All tiers write the paper-layout plots under `$RUN_DIR/fresh/plots/`:

| Paper plot | PDF |
|---|---|
| Plot 6 (C1/C2) | `retry_aggregate_improvement_geomean_with_and_without_xapian.pdf` |
| Plot 7 (C3) | `retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf` |
| Plot 10 (C4) | `ablation_param_geomean.pdf` |

The fast base tier fills its measured method positions and preserves all
unavailable paper-grid positions as empty. `--extended` fills the requested
three-workload method matrix when the evaluator has more time.

Legacy internal identifiers beginning with `sematune_*` refer to the same
system now named TuxBot and are retained for result compatibility.

The reports are `claim_report.md`, `three_app_plots_6_7_report.md`, and
`c4_method_report.md`. Matching CSVs are under `fresh/plots/` and `fresh/tables/`.
The relevant reports must show complete evidence and `CONSISTENT`; also verify
`fresh/restoration_report.json` reports successful host restoration.

If the provider is unavailable, run the committed base trace fallback:

```bash
reproduction/replay_claims.sh --dry-run
reproduction/replay_claims.sh --run
```

Replay executes the base workload windows with zero API calls but does not
retest provider generation. Detailed validation, resume, extended/full modes,
and exact output names are in [INSTRUCTIONS.md](INSTRUCTIONS.md).

## Artifact reference

### Environment and safety

The paper machine is x86-64 CloudLab `c220` bare metal: 2 × Intel Xeon Silver
4114, 192 GiB RAM, Ubuntu 22.04.2, kernel `5.15.0-160-generic`. Live runs change
scheduler, network, VM, P-state, C-state, and IRQ controls and recreate test
database tables. Use only a dedicated/disposable host and database. Every
runner snapshots, restores, and byte-verifies host state.

Install the minimal environment on Ubuntu 22.04 x86-64 with:

```bash
scripts/setup.sh --base
```

Setup uses locked Python requirements, package-managed OS dependencies, and
generated site configuration. Missing matching `perf` tools produce a warning,
not a setup failure; IPC/cache variants are then operational checks only and
must not be interpreted quantitatively.

Expected time, RAM, disk use, destructive-operation warnings, and unusual
messages are listed in [Functional reviewer notes](docs/FUNCTIONAL_REVIEWER_NOTES.md).

### Components and provenance

| Path | Role |
|---|---|
| `README.md`, `INSTRUCTIONS.md` | Short and detailed evaluator entry points |
| `src/optimizer/` | Tuning runtime, tuners, system controls, and workload adapters |
| `functional_example/` | Minimal Sysbench example, configurations, traces, and plots |
| `reproduction/reproduce_claims.sh` | Unified base, extended, and full C1–C4 runner |
| `reproduction/{claim,extended_claim,full_claim}_manifest.json` | Exact one-repeat tier selections and completion contracts |
| `reproduction/trace_baselines/` | Checksummed provider-response fallback and disaggregated baseline evidence |
| `reproduction/configs/` | Materialized paper-derived experiment configurations |
| `reproduction/{suite.py,plot_claims.py,plot_three_app_paper.py,plot_c4_methods.py}` | Fresh validation, resume, plotting, and report generation |
| `all_results/paper_evaluation/` | Curated archived paper histories |
| `paper_evaluation_plots/` | Submitted reference PDFs and latency table |
| `scripts/setup*.sh`, `scripts/fetch_artifact_inputs.py` | Dependency installation and locked external-input retrieval |
| `scripts/artifact_plots/` | Archived-paper plot wrappers and numerical validators |
| `config/benchbase/postgres/` | Retained BenchBase workload inputs |
| `deps/` | Pinned third-party source snapshots, kept separate from authored code |
| `artifact/` | Environment locks, checksums, provenance, and modification records |
| `tests/` | Functional, setup, trace, manifest, and reproduction checks |
| `tools/maintenance/` | Maintainer-only derivation and trace-extraction utilities |
| `requirements*.{in,txt,lock}` | Python dependency inputs and locked environments |

See the [source map](docs/COMPONENTS.md), [dependency installation](docs/FULL_INSTALL.md),
[third-party modification boundary](artifact/THIRD_PARTY_MODIFICATIONS.md), and
[data curation record](artifact/paper_plot_inputs.json). Exact Python locks,
OS-package inputs, dependency commits, example configurations, and download
automation are included. The paper makes no mechanized-proof claim; empirical
quantitative claims are checked by the reproduction and plot-validation scripts
above.
