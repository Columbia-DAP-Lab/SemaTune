# Plot generation

## Functional outputs

For a completed Functional result directory:

```bash
functional_example/plot.sh \
  --results-dir results/functional_sysbench_real \
  --output-dir results/functional_sysbench_real/plots
```

[`plot_tpcc_suite.py`](../functional_example/plot_tpcc_suite.py) produces the aggregate 14-method plot.
[`plot_paper_style_equivalents.py`](../functional_example/plot_paper_style_equivalents.py) produces the Figure 6/7/8/9 Functional
equivalents. These are operational demonstrations, not paper validators.

## Scoped Results Reproduced outputs

The recommended C1–C4 command plots automatically. To rerun only its fresh
aggregation after all 21 jobs have completed:

```bash
source .venv-functional/bin/activate
python reproduction/plot_claims.py \
  --results-dir results/reproduced_core/fresh/raw \
  --output-dir results/reproduced_core/fresh \
  --report-dir results/reproduced_core \
  --archived-plots-dir results/reproduced_core/archived/plots \
  --manifest reproduction/claim_manifest.json
```

[`plot_claims.py`](../reproduction/plot_claims.py) generates the four fresh
C1–C4 PDFs and their CSV inputs. It uses the paper colors and aggregation
formula, but does not invent error bars for one repetition. The archived phase
uses [`plot_all.sh`](../reproduction/plot_all.sh) with paper Plots 6, 7, and 10.

For the optional 11-workload, provider-only C1–C3 family extension, plotting is
also automatic. To regenerate it from completed histories:

```bash
.venv-functional/bin/python reproduction/plot_c123_families.py \
  --results-dir results/reproduced_core/fresh/raw \
  --output-dir results/reproduced_core/fresh \
  --report-dir results/reproduced_core \
  --manifest reproduction/c123_family_manifest.json

.venv-functional/bin/python reproduction/validate_c123_families.py \
  --output-dir results/reproduced_core
```

These are the two evaluation Plots 6/7 equivalents, not one plot per claim. They
contain all selected workloads and an aggregate excluding Xapian.
Their `c123_*.csv` inputs retain every workload, including Xapian, and encode
whether the primary metric is minimized or maximized. The wrapper delegates to
the same [`plot_retry_aggregate_improvement.py`](../scripts/plot_retry_aggregate_improvement.py)
program used for paper Plots 6 and 7. The complete original method grid, page
dimensions, bar width, labels, colors, typography, spacing, and
with/without-Xapian layout are retained. Unavailable methods remain empty.

For the three-application Plots 6/7 matrix with all requested TuxBot,
TuxBot-Trim, and MLOS signal variants, plotting is performed only after the
strict 30-job workflow completes:

```bash
.venv-functional/bin/python reproduction/plot_three_app_paper.py \
  --results-dir results/reproduced_core/fresh/raw \
  --output-dir results/reproduced_core/fresh \
  --report-dir results/reproduced_core \
  --manifest reproduction/three_app_plots_6_7_manifest.json
```

This regenerates the same canonical Plots 6/7 filenames. It requires all three
workloads for every requested method, leaves only the out-of-scope Plot 6
methods empty, and rejects any PDF whose page dimensions differ from the paper
references.

After C1–C3, the measured C4 comparison of TuxBot, MLOS, and TuxBot-Trim at
2, 8, 16, and 41 knobs is also plotted automatically. To regenerate only its
plot and disaggregated CSVs from completed histories:

```bash
.venv-functional/bin/python reproduction/plot_c4_methods.py \
  --results-dir results/reproduced_core/fresh/raw \
  --output-dir results/reproduced_core/fresh \
  --report-dir results/reproduced_core \
  --manifest reproduction/c4_method_manifest.json
```

