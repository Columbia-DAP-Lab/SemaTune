# TuxBot paper claims and artifact evidence map

This is the authoritative map from C1–C4 to archived evidence, fresh evaluator
workflows, and paper Plots 6, 7, and 10. Artifact Functional starts under
`functional_example/`; it is separate from performance-claim validation.

## Evaluator workflow

From the repository root:

```bash
reproduction/reproduce_claims.sh --dry-run
reproduction/reproduce_claims.sh --dry-run --extended
reproduction/reproduce_claims.sh --dry-run --full

# Run one tier; add exactly one of --extended or --full when desired.
reproduction/reproduce_claims.sh --run --clean \
  --output-dir results/reproduced_core
```

The default selects 21 configurations for the smallest three-workload C1–C4
direction check. `--extended` selects 60 configurations and fully populates
Plots 6, 7, and 10 on Silo, TPC-C, and Sysbench OLTP-RW. `--full` selects 164
configurations, expands the Plot 6/7 matrix to all 11 selected workloads, and
keeps Plot 10 on the three-workload set. The full command warns that execution
can take several days and consume substantial hosted-model quota. All tiers
run once and resume only strictly complete histories.

For fresh Plot 10, TuxBot uses 2/8/16/41 knobs and TuxBot-Trim/MLOS use
2/8/16. No fresh tier schedules 4 knobs. Trim/MLOS at 41 are omitted because
their high-dimensional optimizer iterations stall beyond the reviewer budget.
See `reproduction/README.md` for exact matrices, trace fallback, and outputs.

Each `generate_plot_N.sh` can also be run separately and accepts an output
directory as its first argument. `SEMATUNE_RESULTS_ROOT` can point at a different
archive with the same layout. A generated PDF alone is not a pass: the validator
checks the generated CSV against the numerical claims in the paper and checks
the expected workload count.

Current archive status (audited 2026-07-10): **the paper-era inputs are now
isolated under `all_results/paper_evaluation/`. All seven plots regenerate and
pass the `measured` validation profile. The optional strict `paper` profile
intentionally flags the documented accepted-paper versus measured-history
differences in Plots 8 and 9. Plot 7 uses all five recovered March 13 measured
Twitter/System histories rather than the paper-era synthetic IPC-plus-five-
percentage-point substitution. Plot 11 computes Top-1/Top-3 from archived histories and sources the missing
Sysbench App/No-Memory series from the regular App-only paper run. The latency
table reuses its submitted CSV.**

## C1–C4 claim map

| ID | Accepted-paper observation | Fresh pass condition | Latest one-repeat observation | Required plot |
|---|---|---|---|---|
| C1 | TuxBot improves stable performance by 72.49% over Default Parameters across 13 workloads. | Stable TuxBot/Default aggregate is above 1.0. | 11-workload extension: **1.4580× (+45.80%), consistent**. | Plot 6 |
| C2 | TuxBot improves performance by 153.3% relative to application-metric MLOS. | Stable TuxBot/MLOS aggregate is above 1.0. | 11-workload extension: **2.3334× (+133.34%), consistent**. | Plot 6 |
| C3 | System-metric TuxBot outperforms application-metric MLOS by 93.7%. | Stable System-TuxBot/MLOS-App aggregate is above 1.0. | 11-workload extension: **2.3761× (+137.61%), consistent**. | Plot 7 |
| C4 | TuxBot remains effective at 41 knobs; the paper reports +155.9%. | The measured 41-knob stable TuxBot/Default aggregate is finite and above 1.0. | Three workloads: **1.1952× (+19.52%), consistent**. | Plot 10 |

The fresh values above were audited on 2026-07-14. They are stochastic
one-repeat observations, not substitutions for the accepted-paper values.
`c123_family_report.{md,json}` and `c4_method_report.{md,json}` are the
machine-generated sources of truth. C1 and C2 share Plot 6; C3 uses Plot 7;
C4 uses Plot 10—there is intentionally not one PDF per claim.

