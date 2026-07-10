# Global Tuner Ranking vs Fixed

Sorted by Geomean Mean Improvement.

Cost model: Gemini API pricing, `<200k` context tier.
Rates: `gemini-2.5-flash-lite` in/out = `$0.10/$0.40` per 1M, `gemini-2.5-flash` = `$0.30/$2.50`, `gemini-2.5-pro` = `$1.25/$10.00`.

| Rank # | Tuner name | Geomean Mean Improvement | Geomean Median Improvement | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_ipc | +82.43% | +83.47% | $2.3286 | $0.0016 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_indirect_all_mode2 | +82.21% | +88.94% | $3.0675 | $0.0021 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +76.33% | +78.19% | $0.2106 | $0.0001 | $0.1648 | $0.0458 |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +64.79% | +83.76% | $0.2206 | $0.0001 | $0.1730 | $0.0476 |
| 5 | llm_gemini_2_5_flash_lite_llm_dual | +58.51% | +83.71% | $0.4668 | $0.0003 | $0.3234 | $0.1434 |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +57.17% | +79.99% | $0.2334 | $0.0001 | $0.1836 | $0.0498 |
| 7 | llm_gemini_2_5_flash_indirect_all_mode3 | +55.85% | +60.80% | $3.1018 | $0.0022 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +40.88% | +43.59% | $0.2250 | $0.0001 | $0.1762 | $0.0489 |
| 9 | mlos | +39.35% | +54.98% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_lite | +37.12% | +66.40% | $0.1980 | $0.0002 | $0.0000 | $0.0000 |
| 11 | llm_gemini_2_5_flash_lite_indirect | +30.83% | +34.32% | $0.7244 | $0.0004 | $0.0000 | $0.0000 |
| 12 | llm_gemini_2_5_flash_lite_indirect_all | +30.45% | +36.30% | $0.9209 | $0.0005 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +29.08% | +33.20% | $0.9697 | $0.0006 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_ipc | +24.08% | +24.95% | $0.7646 | $0.0004 | $0.0000 | $0.0000 |
| 15 | bayesian | +17.45% | +35.86% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_full_metrics | +12.78% | +11.52% | $2.5334 | $0.0020 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_mlos_trimming | +10.89% | +22.25% | $0.0207 | $0.0000 | $0.0000 | $0.0000 |
| 18 | dqn | +10.21% | +20.76% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 19 | llm_gemini_2_5_flash_lite_full_metrics | +4.70% | +1.02% | $0.6114 | $0.0006 | $0.0000 | $0.0000 |
| 20 | llm_gemini_2_5_flash | +3.72% | +4.03% | $0.1795 | $0.0002 | $0.0000 | $0.0000 |
| 21 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | +0.23% | -0.04% | $0.6931 | $0.0006 | $0.0000 | $0.0000 |
| 22 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 23 | smac | -4.07% | -6.27% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 24 | qlearning | -7.63% | +11.01% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## dcperf_spark_tput

