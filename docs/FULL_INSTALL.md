# Full benchmark dependency installation

This is for broader Results-Reproduced experiments, not the minimal Sysbench
Functional check. The provided preconfigured CloudLab host is recommended.

From a recursive Git checkout or materialized archive:

```bash
scripts/setup.sh --full
```

This installs the base environment, builds the pinned Mutilate binary, builds
TailBench Masstree/Silo/Sphinx/Xapian with their inputs, and installs
DCPerf/SparkBench with Spark 2.4.5, the approximately 109 GB dataset, and a
populated warehouse.

To configure the full-install host as the server in a two-node Mutilate
deployment, provide both internal addresses:

```bash
scripts/setup.sh --full \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
```

The load-generator node should use the much smaller client-only mode instead
of installing TailBench and SparkBench:

```bash
scripts/setup.sh --memcached-client \
  --server-ip 10.10.1.2 --client-ip 10.10.1.3
```

SparkBench needs at least 300 GiB free and initial population can take up to
three hours. TailBench's 10.23 GB upstream input archive has no
publisher-provided digest; its locked size and safe archive layout are checked.

Individual setup commands:

```bash
scripts/setup_tailbench.sh \
  --data-root /mydata/SemaTune-tailbench/tailbench.inputs

scripts/setup_sparkbench.sh --with-dataset \
  --data-root /mydata/SemaTune-sparkbench
```

`--full` builds Mutilate locally. Paired network arguments additionally create
the ignored runtime environment and prepare memcached on the server; addresses
are never written into tracked configurations. For the standalone server mode,
real-API Functional run, generated summaries, and client service operations, see
[`MUTILATE_README.md`](../src/optimizer/benchmarks/MUTILATE_README.md) and
[`MUTILATE_INTERNAL_NETWORK_SETUP.md`](../src/optimizer/benchmarks/MUTILATE_INTERNAL_NETWORK_SETUP.md).
