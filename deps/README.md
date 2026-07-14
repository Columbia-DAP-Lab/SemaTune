# Third-party source map

SemaTune-authored code is under `src/`; the directories here are unmodified or
paper-specific third-party source snapshots except where noted in their own
Git histories. Their upstream licenses remain authoritative.

| Directory | Origin | Pinned commit | License | SemaTune relationship |
| --- | --- | --- | --- | --- |
| `benchbase` | paper fork <https://github.com/gliargovas/benchbase> of CMU BenchBase | `54d30feb1f9c8b88cca7715fc19de1622cfd1b82` | Apache-2.0 | The pinned revision is an unchanged upstream commit; later fork development is not included. |
| `Tailbench` | paper fork of TailBench | `2f3098b539a9a3413086fc77e29637937bafd116` | Composite; nested notices | Differs from base `c3bf142d25224af8a74d06b91c1a84b0e5b5e1d4` only by making `tailbench-setup.sh` executable. |
| `DCPerf` | <https://github.com/facebookresearch/DCPerf> | `5d8d16d63cf28311ee85a2f63ce7506ade67dbef` | MIT plus component licenses | Used through the SemaTune DCPerf adapter. |
| `mutilate` | <https://github.com/leverich/mutilate> | `d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64` | BSD-3-Clause | Used as an external Memcached load generator. |

`scripts/setup.sh --base` prepares Sysbench, PostgreSQL, and BenchBase for the
Functional workflow. `scripts/setup.sh --full` additionally materializes all
four pinned source trees, installs the retained native dependency closure,
builds Mutilate plus the four retained TailBench workloads and their inputs,
and installs the verified Spark runtime, dataset, storage layout, and populated
warehouse. The standalone `--memcached-server` and `--memcached-client` modes
materialize only the dependencies needed by each Mutilate role and generate
allocation-specific configuration from explicit network arguments; see the
root README and machine-readable provenance.

The exact upstream bases, modification commands, and rationale for retaining
complete dependency snapshots are documented in
[`artifact/THIRD_PARTY_MODIFICATIONS.md`](../artifact/THIRD_PARTY_MODIFICATIONS.md).
