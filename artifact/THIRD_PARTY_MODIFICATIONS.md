# Third-party source boundaries and modifications

All TuxBot-authored implementation is under `src/`. The `deps/` entries are
pinned Git submodules retained as complete, indivisible third-party snapshots
so their source, build files, history, and license notices stay together. Some
upstream applications in those snapshots are not executed by the artifact;
they are dependency source, not TuxBot artifact components.

## Exact revisions

| Dependency | Pinned revision | Upstream base | TuxBot delta at the pinned revision |
| --- | --- | --- | --- |
| BenchBase | `54d30feb1f9c8b88cca7715fc19de1622cfd1b82` | Same commit in `cmu-db/benchbase` | None. The configured fork has later development commits, but they are not in the pinned artifact revision. |
| TailBench | `2f3098b539a9a3413086fc77e29637937bafd116` | `c3bf142d25224af8a74d06b91c1a84b0e5b5e1d4` | One mode-only change: `tailbench-setup.sh` is executable (`100644` to `100755`). |
| DCPerf | `5d8d16d63cf28311ee85a2f63ce7506ade67dbef` | Same upstream commit | None. |
| Mutilate | `d65c6ef7c2f78ae05a9db3e37d7f6ddff1c0af64` | Same upstream commit | None. Setup translates a disposable copy of its Python-2-style `SConstruct`; the pinned source remains unchanged. |

The fork URLs, licenses, and relationships are listed in `deps/README.md`.
The two nonzero adaptation points above can be inspected locally with:

```bash
git -C deps/Tailbench diff --summary \
  c3bf142d25224af8a74d06b91c1a84b0e5b5e1d4..2f3098b539a9a3413086fc77e29637937bafd116

rg -n "lib2to3|disposable copy" scripts/setup.sh
```

For every submodule, `git submodule status` prints the artifact-pinned commit.
`scripts/setup.sh` verifies those commits before building a Git checkout; the
materialized archive is protected by its release checksums.
