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
functional_example/install.sh
functional_example/run.sh --dry-run
functional_example/run.sh --quick --trace-replay \
  --output-dir results/functional_sysbench_trace
```

The dry run performs no root, database, benchmark, provider, output-directory,
or kernel write. Replay makes no provider request. The live suite supports
Fixed, MLOS, Bayesian, DQN, Q-learning, SemaTune Single, SemaTune Dual, and
SemaTune-Trim. A single method can be selected with `--method ID`; use
`--resume` to retain already completed methods.

For real Gemini decisions:

```bash
export GEMINI_API_KEY='your-key-from-Google-AI-Studio'
functional_example/run.sh --quick --real-llm \
  --output-dir results/functional_sysbench_real
```

The key is read only from the process environment. It is never read from a
file, printed, placed on a command line, or serialized.

## Canonical paper configuration

To validate the original Sysbench setup rather than the reduced 10+5 schedule:

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

Every reduced method uses the same workload and eight ranges as the paper, but
runs 10 tuning and 5 frozen stable windows at 10 seconds each. The inputs are
`sysbench_fixed.json`, `sysbench_mlos.json`, `sysbench_bayesian.json`,
`sysbench_dqn.json`, `sysbench_qlearning.json`,
`sysbench_sematune_single.json`, `sysbench_sematune_dual.json`, and
`sysbench_sematune_trim.json`. `sysbench_suite.json` maps them to their paper
sources. `sysbench_trace_replay.json` is an offline policy derived from the
archived Sysbench Actor configuration.

The complete reduced output includes materialized configs, logs, raw histories,
machine/state manifests, `sysbench_summary.{csv,json}`, restoration evidence,
and:

```text
plots/sysbench_tuning_vs_stable.{png,pdf,csv}
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

## Restoration

The runner captures every setting it may change. Success, failure, timeout,
`SIGINT`, `SIGTERM`, and `SIGHUP` terminate the complete benchmark process group
before restoration. A live run is successful only if both
`HOST_STATE_RESTORE: PASS` and `HOST_STATE_VERIFY: PASS` are printed.

The six `representative_*.json` files cover Fixed, application-metric SemaTune,
System-metric SemaTune, a classical baseline, memory-enabled SemaTune, and the
parameter-count ablation. They are representative inputs; Functional evaluation
does not require running every paper experiment.
