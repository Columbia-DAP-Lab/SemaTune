# Committed C1–C4 provider-response baseline

This portable bundle was extracted from the completed provider-backed scoped
C1–C4 validation run. It contains the 15 Actor/Speculator response traces used
by the TuxBot jobs, including roles, decisions, response delays, convergence
metadata, token counts, and source-history SHA-256 values. It contains no API
credential and no replay workload measurements.

`manifest.json` maps every TuxBot job to a trace and authenticates each file.
`claim_report.json` and `tables/improvement_factors.csv` retain the provider-run
aggregate and disaggregated baseline used by the replay comparison report.

Reviewers normally use the wrapper from the repository root:

```bash
reproduction/replay_claims.sh --dry-run
reproduction/replay_claims.sh --run
```

The run still executes all 21 configurations and all 1,050 workload windows.
Fixed and MLOS execute normally; only the 15 hosted-model response streams are
served from this bundle. No hosted-model request is permitted.

Maintainers can rebuild the bundle from another complete provider-backed run:

```bash
.venv-functional/bin/python reproduction/build_trace_baseline.py \
  --source-dir /path/to/completed/provider-results \
  --output-dir reproduction/trace_baselines/c1_c4_provider \
  --force
```
