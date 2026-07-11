# SemaTune Artifact

This artifact accompanies **“SemaTune: Semantic-Aware Online OS Tuning with
Large Language Models,”** accepted at the ACM SIGOPS 32nd Symposium on
Operating Systems Principles (SOSP ’26).

This document covers the **Artifact Available** package and the evaluator entry
point for the **Artifact Functional** workflow. Availability review checks that
the identified artifact is permanently accessible, complete, licensed,
citable, and internally consistent. Functional review can exercise the short
live example below without repeating the paper’s days-long tuning campaigns.

## Artifact Available

| Resource | Location |
| --- | --- |
| Permanent artifact archive | <https://doi.org/10.5281/zenodo.21285693> |
| GitHub source mirror | <https://github.com/nebula-cu/os-param-tuning/tree/sosp-ae> |
| Software citation | [`CITATION.cff`](CITATION.cff) |
| Software license | [`LICENSE`](LICENSE) |
| Machine-readable provenance | [`artifact/versions.json`](artifact/versions.json) |

The Zenodo deposit is the canonical release. It contains a recursively
materialized copy of every pinned submodule; GitHub-generated source archives
are not a substitute because they do not embed submodule contents. The GitHub
`sosp-ae` branch is presented as a single-root-commit snapshot so that its
published history contains only the artifact state.

### Quick availability check (about 10 minutes)

From the root of an extracted Zenodo archive:

```bash
python3 scripts/artifact_checksums.py verify \
  --root . --manifest SHA256SUMS

python3 -m json.tool artifact/versions.json >/dev/null
python3 -m json.tool artifact/downloads.lock.json >/dev/null
python3 scripts/fetch_artifact_inputs.py list
```

The first command verifies every regular file and the recorded destination of
every symbolic link in the released payload. The remaining commands validate
and display the immutable source, software, model, and external-input records.
These checks do not install packages, contact an LLM provider, change kernel
settings, or run a benchmark.

### Optional full paper-plot reconstruction (Results Reproduced scope)

The evaluator-facing mapping from paper claims to archived result directories,
plotting programs, reference figures, and per-plot commands is in
[`artifact/PAPER_CLAIMS_AND_PLOTS.md`](artifact/PAPER_CLAIMS_AND_PLOTS.md).
The isolated paper-era snapshot is under `all_results/paper_evaluation/` and is
identified by `artifact/paper_plot_inputs.json`. To verify its checksums and
regenerate/validate the active empirical outputs:

```bash
scripts/artifact_plots/verify_paper_inputs.sh
SEMATUNE_VALIDATION_MODE=measured \
  scripts/artifact_plots/generate_all.sh artifact/generated_plots
```

This is optional for Functional review; regenerating every paper presentation
belongs to Results Reproduced. The plot-only workflow takes about one minute on the reference analysis host;
it does not rerun the days-long tuning campaigns. The indirect System/Twitter
series uses all five recovered March 13 measured histories; it does not use the
paper-era synthetic IPC-plus-five-percentage-point substitution. Memory Top-1/Top-3 values are
recomputed from archived histories, and the missing Sysbench App/No-Memory
series is sourced from the regular App-only paper run; the latency CSV is reused
and validated.

The `measured` profile validates the retained measured histories; the optional
`paper` profile preserves strict checks against the two disclosed Plot 3/4
numbers in the accepted manuscript.

The evaluator-facing one-rerun workflow, exact per-plot configurations, shared
result mapping, resume behavior, and end-to-end runner are under
[`reproduction/`](reproduction/README.md). The three Results-Reproduced commands
are:

```bash
reproduction/reproduce_all.sh --dry-run
reproduction/reproduce_all.sh --archived-only --output-dir results/reproduced_archived
reproduction/reproduce_all.sh --run --output-dir results/reproduced_one_run
```

The live command performs one rerun per unique configuration and automatically
plots all results. It requires the caller's exported `GEMINI_API_KEY`, the full
paper dependencies, and the dedicated paper-compatible host. The nominal
benchmark-window time is about 23.5 hours, with one to several days of wall time
expected after workload and model overhead.

## Artifact Functional: minimal live example

> **Safety warning:** this experiment changes live scheduler, busy-poll,
> P-state, and C-state controls. Use only a dedicated or disposable bare-metal
> machine.

