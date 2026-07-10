# Tailbench (Xapian) load-related parameters

Use these in your **JSON config** when running via the optimizer, or set the **environment variables** when running the binary directly.

## JSON config keys (optimizer)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `tailbench_data_root` | string | Root for datasets (terms.in, wiki, etc.). Relative to repo or absolute. | `"deps/Tailbench/tailbench.inputs"` |
| `tailbench_xapian_db_path` | string | Xapian DB path (wiki index). | `"deps/Tailbench/tailbench.inputs/xapian/wiki"` |
| `tailbench_threads` | int | Number of worker threads (`-n` for xapian). | `40` |
| `tailbench_qps` | int | Target QPS (TBENCH_QPS). | `2500` |
| `tailbench_warmupreqs` | int | Warmup requests before measurement. | `2500` |
| `tailbench_maxreqs` | int | Max requests; use large value for long runs. | `10000000000` |
| `tailbench_minsleepns` | int | Min sleep between requests (ns). | `0` |
| `tailbench_metrics_interval_sec` | int | Interval (seconds) for emitting interval CSVs. | `1` |
| `tailbench_output_dir` | string or null | Dir for lats.bin, lats_interval_*.csv; null = workdir. | `null` |

Paths: if not absolute, they are resolved relative to the repo root (`OS_PARAM_TUNING_ROOT`).

## Raw command (env vars)

When running `xapian_integrated` by hand, set load-related env vars and CLI args:

```bash
cd /mydata/os-param-tuning/deps/Tailbench/tailbench/xapian

export LD_LIBRARY_PATH="./xapian-core-1.2.13/install/lib:${LD_LIBRARY_PATH}"

# Dataset (required)
export TBENCH_TERMS_FILE="/mydata/os-param-tuning/deps/Tailbench/tailbench.inputs/xapian/terms.in"

# Load-related (optional; these match the JSON keys above)
export TBENCH_QPS=2500
export TBENCH_WARMUPREQS=2500
export TBENCH_MAXREQS=10000000000
export TBENCH_MINSLEEPNS=0
export TBENCH_METRICS_INTERVAL_SEC=1

taskset -c 0-9 ./xapian_integrated \
  -n 40 \
  -d /mydata/os-param-tuning/deps/Tailbench/tailbench.inputs/xapian/wiki \
  -r 1000000000
```

- `-n` = threads (overrides are app-specific; env is used by the harness where applicable).
- `-d` = Xapian DB path.
- `-r` = max requests.

Adjust `TBENCH_*` and `-n` / `-r` to control load.
