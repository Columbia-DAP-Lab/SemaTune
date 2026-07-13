# SemaTune Sysbench OLTP-RW Functional example

> **Safety warning:** live runs change scheduler, busy-poll, Intel P-state, CPU
> idle, and IRQ-affinity controls. Use only a dedicated/disposable bare-metal
> Linux host. Every runner snapshots, restores, and byte-verifies these values.

This is the SOSP '26 Artifact Functional entry point. Sysbench OLTP read/write
uses the paper's ordered eight-control search space. The reduced suite is for
fast functional validation; the canonical command below retains the paper
configuration and is longer.

## Evaluator workflow

From the repository root:

```bash
scripts/setup.sh --base
functional_example/run.sh --dry-run

export GEMINI_API_KEY='<provided-key>'
mkdir -p results
FUNCTIONAL_DIR="$(mktemp -d -p "$PWD/results" functional_sysbench_real_XXXXXXXX)"
functional_example/run.sh --quick --real-llm \
  --output-dir "$FUNCTIONAL_DIR"
```

`scripts/setup.sh` defaults to `--base`. It creates `.venv-functional`,
installs the hash-locked Python environment, Sysbench, PostgreSQL and the local
`admin`/`benchdb` database, Java 21, and pinned BenchBase. Use `--full` only
when preparing the additional Mutilate, TailBench, and DCPerf/SparkBench
software dependencies and datasets; the Functional Sysbench workflow does not
need them. Full-install storage, timing, per-component commands, and the
remaining distributed Mutilate TODO are documented in the root README.

The dry run performs no root, database, benchmark, provider, output-directory,
or kernel write. The expected evaluator run uses real hosted-model decisions
and takes about 35 minutes on the supplied host. The live suite supports
Fixed; MLOS App/IPC/Cache; Bayesian; DQN; Q-learning; SemaTune Single;
SemaTune App/System/IPC; and SemaTune-Trim App/IPC/Cache. A single method can
be selected with `--method ID`; use `--resume` to retain completed methods.

Provider-free `--trace-replay` is an optional diagnostic. It automatically uses
the method-specific, committed Gemini 2.5
Flash-Lite response histories in `functional_example/traces/`. These traces
were extracted from the completed Functional real-provider run and contain no
API key. No trace-path configuration is required from the evaluator.

For real Gemini decisions:

```bash
export GEMINI_API_KEY='<provided-key>'
functional_example/run.sh --quick --real-llm \
  --output-dir "$FUNCTIONAL_DIR"
```

The key is read only from the process environment. It is never read from a
file, printed, placed on a command line, or serialized.

All Functional SemaTune calls deliberately use Gemini 2.5 Flash-Lite. Both the
Actor and Speculator use Flash-Lite in every dual-loop variant. This degraded
model choice reduces API cost and avoids overcharging during evaluation. The
suite proves that the selected tuners and signal paths are operational; its
performance outputs are not used for result validation.

## Canonical paper configuration

To validate the original Sysbench setup rather than the reduced 5+5 schedule:

```bash
export GEMINI_API_KEY='your-key-from-Google-AI-Studio'
functional_example/run_original_sysbench.sh \
  --output-dir results/functional_sysbench_original_real
```

This uses
`reproduction/configs/common/sysbench_oltp_rw_hi_p99/sematune_app.json`
unchanged except for the output path and removal of stored credentials:

- 30 tuning windows plus 20 frozen stable windows, with one explicit default
  baseline measurement in the current implementation;
- 5-second configured windows (3-second Sysbench measurement bracketed by
  collection delay/buffer), 40 threads, four 100,000-row tables, no rate cap;
- controls and perf on CPUs 0-9; Sysbench on CPUs 10-19;
- Gemini 2.5 Flash Actor and Gemini 2.5 Flash-Lite Speculator;
- the exact paper knob order, ranges, Linux defaults, and final Actor gate.

`verify_sysbench_original.py` compares the runnable config with both the
canonical JSON and an archived paper history. After execution it requires:

- range-valid, justified Actor and Speculator responses;
- `parameters_applied=true` plus an application timestamp for every inspected
  response (set only after ParameterManager succeeds);
- a fresh final Actor response after tuning window 30;
- exact equality between that final proposal and the frozen stable config;
- 30 tuning and 20 stable windows and no serialized credential material.

It writes `config_validation.json` and `llm_application_validation.json` beside
the raw history and log. Approximate runtime is 5-10 minutes on the validation
machine, subject to provider latency.

To run the complete single-workload comparison used for the Functional
demonstration—including all classical baselines, SemaTune Single/App/System/IPC,
MLOS IPC, and SemaTune-Trim—use:

```bash
functional_example/run_sysbench_paper_suite.sh \
  --output-dir results/functional_sysbench_paper_suite
```

