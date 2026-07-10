# Global Tuner Ranking vs Fixed

Sorted by Geomean Mean Improvement.

| Rank # | Tuner name | Geomean Mean Improvement | Geomean Median Improvement |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +191.96% | +188.44% |
| 2 | llm_gemini_2_5_flash_indirect_all_mode2 | +174.87% | +187.02% |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +167.55% | +179.52% |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +154.65% | +177.87% |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +150.16% | +169.54% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +146.90% | +177.76% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual | +138.50% | +175.58% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +104.45% | +175.77% |
| 9 | mlos | +89.40% | +148.69% |
| 10 | llm_gemini_2_5_flash_indirect_all_mode3 | +88.02% | +151.46% |
| 11 | llm_gemini_2_5_flash_ipc | +78.39% | +74.34% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +72.54% | +114.98% |
| 13 | llm_gemini_2_5_flash_ipc copy | +69.22% | +68.26% |
| 14 | llm_gemini_2_5_flash | +67.51% | +66.96% |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +59.84% | +102.62% |
| 16 | llm_gemini_2_5_flash_lite_mlos_trimming | +51.85% | +90.53% |
| 17 | llm_gemini_2_5_flash_lite | +49.97% | +66.92% |
| 18 | bayesian | +49.46% | +96.88% |
| 19 | llm_gemini_2_5_flash_lite_indirect | +34.65% | +26.90% |
| 20 | dqn | +34.02% | +53.02% |
| 21 | llm_gemini_2_5_flash_lite_indirect_all | +32.04% | +31.11% |
| 22 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +25.45% | +28.25% |
| 23 | llm_gemini_2_5_flash_lite_ipc | +21.71% | +17.01% |
| 24 | qlearning | +8.79% | +16.63% |
| 25 | llm_gemini_2_5_flash_full_metrics | +6.49% | +4.10% |
| 26 | llm_gemini_2_5_flash_lite_full_metrics | +2.94% | +0.29% |
| 27 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2_copy | +1.34% | +1.98% |
| 28 | llm_gemini_2_5_flash_lite_llm_dual_ipc copy | +1.02% | +0.82% |
| 29 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | +1.02% | +0.72% |
| 30 | fixed | +0.00% | +0.00% |
| 31 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3_copy | -0.01% | +0.00% |
| 32 | smac | -6.40% | -7.66% |

## dcperf_spark_tput

Metric: `queries_per_hour` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_ipc | +20.07% | +7.44% |
| 2 | llm_gemini_2_5_flash_full_metrics | +17.50% | +1.93% |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +16.73% | +2.03% |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +16.49% | +1.83% |
| 5 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +15.75% | +3.51% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +15.28% | +2.19% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +14.94% | +0.78% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +13.83% | -0.59% |
| 9 | llm_gemini_2_5_flash_indirect_all_mode3 | +13.82% | -0.37% |
| 10 | llm_gemini_2_5_flash_lite_full_metrics | +13.67% | -0.57% |
| 11 | llm_gemini_2_5_flash_lite_indirect_all | +13.65% | -1.41% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual | +13.16% | -1.89% |
| 13 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +12.73% | -0.68% |
| 14 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +6.13% | -3.91% |
| 15 | llm_gemini_2_5_flash_indirect_all_mode2 | +6.08% | -1.76% |
| 16 | fixed | +0.00% | +0.00% |
| 17 | llm_gemini_2_5_flash_lite | -5.13% | -18.33% |
| 18 | llm_gemini_2_5_flash | -5.19% | -15.00% |
| 19 | llm_gemini_2_5_flash_lite_indirect | -5.49% | -21.48% |
| 20 | dqn | -8.04% | -24.27% |
| 21 | qlearning | -11.12% | -2.66% |
| 22 | llm_gemini_2_5_flash_lite_ipc | -12.96% | -37.79% |
| 23 | bayesian | -25.22% | -31.77% |
| 24 | llm_gemini_2_5_flash_lite_mlos_trimming | -26.25% | -38.43% |
| 25 | smac | -32.74% | -37.99% |
| 26 | mlos | -33.71% | -50.89% |