Metric: `queries_per_hour` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +18.54% | +6.77% | $0.2334 | $0.0016 | $0.1836 | $0.0498 |
| 2 | llm_gemini_2_5_flash_ipc | +18.07% | +7.27% | $0.1807 | $0.0012 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +17.57% | +7.15% | $0.2106 | $0.0014 | $0.1648 | $0.0458 |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +16.12% | +5.70% | $0.2206 | $0.0015 | $0.1730 | $0.0476 |
| 5 | llm_gemini_2_5_flash_full_metrics | +15.99% | +5.38% | $0.1940 | $0.0013 | $0.0000 | $0.0000 |
| 6 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +15.91% | +5.24% | $0.2250 | $0.0015 | $0.1762 | $0.0489 |
| 7 | llm_gemini_2_5_flash_lite_full_metrics | +14.41% | +3.59% | $0.0569 | $0.0004 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_indirect_all_mode2 | +13.80% | +4.90% | $0.1766 | $0.0012 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_indirect_all_mode3 | +13.75% | +5.14% | $0.1789 | $0.0012 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +13.32% | +3.32% | $0.0528 | $0.0004 | $0.0000 | $0.0000 |
| 11 | llm_gemini_2_5_flash_lite_llm_dual | +1.92% | +4.14% | $0.2134 | $0.0014 | $0.1670 | $0.0463 |
| 12 | llm_gemini_2_5_flash | +1.79% | +0.71% | $0.1795 | $0.0012 | $0.0000 | $0.0000 |
| 13 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_indirect_all | -1.75% | -1.51% | $0.0539 | $0.0004 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_ipc | -2.19% | -1.30% | $0.0514 | $0.0003 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_lite | -2.65% | -2.43% | $0.0511 | $0.0003 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_indirect | -3.00% | -1.89% | $0.0544 | $0.0004 | $0.0000 | $0.0000 |
| 18 | bayesian | -16.81% | -23.65% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 19 | mlos | -17.29% | -28.96% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 20 | llm_gemini_2_5_flash_lite_mlos_trimming | -17.81% | -28.14% | $0.0075 | $0.0001 | $0.0000 | $0.0000 |
| 21 | dqn | -18.08% | -25.18% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 22 | qlearning | -18.32% | -32.43% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 23 | smac | -22.09% | -32.18% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## masstree_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +2132.93% | +2335.81% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_ipc | +1961.46% | +2293.21% | $0.4910 | $0.0016 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash_indirect_all_mode2 | +1668.93% | +2235.54% | $0.8506 | $0.0028 | $0.0000 | $0.0000 |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +1048.93% | +2095.56% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 5 | llm_gemini_2_5_flash_lite_llm_dual | +1008.46% | +2235.54% | $0.2534 | $0.0008 | $0.1564 | $0.0970 |
| 6 | mlos | +788.70% | +1492.20% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +749.50% | +1779.76% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_indirect_all_mode3 | +616.45% | +810.98% | $0.8951 | $0.0030 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_lite | +588.11% | +2133.51% | $0.1469 | $0.0005 | $0.0000 | $0.0000 |
| 10 | bayesian | +542.64% | +758.38% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 11 | dqn | +493.00% | +782.47% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 12 | qlearning | +356.27% | +384.47% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_mlos_trimming | +321.23% | +366.78% | $0.0069 | $0.0000 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +311.15% | +363.25% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_indirect | +266.43% | +296.66% | $0.1631 | $0.0005 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_lite_ipc | +213.54% | +260.93% | $0.1487 | $0.0002 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_indirect_all | +206.11% | +277.60% | $0.2789 | $0.0005 | $0.0000 | $0.0000 |
| 18 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +199.16% | +278.03% | $0.2710 | $0.0009 | $0.0000 | $0.0000 |
| 19 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## sphinx_tput_max

Metric: `throughput` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_indirect_all_mode2 | +3.97% | +3.03% | $0.8916 | $0.0030 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_indirect_all_mode3 | +3.71% | +1.82% | $0.8981 | $0.0030 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash | +3.63% | +1.82% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 4 | mlos | +3.24% | +3.03% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 5 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +0.11% | +1.82% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 6 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 7 | llm_gemini_2_5_flash_full_metrics | -0.73% | -3.03% | $0.8917 | $0.0030 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_lite | -1.12% | -3.03% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_lite_indirect_all | -1.67% | -3.03% | $0.2274 | $0.0008 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_ipc | -1.81% | -3.03% | $0.4849 | $0.0016 | $0.0000 | $0.0000 |
| 11 | llm_gemini_2_5_flash_lite_full_metrics | -1.89% | -3.03% | $0.2630 | $0.0009 | $0.0000 | $0.0000 |
| 12 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -2.08% | -3.03% | $0.2674 | $0.0009 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_ipc | -2.10% | -3.03% | $0.1439 | $0.0005 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | -3.81% | -0.61% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | -4.83% | -0.61% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_lite_mlos_trimming | -6.23% | -5.45% | $0.0010 | $0.0000 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | -7.29% | -3.03% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 18 | llm_gemini_2_5_flash_lite_llm_dual | -7.74% | -6.06% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 19 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | -8.30% | -9.09% | $0.2533 | $0.0008 | $0.0000 | $0.0000 |
| 20 | llm_gemini_2_5_flash_lite_indirect | -14.93% | -15.15% | $0.1472 | $0.0005 | $0.0000 | $0.0000 |
| 21 | bayesian | -15.33% | -12.73% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 22 | qlearning | -15.53% | -8.48% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 23 | dqn | -19.16% | -21.21% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## sysbench_cpu_tput