This command is resumable and preserves each method's original paper profile.
It reuses completed histories in the output directory, snapshots/restores the
host separately around every missing method, and generates a Plot-1-style
comparison using windows 1-30 and 31-50. Positive percentages indicate lower
p99 latency than Fixed; negative percentages indicate degradation. As in Plot
1, the plotted percentage is `(Fixed p99 / Candidate p99 - 1) * 100`; the CSV
also reports the conventional percentage reduction in latency. To rerun a
single method explicitly, add `--method METHOD --force`.

The resulting comparison is written as
`plots/sysbench_improvement_over_fixed.{pdf,png,csv,json}` with the complete
audit in `plots/validation.json`.

## Matched Fixed versus dual-loop comparisons

The canonical Sysbench CPU-throughput and BenchBase TPC-C p99 workloads can be
run as matched Fixed/dual-loop comparisons. Both methods are normalized to 30
tuning plus 20 stable windows, and the generated report compares windows
31–50. The dual loop uses the real Gemini provider:

```bash
functional_example/run_fixed_dual_comparison.sh \
  --workload sysbench_cpu_tput

functional_example/run_fixed_dual_comparison.sh \
  --workload tpcc_hi_p99
```

Each command is resumable, snapshots/restores the host independently around
each method, and writes `comparison/comparison.{json,csv}`. CPU throughput is
reported as a higher-is-better gain; TPC-C is reported as lower-is-better p99
latency reduction and paper-style Fixed/Candidate improvement.

## BenchBase workload smoke test

Wikipedia, Twitter, and YCSB can be checked with one Fixed measurement each:

```bash
functional_example/run_benchbase_smoke.sh \
  --output-dir results/benchbase_fixed_smoke
```

The runner uses the canonical workload XML and Fixed configuration, but
normalizes each workload to exactly one 5-second window. Each workload gets a
fresh disposable PostgreSQL database, an independent host-state snapshot and
byte-verified restoration report, raw BenchBase output, and optimizer history.
It continues to the remaining workloads after a workload failure and exits
nonzero if any workload lacks positive throughput/goodput/p99 metrics. The
combined result is written to `smoke_summary.{json,csv}`.

To replay the exact recorded Actor/Speculator actions (including their response
delays) from completed App/System/IPC dual-loop histories without provider
calls, run:

```bash
functional_example/run_sysbench_dual_replay.sh \
  --real-dir results/functional_sysbench_llm_retry_temp07 \
  --output-dir results/functional_sysbench_dual_action_replay
```

The command byte-verifies restoration after every replay and emits
`plots/sysbench_real_vs_action_replay.{pdf,png,csv,json}`. SemaTune Single is
shown for real-LLM context but has no dual-loop replay bar.

## Reduced suite and outputs

Every reduced method runs 5 tuning and 5 frozen stable windows at 10 seconds
each. The original SemaTune paper configuration uses 30 tuning plus 20 stable windows.
The Functional suite tunes only the same ordered eight OS parameters used in
Figures 6, 7, and 8. The Results-Reproduced workflow evaluates additional
parameter counts and larger search spaces.

The `sysbench_*.json` inputs cover the 14 methods and signal variants listed
above. `sysbench_suite.json` maps each input to its paper source and, for every
LLM-based method, to its recorded trace under `traces/`. The runner selects
these mappings automatically in `--trace-replay` mode.

The complete reduced output includes materialized configs, logs, raw histories,
machine/state manifests, `sysbench_summary.{csv,json}`, restoration evidence,
and:

```text
plots/sysbench_tuning_vs_stable.{png,pdf,csv}
plots/functional_figure_6_equivalent.{png,pdf,csv}
plots/functional_figure_7_equivalent.{png,pdf,csv}
plots/functional_figure_8_equivalent.{png,pdf,csv}
plots/functional_figure_9_equivalent.{png,pdf,csv}
plots/archived_headline.{pdf,csv}
plots/validation.json
```

Plotting can be repeated without rerunning the workload:

```bash
functional_example/plot.sh \
  --results-dir results/functional_sysbench_trace \
  --output-dir results/functional_sysbench_trace/plots
```

Results are host-dependent and the validator does not require SemaTune to beat
Fixed in a short run. Full paper campaigns use five repeats and are documented
separately in `../reproduction/README.md`.

The Figure 6/7/8/9 equivalents are single-workload operational samples computed
from this reduced run. They are not used to validate the paper results. They
reuse the paper plotting styles, colors, hatches, markers, axes, method order,
and group labels. Figure 8 uses only the improvement panel from the paper's
dual/single/MLOS layout; the Functional suite has no separate Single-Reasoning
run, so it is not fabricated. Figure 9 uses the paper's bad-window-rate and
variability layout. Every equivalent includes a demonstration-only footnote.

## Restoration

The runner captures every setting it may change. Success, failure, timeout,
`SIGINT`, `SIGTERM`, and `SIGHUP` terminate the complete benchmark process group
before restoration. A live run is successful only if both
`HOST_STATE_RESTORE: PASS` and `HOST_STATE_VERIFY: PASS` are printed.
