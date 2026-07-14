# Functional reviewer notes

This page consolidates safety, resource planning, and expected messages for
the short Artifact Functional workflow. The root README remains the execution
entry point.

## Safety and destructive operations

Live runs change Linux scheduler, busy-poll, Intel P-state, CPU-idle, VM, and
IRQ-affinity controls. Use a dedicated/disposable bare-metal host. The runner
captures every control it may change, restores it on success, failure, timeout,
or signal, and requires byte-identical verification.

Database operations are also destructive within their configured test scope:

- `scripts/setup.sh --base` creates or updates the PostgreSQL role `admin` and
  database `benchdb` and writes a generated password to the ignored, mode-600
  `functional_example/site.env`;
- Sysbench preparation recreates its workload tables in the configured
  database; and
- the BenchBase smoke workflow creates and drops disposable smoke databases.

Do not point `SEMATUNE_SYSBENCH_*` or BenchBase configurations at a valuable or
shared database. The documented setup uses only the generated local database.
`functional_example/run.sh --dry-run` performs no root, database, provider,
benchmark, output-directory, or kernel write.

## Resource planning

These are reviewer planning values, not performance requirements. Paper-result
comparability still requires the documented CloudLab hardware.

| Workflow | Expected time | RAM capacity | Disk use/free-space planning |
| --- | --- | --- | --- |
| Functional dry run | Measured 0.27 s | Measured 17 MiB maximum RSS | No output writes |
| Fresh `--base` setup | Allow 20-40 min; download/build speed dependent | 16 GiB recommended | Allow 5 GiB free; validation host uses 1.6 GiB for the Python environment and 290 MiB for the BenchBase tree/build |
| 14-method Functional run | Measured 34 min 35 s; documented hard timeout 60 min | 16 GiB recommended; supplied host has 192 GiB | Measured output 3.5 MB logical/4.6 MB allocated |
| Functional trace replay | Allow 30-50 min because recorded provider delays are preserved | 16 GiB recommended | Same order as the real-provider Functional output |
| Canonical single Sysbench run | About 5-10 min plus provider latency | 16 GiB recommended | Typically a few megabytes of histories/logs |
| Two-node Mutilate check | Allow 10-20 min including setup checks and provider latency | 16 GiB/node recommended | A few megabytes of output; separate server and load-generator nodes |
| Archived seven-plot generation | A few minutes | 4 GiB recommended | 344 MiB retained input data plus generated PDFs/CSVs |
| Full dependency setup | Up to 3 h for Spark population | 16 GiB minimum; paper host recommended | At least 300 GiB free; Spark input is about 109 GB |

The supplied validation host has ample headroom. Peak RSS was recorded for the
dry run only; RAM figures for live workflows are conservative capacity
requirements so reviewers are not given a fabricated peak measurement.

## Expected messages and behavior

| Observation | Meaning and reviewer action |
| --- | --- |
| DQN/Q-learning reports that a grid was reduced from 10 to 2 points | Expected action-space cap for the short Functional run; no action required. |
| A short-run method performs worse than Fixed | Not a Functional failure. The reduced run validates operational paths, not paper performance. |
| Provider quota, rate-limit, model-access, or availability error | External service failure. Resume the same output directory or use the documented trace-replay fallback. |
| A reproduced claim is marked `DIVERGENT` | The fresh stochastic aggregate differed from the paper direction. Inspect workload rows/logs; the artifact reports rather than hides it. |
| Mutilate client repeatedly waits or reconnects | Expected when no server experiment is active. Inspect its systemd status only if the server run cannot connect. |
| `perf` is missing or asks for tools matching the running kernel | Setup and preflight warn but continue. The Functional paths remain executable; hardware-counter files may be empty, so IPC/cache signal values are operational checks only and must not be used for performance comparison. Install matching `linux-tools-$(uname -r)` before evaluating those signal results quantitatively. |
| `HOST_STATE_RESTORE: PASS` and `HOST_STATE_VERIFY: PASS` | Required success messages. A missing or failed verification is an artifact failure even if measurements were produced. |
| Restoration takes several seconds after interruption | Expected while the complete benchmark process group is terminated before controls are restored. |

## Artifact boundary

The active artifact consists of authored implementation under `src/`, reduced
Functional inputs, reproduction manifests/configurations, the 2,098
checksummed archived result files, and their plot/validation programs. The
curated data selection and redactions are recorded in
`artifact/paper_plot_inputs.json`; limitations and manual historical plot
adjustments are recorded in `artifact/PAPER_CLAIMS_AND_PLOTS.md`.

Generated results, virtual environments, downloads, caches, logs, and local
credentials are ignored. Complete pinned dependency submodules remain under
`deps/` to preserve upstream source and licensing; their exact modification
boundary is documented in `artifact/THIRD_PARTY_MODIFICATIONS.md`.
