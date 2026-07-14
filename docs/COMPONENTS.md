# Artifact component and source map

The root modules in `src/optimizer/` implement the shared optimization runtime:

| File | Purpose |
| --- | --- |
| `__init__.py` | Marks the Python package and records its version. |
| `benchmark.py` | Defines the benchmark interface, metrics, and process helpers. |
| `config.py` | Loads and validates experiment configurations. |
| `main.py` | Runs single-loop experiments. |
| `dual_loop_main.py` | Runs Actor-Speculator experiments. |
| `optimizer.py` | Implements the single-loop measure, suggest, apply, and record lifecycle. |
| `dual_loop_optimizer.py` | Implements concurrent Actor/Speculator requests, application, stable evaluation, and history recording. |
| `main_helpers.py` | Constructs tuner implementations without circular imports. |
| `model_versions.py` | Centralizes hosted-model identifiers and settings. |
| `parameter_manager.py` | Validates, applies, reads, and restores Linux controls. |
| `token_bookkeeping.py` | Aggregates LLM token usage by role and phase. |

## Benchmark adapters

`src/optimizer/benchmarks/` adapts workloads to the shared interface:

| File | Purpose |
| --- | --- |
| `benchmark_registry.py` | Registers benchmark types, scripts, defaults, and lifecycle requirements. |
| `benchbase.py` | Runs BenchBase TPC-C, Wikipedia, Twitter, YCSB, and SIbench. |
| `sysbench.py` | Runs Sysbench OLTP, CPU, memory, FileIO, and threads. |
| `tailbench.py` | Runs the retained TailBench applications. |
| `dcperf.py` | Runs DCPerf SparkBench. |
| `mutilate_benchmark.py` | Coordinates Memcached and the remote load generator. |
| `mutilate_client.py` | Runs on the remote Mutilate load-generator node. |
| `mutilate_protocol.py` | Implements buffered, framed coordination messages. |
| `ADD_BENCHMARK_README.md` | Explains how to register another adapter. |
| `MUTILATE_README.md` | Documents distributed Mutilate execution. |
| `MUTILATE_INTERNAL_NETWORK_SETUP.md` | Documents the two-node network layout. |

## Cross-run memory

`src/optimizer/memory/` implements the agentic memory path:

| File | Purpose |
| --- | --- |
| `common.py` | Shared schemas, sanitization, aggregation, and formatting. |
| `redaction.py` | Produces sanitized retrieval documents from histories. |
| `summary.py` | Summarizes redacted histories with the configured provider. |
| `store.py` | Embeds, stores, and queries memory documents with ChromaDB. |

## Tuners

`src/optimizer/tuners/` contains the parameter-selection strategies:

| File | Purpose |
| --- | --- |
| `base.py` | Tuner interface and response object. |
| `fixed.py` | Fixed control baseline. |
| `llm.py` | Gemini/OpenRouter or recorded-response LLM tuner. |
| `llm_trimming.py` | LLM-guided search-space trimming. |
| `bayesian.py` | SMAC3 Bayesian optimization. |
| `mlos_tuner.py` | MLOS `SmacOptimizer` baseline. |
| `dqn.py` | PyTorch Deep Q-Network tuner. |
| `qlearning.py` | Tabular Q-learning tuner. |

Third-party sources remain separate under `deps/`; see [`deps/README.md`](../deps/README.md).

## Maintainer utilities

`tools/maintenance/` contains provenance-preserving utilities used to derive
the reduced Functional Sysbench configurations and credential-free replay
traces. Evaluators do not need these utilities for the documented workflows.