The dual-versus-single cost, tuning robustness, and cross-run memory analyses
remain documented below and are reproducible through the complete workflow;
they are outside the time-bounded C1–C4 badge request.

## Plot 6 — end-to-end performance and catastrophic-region avoidance

- Paper label: `fig:retry_aggregate_improvement`
- Wrapper: `scripts/artifact_plots/generate_plot_1.sh`
- Fresh wrapper: `reproduction/reproduce_claims.sh --extended`
- Plot program: `scripts/plot_retry_aggregate_improvement.py`
- Reference: `paper_evaluation_plots/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf`
- Result roots:
  - TuxBot reruns: `all_results/paper_evaluation/results_config_full_param_*_retry`
  - fixed/classical fallbacks: `all_results/paper_evaluation/results_config_full_param_*_new`
- Expected full-set stable values: TuxBot +72.49%, TuxBot-Trim -26.14%,
  MLOS -31.91%, Bayesian -37.46%, DQN -44.49%, Q-learning -58.49%.
- Expected non-catastrophic stable values (11 workloads, excluding Xapian and
  Memcached): TuxBot +87.22%, TuxBot-Trim +63.93%, MLOS +50.52%.
- Current status: **PASS**. The wrapper uses canonical lowercase
  `sibench_hi_p99`, requires 13/11 workloads, and regenerates +58.93%/+73.01%
  for TuxBot versus +59.36%/+72.49% in the paper. The measured
  non-catastrophic stable value is +88.07% versus the submitted +87.22%.

## Plot 7 — direct application metrics versus indirect system signals

- Paper label: `fig:retry_indirect_aggregate_improvement`
- Wrapper: `scripts/artifact_plots/generate_plot_2.sh`
- Fresh wrapper: `reproduction/reproduce_claims.sh --extended`
- Plot program: `scripts/plot_retry_aggregate_improvement.py`
- Reference: `paper_evaluation_plots/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf`
- Result roots: the same retry/fallback roots as Plot 6.
- Required tuner subdirectories include:
  - `llm_dual_app_metrics_final_actor`
  - `llm_dual_system_metrics_plain_final_actor`
  - `llm_dual_ipc_final_actor`
  - `mlos_trimming_aggressive[_ipc|_cache_misses_max]` (or the documented aliases)
  - `mlos_50_tuning_only`, `mlos_ipc_50_tuning_only`, and
    `mlos_cache_misses_50_tuning_only` (or aliases)
- Expected stable values on the full set: TuxBot App +72.49%, System +31.89%,
  IPC +16.19%; MLOS App -31.91%, IPC -67.76%, Cache -64.66%.
- Expected non-catastrophic stable values: App +87.22%, System +64.04%, IPC
  +47.21%; MLOS App +50.52%, IPC -24.09%, Cache -22.77%.
- Measured-regeneration values are System +20.60%/+36.31% during tuning/stable
  on all 13 workloads and +48.49%/+69.82% on the 11-workload non-catastrophic
  subset. These use the recovered Twitter measurements and canonical TPC-C
  System directory rather than historical `copy` backups.
- Current status: **PASS using recovered measured Twitter results**. All five
  March 13 Twitter/System histories are retained under
  `llm_dual_system_metrics_plain_final_actor_recovered_20260313`; the wrapper
  prefers that directory for Twitter and uses the normal System directory for
  every other workload. No result is omitted and no synthetic proxy is applied.
- Result-directory resolution prefers the exact requested directory and uses a
  sibling ending in ` copy` only as a fallback. This prevents the misplaced
  Twitter backup under TPC-C from being interpreted as TPC-C measurements.
- Historical note: the submitted plotting code replaced each Twitter IPC
  improvement with `IPC + 5` percentage points. The recovered measurements are
  +10.77% during tuning and +11.53% when stable, close to the proxy's +10.91%
  and +12.47%. The measured aggregate is +20.60%/+36.31% during tuning/stable,
  close to the submitted +21.74%/+31.89%; the artifact reports the measured
  regeneration rather than silently forcing the paper values.

