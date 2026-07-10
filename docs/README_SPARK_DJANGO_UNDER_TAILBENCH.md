# SparkBench and DjangoBench Build Notes (Tailbench Stack)

This README captures how SparkBench and DjangoBench were recently brought up in this repo.

## Scope (Important)

In this project, these two workloads run through `DCPerf` (`deps/DCPerf`), not as native apps inside `deps/Tailbench/tailbench`.

- `SparkBench` here maps to DCPerf job `spark_standalone_local`.
- `DjangoBench` here maps to DCPerf job `django_workload_default` (or `django_workload_quick` in some configs).
- They are used in the same OS tuning workflow as Tailbench benchmarks, but they are separate benchmark implementations.

## Recent Build Snapshot

- Spark walkthrough file timestamp: `2026-02-17 10:00:30 -0600`
- Django setup file timestamp: `2026-02-17 11:29:16 -0600`
- Wrapper script timestamp: `2026-02-17 10:19:20 -0600`
- Spark config commit in this repo: `52e08bf87` (`2026-02-17`)

## Prerequisites

Install base dependencies:

```bash
sudo apt-get update
sudo apt-get install -y openjdk-8-jdk git-lfs python3 siege maven ant cmake gcc fio
git lfs install
pip install -r deps/DCPerf/requirements.txt
```

## 1) SparkBench (DCPerf) Build/Install

1. Install the benchmark from DCPerf:

```bash
cd /mydata/os-param-tuning
./scripts/run_dcperf.sh install spark_standalone_local
```

2. If dataset auto-download fails, install dataset manually:

```bash
cd /mydata/os-param-tuning/deps/DCPerf/benchmarks/spark_standalone/dataset
rm -rf bpc_t93586_s2_synthetic DCPerf-datasets
git clone https://github.com/facebookresearch/DCPerf-datasets
mv DCPerf-datasets/bpc_t93586_s2_synthetic .
rm -rf DCPerf-datasets
```

3. Run SparkBench:

```bash
cd /mydata/os-param-tuning
./scripts/run_dcperf.sh run spark_standalone_local
```

Notes:
- The repo wrapper `scripts/run_dcperf.sh` keeps DCPerf INFO logs visible in console.
- Spark worker cores can be controlled from config via `dcperf_worker_cores` for `benchmark: dcperf_spark`.

## 2) DjangoBench (DCPerf) Build/Install

1. Install the benchmark:

```bash
cd /mydata/os-param-tuning
./scripts/run_dcperf.sh install django_workload_default
```

2. Ensure Cassandra runtime directories exist and are writable:

```bash
sudo mkdir -p /data/cassandra/{data,commitlog,saved_caches,hints}
sudo chown -R "$USER:$USER" /data/cassandra
```

3. If permissions become root-owned after failed/sudo runs, repair:

```bash
sudo chown -R "$USER:$USER" /mydata/os-param-tuning/deps/DCPerf/benchmarks/django_workload /data/cassandra
```

4. Run DjangoBench in standalone mode:

```bash
cd /mydata/os-param-tuning
./scripts/run_dcperf.sh run django_workload_default -r standalone
```

Notes:
- `deps/DCPerf/benchmarks/django_workload/bin/run.sh` includes Java 8 enforcement for Cassandra 3.11.
- `deps/DCPerf/benchmarks/django_workload/bin/run.sh` rewrites to `127.0.0.1` to avoid hostname resolution failures.
- `deps/DCPerf/benchmarks/django_workload/bin/run.sh` clears proxy and `LD_PRELOAD` variables before Siege.
- Per-component pinning is available via `CASSANDRA_TASKSET_CPUS`, `DJANGO_TASKSET_CPUS`, `SIEGE_TASKSET_CPUS`.

## 3) Running Through the OS Tuner

Spark:

```bash
sudo python3 -m src.barebones_optimizer.main \
  --config config/full_param/dcperf_spark_tput/dcperf_spark_config_fixed.json
```

Django:

```bash
sudo python3 -m src.barebones_optimizer.main \
  --config config/full_param/dcperf_django_hi_tput/dcperf_django_config_fixed.json
```

## 4) Existing Detailed Docs

For full troubleshooting details, see:

- `DCPerf_SparkBench_Walkthrough.md`
- `DCPerf_DjangoBench_Setup.md`