This wrapper invokes the paper Plot 10 program
[`plot_ablation_param_aggregate.py`](../scripts/plot_ablation_param_aggregate.py)
with its exact styling and with `--measured-trim-only`. Consequently it accepts
no historical Trim overrides, workload adjustments, or proxy values. TuxBot
must have three measured workload rows at every knob count; TuxBot-Trim and
MLOS must have three at 2, 8, and 16, while both 41-knob points remain empty
because those high-dimensional iterations take too long.

## Paper plots

Activate the locked environment and generate all seven from archived histories:

```bash
source .venv-functional/bin/activate
OUT=/tmp/sematune-paper-plots
SEMATUNE_VALIDATION_MODE=measured \
  scripts/artifact_plots/generate_all.sh "$OUT"
```

| Paper plot | Result | Compatibility wrapper | Program | Reference |
|---:|---|---|---|---|
| 6 | End-to-end performance | [`generate_plot_1.sh`](../scripts/artifact_plots/generate_plot_1.sh) | [`plot_retry_aggregate_improvement.py`](../scripts/plot_retry_aggregate_improvement.py) | [`retry_aggregate_improvement_geomean_with_and_without_xapian.pdf`](../paper_evaluation_plots/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf) |
| 7 | Application vs. system signals | [`generate_plot_2.sh`](../scripts/artifact_plots/generate_plot_2.sh) | [`plot_retry_aggregate_improvement.py`](../scripts/plot_retry_aggregate_improvement.py) | [`retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf`](../paper_evaluation_plots/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf) |
| 8 | Dual vs. single and cost | [`generate_plot_3.sh`](../scripts/artifact_plots/generate_plot_3.sh) | [`plot_dual_vs_single_cost.py`](../scripts/plot_dual_vs_single_cost.py) | [`dual_vs_single_cost_geomean_error_bars.pdf`](../paper_evaluation_plots/dual_vs_single_cost_geomean_error_bars.pdf) |
| 9 | Tuning robustness | [`generate_plot_4.sh`](../scripts/artifact_plots/generate_plot_4.sh) | [`plot_retry_robustness_aggregate_single.py`](../scripts/plot_retry_robustness_aggregate_single.py) | [`retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf`](../paper_evaluation_plots/retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf) |
| 10 | Parameter scaling | [`generate_plot_5.sh`](../scripts/artifact_plots/generate_plot_5.sh) | [`plot_ablation_param_aggregate.py`](../scripts/plot_ablation_param_aggregate.py) | [`ablation_param_geomean.pdf`](../paper_evaluation_plots/ablation_param_geomean.pdf) |
| 11 | Cross-run memory | [`generate_plot_6.sh`](../scripts/artifact_plots/generate_plot_6.sh) | [`plot_rag_memory_app_system_split.py`](../scripts/plot_rag_memory_app_system_split.py) | [`rag_memory_app_system_geomean.pdf`](../paper_evaluation_plots/rag_memory_app_system_geomean.pdf) |
| 12 | Motivation | [`generate_plot_7.sh`](../scripts/artifact_plots/generate_plot_7.sh) | [`plot_mlos_motivation_examples_combined.py`](../scripts/plot_mlos_motivation_examples_combined.py) | [`mlos_motivation_examples_combined.pdf`](../paper_evaluation_plots/mlos_motivation_examples_combined.pdf) |

The wrapper basenames predate the paper's final numbering and are retained for
compatibility; their validation and public selectors use paper Plots 6–12.
Generate a subset with `reproduction/plot_all.sh --plots 6,7,10 ...`.

For compatible dummy/new histories:

```bash
source .venv-functional/bin/activate
SEMATUNE_RESULTS_ROOT=/path/to/compatible-result-tree \
SEMATUNE_VALIDATION_MODE=none \
  scripts/artifact_plots/generate_plot_1.sh /tmp/dummy-paper-plot-6
```

The result tree must match the workload/method layout and JSON history schema
under `all_results/paper_evaluation/`. Detailed provenance is in
[`artifact/PAPER_CLAIMS_AND_PLOTS.md`](../artifact/PAPER_CLAIMS_AND_PLOTS.md).