## Plot 8 — dual-loop versus single-loop quality and cost

- Paper label: `fig:dual_vs_single`
- Wrapper: `scripts/artifact_plots/generate_plot_3.sh`
- Plot program: `scripts/plot_dual_vs_single_cost.py`
- Reference: `paper_evaluation_plots/dual_vs_single_cost_geomean_error_bars.pdf`
- Result roots: the same retry/fallback roots as Plot 6.
- Required tuner subdirectories: `llm_dual_app_metrics_final_actor`,
  `llm_reasoning_app_metrics_final_actor`, `llm_app_metrics_final_actor`,
  `mlos_trimming_aggressive`/`mlos_trimming`, and `mlos_50_tuning_only`/`mlos`.
- Cost input: set `SEMATUNE_COST_SUMMARY_CSV` to the per-session cost CSV used
  for the paper. The LaTeX command names
  `tmp/sample_dual_vs_single_cost_20260323/dual_vs_single_per_run_costs_robust.csv`,
  but that file is not in this branch. Costs must be derived from archived token
  usage using a documented pricing snapshot, not only from constants in the plot
  script.
- Current status: **REGENERATES; documented numerical drift**. All method families are retained and the generated
  performance values match the paper at displayed precision except the same
  small full-set TuxBot drift as Plot 6 and the measured non-catastrophic
  TuxBot value (+88.07% versus +87.2%). The original sampled-session cost CSV
  is absent; `artifact/reference_data/dual_vs_single_costs.csv` reconstructs its
  displayed totals from the accepted manuscript and labels that provenance explicitly.
- Historical cross-check: with the full `origin/sosp` roots and corrected
  SIbench case, every paper value agrees at displayed precision except full-set
  TuxBot (58.7%/72.3% regenerated versus 59.4%/72.5% stated). The scatter
  positions are reproduced by hard-coded per-session costs, but summed-history
  costs and cost/action are wrong without the missing sampled-session CSV.

## Plot 9 — tuning-phase robustness

- Paper label: `fig:retry_robustness`
- Wrapper: `scripts/artifact_plots/generate_plot_4.sh`
- Plot program: `scripts/plot_retry_robustness_aggregate_single.py`
- Reference:
  `paper_evaluation_plots/retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf`
- Result roots: retry/fallback roots from Plot 6; this plot requires TuxBot,
  TuxBot-Trim, and MLOS to coexist for every included workload.
- Current status: **REGENERATES; documented numerical drift**. After removal of
  stale result copies, the 12-workload command regenerates 18.0%/11.9%/11.3%
  for TuxBot, 33.0%/32.8%/92.0% for TuxBot-Trim, and
  33.2%/30.9%/103.1% for MLOS.
- Numerical inconsistency requiring a paper decision:
  - The LaTeX command summary and reference CSV report TuxBot
    19.3% P50, 11.7% P10, 11.0% variability; TuxBot-Trim 33.0%, 32.8%,
    92.0%; MLOS 33.2%, 30.9%, 103.1% (12 workloads).
  - The active prose instead states 16.9%/12.0%/11.1%,
    29.7%/29.6%/24.7%, and 28.8%/26.3%/25.1%, and then discusses 13 workloads
    including Memcached even though the plotted command lists 12 and excludes
    Memcached.
  Recompute once from an explicitly named workload set, update either the prose
  or figure, and keep the generated CSV as the source of truth.
- Historical cross-check: the complete `origin/sosp` inputs reproduce the
  12-workload command-summary values exactly. Excluding the entire Xapian
  workload with `--aggregate-exclude-experiments xapian_hi_p99` reproduces the
  active prose values exactly. The reference PDF is the Xapian-included version,
  so this is a paper/figure choice rather than numerical noise.

## Plot 10 — parameter-count scaling