The defensible checklist minimum is Fixed versus dual-loop SemaTune on one
short workload. This package goes beyond that minimum and runs the requested
TPC-C suite: Fixed, MLOS, Bayesian/SMAC, DQN, Q-learning, SemaTune Single,
SemaTune Dual, and SemaTune-Trim. Every method uses the canonical eight TPC-C
controls for 10 tuning plus 5 frozen stable windows at 5 seconds per window.
The evaluator commands are:

```bash
functional_example/install_tpcc.sh
functional_example/run.sh --dry-run
functional_example/run.sh --quick --trace-replay \
  --output-dir results/functional_tpcc_trace
```

The deterministic trace makes no provider request. `--quick --real-llm` uses
only the caller's exported `GEMINI_API_KEY`; the key is never printed or
serialized. The eight-method suite takes roughly 15–30 minutes, has a
60-minute timeout, produces raw histories/logs/CSV/JSON plus the live and
archived-evidence plots, and restores and byte-verifies all captured controls
on every exit path. Results are host-dependent and validation does not promise
improvement. Do not overlap trace and real suites: the runner holds an
exclusive host lock because BenchBase recreates the shared TPC-C tables.

```bash
functional_example/plot.sh \
  --results-dir results/functional_tpcc_trace \
  --output-dir results/functional_tpcc_trace/plots
```

See [`functional_example/README.md`](functional_example/README.md) for exact
outputs, resource expectations, harmless warnings, restoration coverage, and
the six representative experiment-kind inputs.

### Archive contents

The release contains:

- SemaTune’s controller, typed parameter validation, telemetry, baselines, and
  workload adapters under `src/barebones_optimizer/`.
- The cross-run memory implementation under
  `src/barebones_optimizer/memory/`, including redaction, summarization,
  embedding, ChromaDB storage, retrieval, configuration generation, tests, and
  plotting utilities.
- Six representative experiment-kind configurations and the runnable quick
  inputs under `functional_example/`; the broader historical config grids are
  not part of the Functional package.
- A 438 MiB, 2,657-file paper-plot snapshot under
  `all_results/paper_evaluation/`, imported from commit
  `4383a40da65f468fb0895d68cd375912dc909068`, plus per-file checksums. Plot
  commands always write regenerated PDFs/CSVs to the requested output directory.
- Archived paper evidence is curated only under
  `all_results/paper_evaluation/`; duplicate old/short/window roots are not
  part of the release.
- Benchmark source snapshots under `deps/`, fixed by the commits below.
- A Python 3.10 dependency specification and fully transitive, hash-locked
  environment in `requirements.in` and `requirements.txt`.
- Machine-readable environment and download records under `artifact/`, plus
  scripts that verify downloads, Ubuntu package payloads, and the final archive.
- MIT software licensing and CFF citation metadata.

The accepted paper PDF is supplied through the SOSP artifact-evaluation system
and is not duplicated in the public software archive.

## Provenance

### Paper experiment platform

| Component | Recorded value |
| --- | --- |
| Infrastructure | Bare-metal CloudLab; one benchmark per host |
| Processors | 2 × Intel Xeon Silver 4114 |
| Hardware threads | 40; hyperthreading enabled |
| Memory | 192 GiB DRAM |
| Operating system | Ubuntu 22.04.2 LTS |
| Linux kernel | `5.15.0-160-generic` |
| Latency workload affinity | CPUs 0–9 |
| PostgreSQL | `14.20-0ubuntu0.22.04.1` |
| Sysbench | `1.0.20+ds-2`, using LuaJIT `2.1.0-beta3` |

PostgreSQL’s exact server version is preserved in the archived BenchBase
summaries, while the Sysbench/LuaJIT versions are preserved in the archived
`sysbench.log` files.

### Functional-validation machine (not the paper platform)

The minimal workflow was validated on a separate machine. These values are
not claims about the original paper platform above.

