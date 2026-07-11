# Third-party source map

SemaTune-authored code is under `src/`; the directories here are unmodified or
paper-specific third-party source snapshots except where noted in their own
Git histories. Their upstream licenses remain authoritative.

| Directory | Origin | Pinned commit | License | SemaTune relationship |
| --- | --- | --- | --- | --- |
| `benchbase` | paper fork <https://github.com/gliargovas/benchbase> of CMU BenchBase | `54d30feb1f9c8b88cca7715fc19de1622cfd1b82` | Apache-2.0 | Used through the SemaTune BenchBase adapter/configs; modifications remain in the fork history. |
| `Tailbench` | paper fork of TailBench | `2f3098b539a9a3413086fc77e29637937bafd116` | Composite; nested notices | Fork used by the workload adapter; modifications remain in the fork history. |
| `DCPerf` | <https://github.com/facebookresearch/DCPerf> | `5d8d16d63cf28311ee85a2f63ce7506ade67dbef` | MIT plus component licenses | Used through the SemaTune DCPerf adapter. |
| `mutilate` | <https://github.com/leverich/mutilate> | `d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64` | BSD-3-Clause | Used as an external Memcached load generator. |

The Functional quick installer does not build these unrelated workloads.
Their acquisition/build requirements and exact versions are retained for the
archived paper experiments in the root README and machine-readable provenance.