- Paper label: `fig:param_ablation`
- Wrapper: `scripts/artifact_plots/generate_plot_5.sh`
- Fresh wrapper: `reproduction/reproduce_claims.sh --extended`
- Plot program: `scripts/plot_ablation_param_aggregate.py`
- Reference: `paper_evaluation_plots/ablation_param_geomean.pdf`
- Expected layout under each source root:
  `.../ablation_params/<N>_param/<workload>/<tuner>/*.json`, for N in
  1, 2, 4, 8, 16, 32, and 41 and workloads Silo, TPC-C, and Sysbench OLTP-RW.
  This is the archived paper layout; fresh reviewer tiers intentionally select
  only the counts documented below and never schedule 4 knobs.
- Historical roots are
  `results_params/{silo_hi_p99_final,tpcc_p99_final,sysbench_oltp_rw_final}`;
  they are retained under `all_results/paper_evaluation/results_params/`.
- Fixed roots and 8-knob fallback roots are named explicitly in the wrapper.
- Current status: **PASS**. All 42 displayed values regenerate from the isolated
  paper roots. The restored paper script contains three non-data
  adjustments: fixed +5%/+7% TuxBot-Trim points at 1/2 knobs, a -20 percentage
  point adjustment to 41-knob Silo trimming, and a +3 point TPC-C trimming
  fallback. Evaluators must be told why these are justified or, preferably, the
  underlying raw runs should replace them.
- Fresh scoped status: **CONSISTENT**. TuxBot's 41-knob stable aggregate is
  +19.52% over Default across Silo, TPC-C, and Sysbench OLTP-RW. TuxBot-Trim
  and MLOS at 41 knobs are intentionally empty because their high-dimensional
  iterations exceed the scoped reviewer budget; both methods are measured at
  2, 8, and 16 knobs.
- Expected TuxBot stable series: -4.5, +15.7, +314.1, +216.7, +213.4,
  +105.2, +155.9%. Expected MLOS stable series: +3.2, +12.5, +119.3, +76.3,
  +19.6, +28.3, +13.0%.
- Historical cross-check: the full paper-era inputs reproduce all 42 displayed
  values and the reference PDF's data streams, subject to the manual adjustments
  above. The neighboring checked-in CSV is stale and is not a valid oracle.

### Response-latency table associated with Plot 10

- Paper label: `tab:latency_by_params`
- Measurement program: `scripts/measure_replayed_tuner_latency_by_params.py`
- Checked-in reference CSV: `paper_evaluation_plots/latency_by_params.csv`
- Reuse/validation command: `scripts/artifact_plots/reuse_latency_table.sh OUTPUT_DIR`
- This is **not an offline result-plot command**: it replays archived prompts
  against live model APIs and therefore depends on model availability, API keys,
  cost, and service latency. The paper also substitutes a 16-knob Silo history
  for the missing 8-knob Reasoning run, while MLOS values come from separate
  Sysbench CPU timing runs. Keep this as an extended/reusable experiment, not a
  required Functional-badge command. Per maintainer direction, the Functional
  workflow reuses the submitted CSV and validates every table cell; live replay
  is optional.

## Plot 11 — cross-run memory on unseen workloads

- Paper label: `fig:memory_subsection_combined`
- Wrapper: `scripts/artifact_plots/generate_plot_6.sh`
- Plot program: `scripts/plot_rag_memory_app_system_split.py`
- Reference: `paper_evaluation_plots/rag_memory_app_system_geomean.pdf`
- Results: `all_results/paper_evaluation/results_rag/{silo,tpcc,sysbench_oltp}`.
- Required per-workload series: fixed, App/System No Memory, App/System Top-1,
  and App/System Top-3 unseen-workload memory.
- Current status: **PASS with the regular baseline**. All Top-1 and Top-3 App/System
  bars are computed from the retained Silo, TPC-C, and Sysbench OLTP histories.
  The memory-specific Sysbench directory lacks only
  `llm_dual_app_metrics_final_actor`, so the wrapper uses the same regular
  Sysbench App-only history and fixed baseline as Plot 6 and writes
  `PLOT_11_REGULAR_BASELINE.txt`. No memory values are imputed or replaced by
  constants. The validator checks all 12 aggregate values, all three workload
  counts, and the regenerated PDF/CSV.