| Component | Recorded value |
| --- | --- |
| OS / kernel | Ubuntu 22.04.2 LTS; `5.15.0-177-generic` |
| CPU | 2 × Intel Xeon E5-2660 v3 at 2.60 GHz; 10 cores/socket; 2 threads/core; 20 physical / 40 logical CPUs |
| NUMA | node 0 CPUs `0-9,20-29`; node 1 CPUs `10-19,30-39` |
| Memory | 157 GiB visible RAM; 8 GiB swap |
| Storage | 2.1 TiB ext4 LVM over two 1.1 TB HUC101212CSS600 disks |
| PostgreSQL / Sysbench / Python | 14.23 / 1.0.20 / 3.10.12 |
| Functional CPU allocation | controls and perf CPUs 0–9; BenchBase CPUs 10–19; PostgreSQL not explicitly pinned |
| Power/frequency state | SMT enabled; turbo enabled; `intel_cpufreq` with `powersave`; POLL/C1/C1E/C3/C6 exposed |

The live preflight requires CPUs 0–19, debugfs scheduler controls, Intel
P-state, cpuidle, perf, Java 21, BenchBase, PostgreSQL, Python 3.10, and administrator
access. It reports a clear incompatibility rather than silently degrading.

### Benchmark implementations

The paper’s 13 workloads are Mutilate/Memcached; BenchBase TPC-C, Wikipedia,
YCSB, Twitter, and SIbench; Tailbench Masstree, Silo, Xapian, and Sphinx;
Sysbench OLTP and CPU; and DCPerf SparkBench.

| Component | Exact artifact version | License |
| --- | --- | --- |
| BenchBase | `2023-SNAPSHOT`; commit `54d30feb1f9c8b88cca7715fc19de1622cfd1b82`; Java 21; Maven 3.8.4; PostgreSQL JDBC 42.7.4 | Apache-2.0 |
| DCPerf | Version 1.0; commit `5d8d16d63cf28311ee85a2f63ce7506ade67dbef` (`v1.0-1-g5d8d16d`); Spark 2.4.5/Hadoop 2.7 | MIT plus component licenses |
| Tailbench fork | Untagged commit `2f3098b539a9a3413086fc77e29637937bafd116`; Masstree 0.1; Xapian 1.2.13; SphinxBase/PocketSphinx 5prealpha | Composite; nested notices preserved |
| Mutilate | Version 0.1; commit `d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64` | BSD-3-Clause |
| Sysbench | Ubuntu package `1.0.20+ds-2` | GPL-2.0-or-later; LuaJIT is MIT |
| Memcached | Ubuntu package `1.6.14-1ubuntu0.1` from the March 30 snapshot | BSD-3-Clause |

All four Git submodule checkouts match the parent-repository gitlinks.
FleetBench is not used by the paper and is not part of this artifact.

To acquire the exact third-party sources from a Git checkout:

```bash
git submodule update --init --recursive
git -C deps/benchbase rev-parse HEAD
git -C deps/Tailbench rev-parse HEAD
git -C deps/DCPerf rev-parse HEAD
git -C deps/mutilate rev-parse HEAD
```

The expected hashes are the four commits in the table above. BenchBase builds
with `cd deps/benchbase && ./mvnw clean package -P postgres -DskipTests`.
Mutilate builds with `cd deps/mutilate && autoreconf -fi && ./configure &&
make -j"$(nproc)"`. TailBench and DCPerf have workload-specific build/data
steps in their retained upstream/fork READMEs; their large inputs are pinned in
`artifact/downloads.lock.json`. These are acquisition instructions for
archived/full experiments. `install_tpcc.sh` builds only BenchBase; it does not
build unrelated workloads.

### AE software environment

The AE dependency selection uses packages available by the end of March 30,
2026 UTC. This date is a dependency-resolution cutoff, not the source date of
the later archived memory plotting records.

| Component | Pin |
| --- | --- |
| Host architecture | x86-64 (`amd64`) |
| Ubuntu archive | Snapshot `20260330T235959Z` |
| Python | `3.10.12`; Ubuntu build `3.10.12-1~22.04.15` |
| Python bootstrap | pip 26.0.1, setuptools 82.0.1, wheel 0.46.3 |
| Python environment | 137 hash-locked distributions: 19 direct requirements plus their transitive closure |
| Lock generator | uv 0.11.2 with `--exclude-newer 2026-03-31T00:00:00Z` |
| PyTorch | `2.10.0+cpu`; exact x86-64 CPython 3.10 wheel URL and SHA-256 locked |
| Gemini SDK | `google-genai==1.69.0` |
| OpenRouter/OpenAI-compatible SDK | `openai==2.30.0` |
| ChromaDB | `chromadb==1.5.5` |
| Java | OpenJDK 21 build `21.0.10+7-1~22.04`; OpenJDK 8 build `8u482-ga~us1-0ubuntu1~22.04` |