Metric: `throughput` | Goal: `maximize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_ipc | +0.50% | +0.51% | $0.4888 | $0.0016 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_indirect_all_mode3 | +0.50% | +0.52% | $0.5143 | $0.0017 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash | +0.49% | +0.52% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 4 | llm_gemini_2_5_flash_full_metrics | +0.49% | +0.50% | $0.4948 | $0.0016 | $0.0000 | $0.0000 |
| 5 | llm_gemini_2_5_flash_indirect_all_mode2 | +0.46% | +0.51% | $0.5071 | $0.0017 | $0.0000 | $0.0000 |
| 6 | llm_gemini_2_5_flash_lite_ipc | +0.36% | +0.48% | $0.1390 | $0.0005 | $0.0000 | $0.0000 |
| 7 | llm_gemini_2_5_flash_lite | +0.34% | +0.47% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +0.31% | +0.42% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_lite_indirect_all | +0.14% | +0.25% | $0.1358 | $0.0005 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +0.12% | +0.30% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 11 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 12 | llm_gemini_2_5_flash_lite_full_metrics | -0.02% | -0.02% | $0.1477 | $0.0005 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -0.03% | -0.01% | $0.1428 | $0.0005 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_indirect | -0.12% | -0.05% | $0.1324 | $0.0004 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | -0.15% | +0.15% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_lite_llm_dual | -0.26% | +0.37% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | -1.65% | -2.11% | $0.1358 | $0.0005 | $0.0000 | $0.0000 |
| 18 | llm_gemini_2_5_flash_lite_llm_dual_ipc | -2.30% | +0.24% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 19 | mlos | -5.43% | +0.39% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 20 | llm_gemini_2_5_flash_lite_mlos_trimming | -13.81% | +0.02% | $0.0007 | $0.0000 | $0.0000 | $0.0000 |
| 21 | bayesian | -19.42% | -0.71% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 22 | dqn | -24.14% | -20.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 23 | qlearning | -24.91% | -0.36% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## sysbench_oltp_rw_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_llm_dual | +47.65% | +48.55% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +46.92% | +51.28% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash_full_metrics | +45.55% | +51.28% | $0.5363 | $0.0018 | $0.0000 | $0.0000 |
| 4 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +44.64% | +48.55% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 5 | llm_gemini_2_5_flash_indirect_all_mode3 | +41.58% | +45.93% | $0.1068 | $0.0016 | $0.0000 | $0.0000 |
| 6 | llm_gemini_2_5_flash_indirect_all_mode2 | +41.36% | +43.30% | $0.1470 | $0.0015 | $0.0000 | $0.0000 |
| 7 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +40.91% | +43.30% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_ipc | +36.04% | +33.37% | $0.1947 | $0.0016 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_lite_indirect | +34.96% | +40.77% | $0.1188 | $0.0004 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_lite_indirect_all | +34.36% | +40.77% | $0.1124 | $0.0004 | $0.0000 | $0.0000 |
| 11 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +27.92% | +31.03% | $0.1143 | $0.0005 | $0.0000 | $0.0000 |
| 12 | llm_gemini_2_5_flash_lite_full_metrics | +13.54% | +1.81% | $0.1143 | $0.0004 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_ipc | +5.83% | -1.79% | $0.1478 | $0.0005 | $0.0000 | $0.0000 |
| 14 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | -2.45% | -1.79% | $0.1517 | $0.0005 | $0.0000 | $0.0000 |
| 16 | bayesian | -3.18% | +20.82% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 17 | llm_gemini_2_5_flash_lite_mlos_trimming | -16.41% | +9.43% | $0.0025 | $0.0000 | $0.0000 | $0.0000 |
| 18 | qlearning | -37.03% | -6.97% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## tpcc_hi_p99

Metric: `latency_p99` | Goal: `minimize`

| Rank # | Tuner name | Mean Improvement vs Fixed Mean | Median Improvement vs Fixed Median | Total LLM Cost (USD) | Avg Cost / Iteration (USD) | Actor Cost (USD) | Speculator Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | llm_gemini_2_5_flash_lite_indirect_all | +23.88% | +25.98% | $0.1126 | $0.0004 | $0.0000 | $0.0000 |
| 2 | llm_gemini_2_5_flash_indirect_all_mode2 | +23.10% | +25.14% | $0.4945 | $0.0016 | $0.0000 | $0.0000 |
| 3 | llm_gemini_2_5_flash_lite_indirect | +23.04% | +26.41% | $0.1085 | $0.0004 | $0.0000 | $0.0000 |
| 4 | llm_gemini_2_5_flash_full_metrics | +22.22% | +23.84% | $0.4165 | $0.0017 | $0.0000 | $0.0000 |
| 5 | llm_gemini_2_5_flash_indirect_all_mode3 | +19.15% | +20.82% | $0.5086 | $0.0017 | $0.0000 | $0.0000 |
| 6 | llm_gemini_2_5_flash_lite_indirect_all_mode2 | +18.26% | +22.65% | $0.1425 | $0.0005 | $0.0000 | $0.0000 |
| 7 | llm_gemini_2_5_flash | +17.46% | +22.96% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 8 | llm_gemini_2_5_flash_lite_llm_dual_ipc | +17.05% | +20.19% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 9 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 | +15.94% | +19.24% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 10 | llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2 | +15.08% | +19.23% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 11 | llm_gemini_2_5_flash_lite_ipc | +14.42% | +11.62% | $0.1338 | $0.0004 | $0.0000 | $0.0000 |
| 12 | llm_gemini_2_5_flash_ipc | +12.82% | +14.31% | $0.4886 | $0.0016 | $0.0000 | $0.0000 |
| 13 | llm_gemini_2_5_flash_lite_llm_dual_full_metrics | +8.42% | +14.24% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 14 | llm_gemini_2_5_flash_lite_full_metrics_mode3 | +6.21% | +4.75% | $0.1311 | $0.0004 | $0.0000 | $0.0000 |
| 15 | llm_gemini_2_5_flash_lite_full_metrics | +3.40% | +3.97% | $0.0295 | $0.0005 | $0.0000 | $0.0000 |
| 16 | llm_gemini_2_5_flash_lite_llm_dual | +3.36% | +12.84% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 17 | mlos | +2.01% | +18.43% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 18 | fixed | +0.00% | +0.00% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 19 | llm_gemini_2_5_flash_lite_mlos_trimming | -20.52% | -3.85% | $0.0020 | $0.0000 | $0.0000 | $0.0000 |
| 20 | bayesian | -25.66% | -8.34% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 21 | dqn | -39.86% | -25.48% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |
| 22 | qlearning | -58.28% | -32.59% | $0.0000 | $0.0000 | $0.0000 | $0.0000 |

## Notes

- Workload 'sysbench_cpu_tput': fixed has conflicting goals for metric 'throughput' ({'minimize': 5, 'maximize': 5}); using inferred goal 'maximize'.
- No token usage found for 28 LLM workload/tuner pair(s) (logs missing and no history token_metrics). Reported cost for those rows is $0.00: masstree_hi_p99/llm_gemini_2_5_flash_lite_llm_dual_full_metrics, masstree_hi_p99/llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2, masstree_hi_p99/llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3, masstree_hi_p99/llm_gemini_2_5_flash_lite_llm_dual_ipc, sphinx_tput_max/llm_gemini_2_5_flash, sphinx_tput_max/llm_gemini_2_5_flash_lite, sphinx_tput_max/llm_gemini_2_5_flash_lite_llm_dual, sphinx_tput_max/llm_gemini_2_5_flash_lite_llm_dual_full_metrics, sphinx_tput_max/llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode2, sphinx_tput_max/llm_gemini_2_5_flash_lite_llm_dual_indirect_all_mode3 ...