- With the regular Sysbench baseline, App No Memory recomputes to
  +87.62%/+145.39% rather than the paper's +86.30%/+144.67%; the four Top-1/Top-3
  memory series still match the paper exactly.

## Plot 12 — motivation examples

- Paper label: `fig:mlos_motivation_examples`
- Wrapper: `scripts/artifact_plots/generate_plot_7.sh`
- Plot program: `scripts/plot_mlos_motivation_examples_combined.py`
- Reference: `paper_evaluation_plots/mlos_motivation_examples_combined.pdf`
- Claims: on Wikipedia, optimizing IPC or cache misses with MLOS yields about
  2x worse p99 latency than optimizing the app metric; on TPC-C, expanding from
  one to 32 knobs degrades MLOS p99 latency by about 50%.
- Required inputs:
  - Wikipedia root with `fixed`, `mlos_50_tuning_only`,
    `mlos_ipc_50_tuning_only`, and `mlos_cache_misses_50_tuning_only`.
  - TPC-C fixed root and 1/2/8/32-knob MLOS result roots under the parameter
    hierarchy described for Plot 10.
- Current status: **PASS**. The Wikipedia and TPC-C histories are retained under
  `all_results/paper_evaluation/`; the validator checks all seven plotted means.
- Historical cross-check: the full `origin/sosp` roots reproduce the plotted
  coordinates. Wikipedia stable p99 is 34.37 ms for App, 73.46 ms for IPC, and
  77.92 ms for Cache (Default 41.67 ms); TPC-C is 79.05 ms at 1 knob and
  109.89 ms at 32 knobs (Default 77.67 ms). The latest historical script differs
  from the submitted reference only in the TPC-C bar colors.

## Empirical tables and non-result figures

- `tab:memcached_mlos_story` is out of scope by maintainer direction.
- `fig:online_loop`, `fig:system_diagram`, `fig:prompt_structure`, and
  `fig:dual_loop_context` are design/architecture figures. They do not validate
  performance claims and are not included in `generate_all.sh`.
- The model-backend and tuning-budget figures were inactive in the accepted paper sources;
  they may be supplementary artifact experiments but are not required to
  reproduce the submitted paper's active claims.

## Reference-plot comparison notes

The filenames included by active LaTeX exist under `paper_evaluation_plots`, but
the neighboring CSVs are not always the data for those exact PDFs. In
particular, `retry_aggregate_improvement_geomean.csv` uses an older workload set
(`otmetrics_p99` and a DCPerf MediaWiki name) and does not correspond to the
active 13-workload `_with_and_without_xapian.pdf` command. Comparison should
therefore be numerical against the active LaTeX claims and generated CSV first,
then visual against the PDF. Do not treat a visually similar PDF or a stale CSV
as claim validation.

The parallel validation used the complete paper-era roots extracted from
`origin/sosp` into `all_results/paper_evaluation/`. Plot 10 matched the reference data
streams; Plot 9 matched the active reference data geometry; Plot 12 matched the
reference geometry with a color-only difference. Plots 6–8 and 11 were compared
numerically because their adjacent CSVs are stale or missing. Exact PDF hashes
are not suitable AE checks because metadata and Matplotlib layout can change.

## Documented limitations

- The artifact reports the five recovered measured Twitter/System histories
  instead of the submitted plot's synthetic IPC-plus-five-point proxy.
- Plot 9 retains the active reference figure's 12-workload calculation; its
  Xapian-excluded prose values are documented above as a paper/figure discrepancy.
- Plot 8's displayed cost totals are reconstructed from the accepted manuscript
  because the original sampled-session CSV was not retained.
- Plot 10 preserves and discloses the paper script's manual adjustments. The raw
  histories remain available so evaluators do not have to infer them.
- Full multi-day reruns are optional extended experiments and are not part of
  the Functional workflow.
