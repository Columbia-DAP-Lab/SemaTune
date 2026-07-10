# SemaTune Artifact

This artifact accompanies **“SemaTune: Semantic-Aware Online OS Tuning with
Large Language Models,”** accepted at the ACM SIGOPS 32nd Symposium on
Operating Systems Principles (SOSP ’26).

This document covers the **Artifact Available** badge. Availability review
checks that the identified artifact is permanently accessible, complete,
licensed, citable, and internally consistent. It does not require evaluators to
repeat the paper’s days-long tuning campaigns.

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

### Archive contents

The release contains:

- SemaTune’s controller, typed parameter validation, telemetry, baselines, and
  workload adapters under `src/barebones_optimizer/`.
- The cross-run memory implementation under
  `src/barebones_optimizer/memory/`, including redaction, summarization,
  embedding, ChromaDB storage, retrieval, configuration generation, tests, and
  plotting utilities.
- Experiment configurations under `config/` and exact compact memory
  configurations under `generated/rag_prior_smoke/`.
- Archived memory and model-backend comparison records under
  `all_results/results_rag/`, `all_results/results_gemini_3/`, and
  `all_results/results_kimi/`, together with the corresponding
  paper-branch plotting scripts.
- The 15-workload, 71-run cross-run-memory source corpus under the retained
  `all_results/results_config_full_param_*_retry/` directories.
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
Legacy exploratory configurations retained elsewhere in `config/` may name
other endpoints; the table above is the authoritative set for the paper and
the imported memory/model-backend comparisons.

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