## masstree_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +2320.18% | +2258.87% |
| 2 | llm_gemini_2_5_flash_ipc copy | +2247.66% | +2169.63% |
| 3 | llm_gemini_2_5_flash_ipc | +2247.66% | +2169.63% |
| 4 | llm_gemini_2_5_flash_indirect_all_mode2 | +2245.29% | +2229.17% |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +1790.05% | +2078.31% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +1255.95% | +2011.99% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual | +1001.68% | +2124.15% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +965.94% | +1593.14% |
| 9 | mlos | +964.54% | +2030.23% |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +940.98% | +2091.21% |
| 11 | llm_gemini_2_5_flash_lite | +882.85% | +2117.48% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +853.31% | +2016.02% |
| 13 | llm_gemini_2_5_flash_indirect_all_mode3 | +628.86% | +1982.27% |
| 14 | bayesian | +585.35% | +912.64% |
| 15 | dqn | +470.64% | +693.88% |
| 16 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +426.50% | +490.97% |
| 17 | llm_gemini_2_5_flash_lite_mlos_trimming | +422.33% | +465.53% |
| 18 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +309.49% | +342.35% |
| 19 | qlearning | +305.89% | +336.52% |
| 20 | llm_gemini_2_5_flash_lite_indirect | +284.80% | +269.64% |
| 21 | llm_gemini_2_5_flash_lite_ipc | +206.38% | +245.96% |
| 22 | llm_gemini_2_5_flash_lite_indirect_all | +204.05% | +258.83% |
| 23 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +198.62% | +265.68% |
| 24 | fixed | +0.00% | +0.00% |

## silo_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash | +1775.66% | +1797.30% |
| 2 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +1745.08% | +1795.70% |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +1733.47% | +1772.05% |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +1689.20% | +1768.94% |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +1423.63% | +1736.90% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual | +1382.73% | +1724.98% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +1380.86% | +1726.46% |
| 8 | llm_gemini_2_5_flash_indirect_all_mode2 | +1191.71% | +1660.72% |
| 9 | mlos | +519.57% | +1632.26% |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +497.63% | +1593.15% |
| 11 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +363.70% | +1663.48% |
| 12 | llm_gemini_2_5_flash_lite_mlos_trimming | +354.24% | +1368.80% |
| 13 | llm_gemini_2_5_flash_indirect_all_mode3 | +320.38% | +844.67% |
| 14 | bayesian | +209.04% | +699.93% |
| 15 | dqn | +180.12% | +331.24% |
| 16 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +177.16% | +912.24% |
| 17 | qlearning | +104.04% | +110.02% |
| 18 | llm_gemini_2_5_flash_lite_indirect | +55.09% | +31.31% |
| 19 | llm_gemini_2_5_flash_lite | +22.29% | +18.86% |
| 20 | llm_gemini_2_5_flash_lite_indirect_all | +21.84% | +8.91% |
| 21 | llm_gemini_2_5_flash_lite_ipc | +8.02% | +7.34% |
| 22 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +1.38% | +3.57% |
| 23 | fixed | +0.00% | +0.00% |

## sphinx_tput_max

Metric: `throughput` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2_copy | +8.31% | +12.50% |
| 2 | llm_gemini_2_5_flash_indirect_all_mode2 | +7.55% | +10.00% |
| 3 | mlos | +7.00% | +7.50% |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_ipc copy | +6.30% | +5.00% |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +5.94% | +7.50% |
| 6 | llm_gemini_2_5_flash | +4.80% | +6.25% |
| 7 | llm_gemini_2_5_flash_indirect_all_mode3 | +4.68% | +5.00% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +2.39% | +3.12% |
| 9 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +1.86% | +4.17% |
| 10 | llm_gemini_2_5_flash_full_metrics | +0.28% | +0.00% |
| 11 | fixed | +0.00% | +0.00% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3_copy | -0.04% | +0.00% |
| 13 | llm_gemini_2_5_flash_lite | -0.63% | +0.00% |
| 14 | llm_gemini_2_5_flash_ipc | -0.67% | +0.00% |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | -0.98% | +0.00% |
| 16 | llm_gemini_2_5_flash_lite_full_metrics | -1.31% | -2.50% |
| 17 | llm_gemini_2_5_flash_lite_indirect_all | -1.59% | +0.00% |
| 18 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -2.42% | -2.50% |
| 19 | llm_gemini_2_5_flash_lite_ipc | -2.74% | -2.50% |
| 20 | llm_gemini_2_5_flash_lite_llm_dual | -3.77% | -3.12% |
| 21 | llm_gemini_2_5_flash_lite_mlos_trimming | -5.63% | -3.12% |
| 22 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | -8.00% | -7.50% |
| 23 | bayesian | -11.69% | -9.38% |
| 24 | dqn | -17.28% | -17.50% |
| 25 | qlearning | -17.50% | -7.50% |
| 26 | llm_gemini_2_5_flash_lite_indirect | -18.06% | -17.50% |