The complete direct and transitive Python records are authoritative in
`requirements.in` and `requirements.txt`. Exact Ubuntu package requests are
declared in `artifact/apt-packages.in.json`; downloaded `.deb` closures can be
recorded and verified with `scripts/manifest_apt_packages.py`.
The AE snapshot selects PostgreSQL 14.22; this is distinct from PostgreSQL
14.20 recorded for the original paper runs above.

### Hosted model identifiers

| Role | Provider and exact request identifier |
| --- | --- |
| SemaTune Actor/Reasoning | Google Gemini Developer API, `gemini-2.5-flash` |
| SemaTune Speculator/Instant | Google Gemini Developer API, `gemini-2.5-flash-lite` |
| Memory summarizer | Google Gemini Developer API, `gemini-2.5-flash-lite` |
| Memory embeddings | Google Gemini Developer API, `gemini-embedding-001`, dimension 768 |
| Gemini comparison Actor | `gemini-3-flash-preview` |
| Gemini comparison Speculator | `gemini-3.1-flash-lite-preview` |
| Additional generated Gemini Actor variant | `gemini-3.1-pro-preview` |
| Kimi comparison Actor | OpenRouter request `moonshotai/kimi-k2-thinking`; archived response identity `moonshotai/kimi-k2-thinking-20251106` |
| Kimi comparison Speculator | OpenRouter request `moonshotai/kimi-k2` |

The paper and retained comparison runs use temperature `0.7`. Their
identifiers and reference date are centralized in
`src/barebones_optimizer/model_versions.py` and `artifact/versions.json`.
The table above is the authoritative set for the paper and retained archived
comparisons; obsolete exploratory configuration grids are excluded.

Hosted APIs do not expose downloadable model weights that the artifact can
checksum. Stable Gemini identifiers are provider-managed endpoints, while the
Gemini comparison identifiers are historical preview endpoints. The artifact
therefore preserves the exact request identifier, provider, SDK version, UTC
run timestamp, and returned concrete model identity whenever the API exposes
one. Historical preview endpoint availability is controlled by the provider.

## External inputs and integrity

`artifact/downloads.lock.json` records every retained external archive by
exact URL, byte size, purpose, license, and a cryptographic digest when one is
available from the publisher or has been independently recorded. It also pins
AN4 and the DCPerf Spark data by Git commit/tree; the latter’s 979 Git-LFS
objects are individually SHA-256 addressed.

The downloader is resumable and writes through a temporary file. It atomically
accepts a file only after its recorded size and SHA-256/SHA-512 match:

```bash
python3 scripts/fetch_artifact_inputs.py fetch \
  spark-2.4.5-hadoop-2.7
```

For an upstream file that was published without a digest, maintainers use the
explicit `record` operation once and review its output before adding the
SHA-256 to the lock. Ordinary `fetch` and `verify-all` operations reject such
an entry until that digest is present.

## License

SemaTune-authored code is released under the [MIT License](LICENSE).
Third-party benchmarks, libraries, and datasets remain under their respective
licenses; their upstream notices are retained with their source snapshots.

## Citation

Machine-readable software and preferred paper citation metadata is provided in
[`CITATION.cff`](CITATION.cff). The preferred paper citation is:

> Georgios Liargkovas, Mihir Nitin Joshi, Hubertus Franke, and Kostis Kaffes.
> “SemaTune: Semantic-Aware Online OS Tuning with Large Language Models.”
> SOSP ’26, 2026.

## Safety and credentials

SemaTune changes live Linux kernel and system parameters. Running experiments
requires a dedicated or disposable bare-metal host and administrator access;
the availability checks above are read-only.

Provider credentials are supplied at runtime through environment variables and
are not part of the released artifact. The artifact contains no reviewer
tracking or analytics.

## Contact

During artifact evaluation, contact the authors through the SOSP artifact
evaluation discussion channel. For repository issues, use the
[GitHub issue tracker](https://github.com/nebula-cu/os-param-tuning/issues).
