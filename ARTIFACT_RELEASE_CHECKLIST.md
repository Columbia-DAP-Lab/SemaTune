# SemaTune Artifact Release Checklist

Maintainer-only working document. It is intentionally not linked from the
evaluator README and is excluded from `SHA256SUMS`. Delete it before the final
public tag and regenerate the single root commit if additional commits have
been created.

## Completed in the AE preparation branch

- [x] Add the SemaTune MIT `LICENSE`.
- [x] Add `CITATION.cff` with the reserved Zenodo DOI and SOSP ’26 citation.
- [x] Replace the mixed framework README with an evaluator-facing
  Artifact Available document.
- [x] Record the original platform, benchmark gitlinks, internal benchmark
  versions, and third-party licenses recoverable from source/results.
- [x] Remove the unused FleetBench `.gitmodules` entry and setup script.
- [x] Import the accepted-paper memory/RAG implementation, compact configs,
  tests, plotting scripts, and archived memory/model comparison records from
  `origin/sosp` commit `4383a40da65f468fb0895d68cd375912dc909068`.
- [x] Centralize the paper model request IDs and temperature in
  `src/barebones_optimizer/model_versions.py`.
- [x] Select Python 3.10 packages available by 2026-03-30 and generate a
  transitive hash lock with uv 0.11.2.
- [x] Pin the CPU-only PyTorch wheel by exact URL and SHA-256.
- [x] Install the complete hash lock in a disposable Ubuntu 22.04 x86-64
  Python 3.10.12 environment and run the imported memory tests successfully.
- [x] Add download, Ubuntu `.deb`, and payload checksum automation.
- [x] Record the March 30 Ubuntu snapshot, direct package versions, static
  archive hashes, AN4 commit, and DCPerf Git-LFS manifest identity.

## Credentials and publication safety

- [x] Replace every literal API credential in configs and results with an
  environment-variable lookup, including imported RAG/model-comparison files.
- [x] Remove credential values from result metadata while preserving model,
  token, cost, and timestamp provenance.
- [x] Remove the fallback in `scripts/run_model_backends_batch.sh` that reads a
  key from a configuration file; environment variables must be the only source.
- [ ] Revoke every credential that has appeared in a working tree or public
  branch.
- [ ] Scan the final tree and its single commit for secrets. Remember that
  preserving other remote branches also preserves any secrets on those
  branches; coordinate their cleanup separately if necessary.

## Source and result curation

- [ ] Review the imported `origin/sosp` subset and confirm it contains the
  complete source/config/result provenance intended for the public artifact.
- [ ] Fix or explicitly document
  `scripts/plot_rag_memory_app_system_split.py`: it currently contains a
  hard-coded geomean summary and imputes a missing Sysbench comparison value.
- [x] Move retained paper results into the final tidy layout and update paths in
  plotting scripts/manifests.
- [ ] Preserve per-run UTC timestamps and exact returned model identities in
  the curated result manifest.
- [x] Resolve the Tailbench `tailbench-setup.sh` executable-bit difference:
  commit mode `0755` in the fork and update the parent gitlink.

## Locked software and external data

- [ ] Retain the successful `pip freeze --all`, install transcript, and
  smoke-test report with the final release metadata.
- [ ] Resolve the direct packages plus dependency closure using Ubuntu snapshot
  `20260330T235959Z`, download all `.deb` payloads, and generate
  `artifact/apt-packages.lock.json` with
  `scripts/manifest_apt_packages.py`.
- [ ] Record the exact Linux-tools/perf packages matching kernel
  `5.15.0-160-generic` and verify `perf --version` on the AE host.
- [ ] Download the 10,230,769,002-byte Tailbench input archive once with
  `scripts/fetch_artifact_inputs.py record tailbench-inputs`, review the
  computed SHA-256, and insert it into `artifact/downloads.lock.json`.
- [ ] Decide whether the Tailbench input may be redistributed. If not, keep it
  external and document its source/license limitation.
- [ ] Verify the Apache Spark archive against its publisher SHA-512.
- [ ] Check out AN4 commit `b567d132f43304e70a2b2eca6077f22255311812`
  rather than cloning a moving branch.
- [ ] Check out DCPerf-datasets commit
  `afbc2c250aebb0c18e65a685f2b5e454e7d0c03b`, pull the 979 required Git-LFS
  objects, run `git lfs fsck`, and verify the recorded LFS-manifest SHA-256.

## Licensing and citation

- [ ] Add `THIRD_PARTY_NOTICES.md` covering every bundled dependency and data
  source. Preserve all nested Tailbench notices; do not describe Tailbench as
  wholly MIT.
- [ ] Confirm redistribution terms for the Tailbench inputs, DCPerf datasets,
  AN4, BenchBase traces, and every binary/archive included in Zenodo.
- [ ] After the proceedings record exists, add paper DOI, URL, pages, and final
  publisher metadata to `CITATION.cff`.
- [ ] Add the final software version and `date-released` to `CITATION.cff`, then
  validate it with a CFF 1.2 validator.

## Single-commit branch and Zenodo release

- [x] Record other local branch tips before rewriting `sosp-ae`.
- [x] Rewrite only local `sosp-ae` to one parentless root commit and confirm
  `git rev-list --count sosp-ae` prints `1`; confirm other branch tips did not
  change.
- [ ] Force-push only `sosp-ae` using `--force-with-lease`. Do not rewrite or
  force-push another branch.
- [ ] Delete this checklist, finish credential/result cleanup, and regenerate
  the single root commit if any preparation commits were added after the local
  rewrite.
- [ ] Build the Zenodo payload from a recursive checkout; remove VCS metadata,
  this checklist, local caches/build products, and the submitted paper PDF.
- [ ] Generate top-level `SHA256SUMS` last over the exact payload with
  `scripts/artifact_checksums.py generate` and verify it from a clean copy.
- [ ] Create the immutable release tag and record the release version/date in
  the CFF and Zenodo metadata.
- [ ] Publish the release at DOI `10.5281/zenodo.21285693`.
- [ ] Download the published Zenodo archive, verify every checksum/manifest,
  check DOI links and rendered metadata, and confirm the recursive benchmark
  contents are present.
