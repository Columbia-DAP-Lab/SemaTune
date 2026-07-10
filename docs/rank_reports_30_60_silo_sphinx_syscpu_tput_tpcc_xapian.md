# Global Tuner Ranking vs Fixed

Sorted by Geomean Mean Improvement.

Benchmarks included:
- `masstree_hi_p99`
- `sphinx_tput_max`
- `sysbench_cpu_tput`
- `tpcc_hi_p99`

| Rank # | Tuner name | Geomean Mean Improvement | Geomean Median Improvement |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_indirect_all_mode2 | +136.86% | +138.44% |
| 2 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +133.24% | +133.74% |
| 3 | llm_gemini_2_5_flash_ipc | +127.61% | +126.10% |
| 4 | llm_gemini_2_5_flash_ipc copy | +120.12% | +118.27% |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +116.51% | +126.45% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3 | +98.64% | +123.31% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2 | +85.08% | +111.77% |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2 | +84.72% | +124.79% |
| 9 | mlos | +83.10% | +129.64% |
| 10 | llm_gemini_2_5_flash_lite_llm_dual | +81.99% | +122.39% |
| 11 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +79.97% | +124.74% |
| 12 | llm_gemini_2_5_flash_lite | +76.96% | +117.27% |
| 13 | llm_gemini_2_5_flash_indirect_all_mode3 | +74.32% | +127.66% |
| 14 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 | +56.61% | +61.70% |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +49.05% | +52.98% |
| 16 | bayesian | +48.20% | +80.73% |
| 17 | llm_gemini_2_5_flash_lite_indirect | +42.00% | +41.86% |
| 18 | llm_gemini_2_5_flash_lite_indirect_all | +39.86% | +47.48% |
| 19 | llm_gemini_2_5_flash_lite_mlos_trimming | +38.31% | +51.66% |
| 20 | llm_gemini_2_5_flash_lite_ipc | +36.36% | +40.01% |
| 21 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +35.00% | +42.73% |
| 22 | dqn | +22.46% | +40.80% |
| 23 | llm_gemini_2_5_flash | +5.57% | +7.65% |
| 24 | llm_gemini_2_5_flash_full_metrics | +5.54% | +5.71% |
| 25 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | +1.53% | +1.08% |
| 26 | llm_gemini_2_5_flash_lite_full_metrics | +1.15% | +0.58% |
| 27 | fixed | +0.00% | +0.00% |
| 28 | qlearning | -2.21% | +5.33% |

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

## sphinx_tput_max

Metric: `throughput` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median |
| --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_indirect_all_mode2 | +7.55% | +10.00% |
| 2 | mlos | +7.00% | +7.50% |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +6.30% | +5.00% |
| 4 | llm_gemini_2_5_flash | +4.80% | +6.25% |
| 5 | llm_gemini_2_5_flash_indirect_all_mode3 | +4.68% | +5.00% |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +2.39% | +3.12% |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +1.86% | +4.17% |
| 8 | llm_gemini_2_5_flash_full_metrics | +0.28% | +0.00% |
| 9 | fixed | +0.00% | +0.00% |
| 10 | llm_gemini_2_5_flash_lite | -0.63% | +0.00% |
| 11 | llm_gemini_2_5_flash_ipc | -0.67% | +0.00% |
| 12 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | -0.98% | +0.00% |
| 13 | llm_gemini_2_5_flash_lite_full_metrics | -1.31% | -2.50% |
| 14 | llm_gemini_2_5_flash_lite_indirect_all | -1.59% | +0.00% |
| 15 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -2.42% | -2.50% |
| 16 | llm_gemini_2_5_flash_lite_ipc | -2.74% | -2.50% |
| 17 | llm_gemini_2_5_flash_lite_llm_dual | -3.77% | -3.12% |
| 18 | llm_gemini_2_5_flash_lite_mlos_trimming | -5.63% | -3.12% |
| 19 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | -8.00% | -7.50% |
| 20 | bayesian | -11.69% | -9.38% |
| 21 | dqn | -17.28% | -17.50% |
| 22 | qlearning | -17.50% | -7.50% |
| 23 | llm_gemini_2_5_flash_lite_indirect | -18.06% | -17.50% |

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