## sysbench_cpu_tput

Metric: `throughput` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_ipc | +0.51% | +0.52% |
| 2 | llm_gemini_2_5_flash_indirect_all_mode3 | +0.51% | +0.52% |
| 3 | llm_gemini_2_5_flash_full_metrics | +0.51% | +0.51% |
| 4 | llm_gemini_2_5_flash | +0.50% | +0.52% |
| 5 | llm_gemini_2_5_flash_indirect_all_mode2 | +0.46% | +0.52% |
| 6 | llm_gemini_2_5_flash_lite_ipc | +0.41% | +0.48% |
| 7 | llm_gemini_2_5_flash_lite | +0.41% | +0.49% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +0.40% | +0.41% |
| 9 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +0.37% | +0.43% |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +0.31% | +0.34% |
| 11 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +0.26% | +0.33% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +0.22% | +0.35% |
| 13 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +0.14% | +0.23% |
| 14 | llm_gemini_2_5_flash_lite_indirect_all | +0.11% | +0.31% |
| 15 | fixed | +0.00% | +0.00% |
| 16 | llm_gemini_2_5_flash_lite_full_metrics | -0.01% | -0.02% |
| 17 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -0.02% | -0.02% |
| 18 | llm_gemini_2_5_flash_lite_indirect | -0.13% | -0.07% |
| 19 | llm_gemini_2_5_flash_lite_llm_dual | -0.23% | +0.37% |
| 20 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | -1.46% | -0.05% |
| 21 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | -2.01% | -3.00% |
| 22 | llm_gemini_2_5_flash_lite_llm_dual_ipc | -2.11% | +0.25% |
| 23 | mlos | -6.80% | +0.37% |
| 24 | bayesian | -10.73% | +0.01% |
| 25 | llm_gemini_2_5_flash_lite_mlos_trimming | -13.36% | +0.42% |
| 26 | dqn | -23.03% | -19.76% |
| 27 | qlearning | -27.55% | -2.81% |

## tpcc_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_indirect | +29.13% | +32.88% |
| 2 | llm_gemini_2_5_flash_lite_indirect_all | +27.74% | +31.44% |
| 3 | llm_gemini_2_5_flash_indirect_all_mode2 | +24.21% | +25.51% |
| 4 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +23.40% | +26.49% |
| 5 | llm_gemini_2_5_flash_full_metrics | +23.10% | +24.23% |
| 6 | llm_gemini_2_5_flash_indirect_all_mode3 | +20.40% | +22.22% |
| 7 | llm_gemini_2_5_flash | +17.94% | +25.76% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +17.51% | +20.21% |
| 9 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +17.41% | +19.68% |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +17.25% | +20.44% |
| 11 | llm_gemini_2_5_flash_lite_ipc | +15.54% | +13.38% |
| 12 | llm_gemini_2_5_flash_ipc | +14.51% | +14.54% |
| 13 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +14.38% | +17.25% |
| 14 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +13.92% | +15.30% |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +13.50% | +16.58% |
| 16 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +9.83% | +18.37% |
| 17 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | +8.92% | +7.08% |
| 18 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +7.65% | +15.25% |
| 19 | llm_gemini_2_5_flash_lite_full_metrics | +6.09% | +5.00% |
| 20 | mlos | +5.87% | +20.98% |
| 21 | llm_gemini_2_5_flash_lite_llm_dual | +3.71% | +13.10% |
| 22 | fixed | +0.00% | +0.00% |
| 23 | bayesian | -10.71% | +16.25% |
| 24 | llm_gemini_2_5_flash_lite_mlos_trimming | -14.33% | -3.85% |
| 25 | dqn | -38.11% | -25.21% |
| 26 | qlearning | -62.31% | -68.64% |

## Notes

- Workload 'sysbench_cpu_tput': fixed has conflicting goals for metric 'throughput' ({'minimize': 5, 'maximize': 5}); using inferred goal 'maximize'.
- Skipping workload 'xapian_hi_p99': no fixed baseline found.
