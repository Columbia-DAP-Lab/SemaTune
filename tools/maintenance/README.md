# Maintenance utilities

These scripts regenerate derived Functional inputs; they are not evaluator
entry points and do not run benchmarks.

- `build_sysbench_configs.py` derives the 14 reduced Sysbench OLTP-RW configs
  and their suite manifest from the canonical paper configs. It deliberately
  removes settings for unrelated benchmark adapters.
- `extract_recorded_traces.py` extracts compact, credential-free provider
  response traces from a completed Functional result directory.
- `build_paper_input_manifest.py` regenerates or strictly checks the SHA-256
  manifest for every retained archived paper-result file.

Run any script from any working directory. Paths are resolved relative to
the repository root.
