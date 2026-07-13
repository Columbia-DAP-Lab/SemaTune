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
  --archived-plots-dir results/reproduced_core/archived/plots
```

[`plot_claims.py`](../reproduction/plot_claims.py) generates the four fresh
C1–C4 PDFs and their CSV inputs. It uses the paper colors and aggregation
formula, but does not invent error bars for one repetition. The archived phase
uses [`plot_all.sh`](../reproduction/plot_all.sh) with paper Plots 1, 2, and 5.

## Paper plots

Activate the locked environment and generate all seven from archived histories:

```bash
source .venv-functional/bin/activate
OUT=/tmp/sematune-paper-plots
SEMATUNE_VALIDATION_MODE=measured \
  scripts/artifact_plots/generate_all.sh "$OUT"
```

| Plot | Wrapper | Program | Reference |
| --- | --- | --- | --- |
| End-to-end performance | [`generate_plot_1.sh`](../scripts/artifact_plots/generate_plot_1.sh) | [`plot_retry_aggregate_improvement.py`](../scripts/plot_retry_aggregate_improvement.py) | [`retry_aggregate_improvement_geomean_with_and_without_xapian.pdf`](../paper_evaluation_plots/retry_aggregate_improvement_geomean_with_and_without_xapian.pdf) |
| Application vs. system signals | [`generate_plot_2.sh`](../scripts/artifact_plots/generate_plot_2.sh) | [`plot_retry_aggregate_improvement.py`](../scripts/plot_retry_aggregate_improvement.py) | [`retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf`](../paper_evaluation_plots/retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf) |
| Dual vs. single and cost | [`generate_plot_3.sh`](../scripts/artifact_plots/generate_plot_3.sh) | [`plot_dual_vs_single_cost.py`](../scripts/plot_dual_vs_single_cost.py) | [`dual_vs_single_cost_geomean_error_bars.pdf`](../paper_evaluation_plots/dual_vs_single_cost_geomean_error_bars.pdf) |
| Tuning robustness | [`generate_plot_4.sh`](../scripts/artifact_plots/generate_plot_4.sh) | [`plot_retry_robustness_aggregate_single.py`](../scripts/plot_retry_robustness_aggregate_single.py) | [`retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf`](../paper_evaluation_plots/retry_robustness_memory_tuxbot_mlos_1_30_aggregate.pdf) |
| Parameter scaling | [`generate_plot_5.sh`](../scripts/artifact_plots/generate_plot_5.sh) | [`plot_ablation_param_aggregate.py`](../scripts/plot_ablation_param_aggregate.py) | [`ablation_param_geomean.pdf`](../paper_evaluation_plots/ablation_param_geomean.pdf) |
| Cross-run memory | [`generate_plot_6.sh`](../scripts/artifact_plots/generate_plot_6.sh) | [`plot_rag_memory_app_system_split.py`](../scripts/plot_rag_memory_app_system_split.py) | [`rag_memory_app_system_geomean.pdf`](../paper_evaluation_plots/rag_memory_app_system_geomean.pdf) |
| Motivation | [`generate_plot_7.sh`](../scripts/artifact_plots/generate_plot_7.sh) | [`plot_mlos_motivation_examples_combined.py`](../scripts/plot_mlos_motivation_examples_combined.py) | [`mlos_motivation_examples_combined.pdf`](../paper_evaluation_plots/mlos_motivation_examples_combined.pdf) |

Generate one plot with `scripts/artifact_plots/generate_plot_N.sh "$OUT"`.

For compatible dummy/new histories:

```bash
source .venv-functional/bin/activate
SEMATUNE_RESULTS_ROOT=/path/to/compatible-result-tree \
SEMATUNE_VALIDATION_MODE=none \
  scripts/artifact_plots/generate_plot_1.sh /tmp/dummy-plot-1
```

The result tree must match the workload/method layout and JSON history schema
under `all_results/paper_evaluation/`. Detailed provenance is in
[`artifact/PAPER_CLAIMS_AND_PLOTS.md`](../artifact/PAPER_CLAIMS_AND_PLOTS.md).
