# Codex Handoff: Results + Plotting Playbook

## Scope
This document captures what was produced/validated in this workspace for:
- `dcperf_spark_tput`
- `tpcc_hi_p99`
- `silo_hi_p99`
- `sysbench_cpu_tput` (pre-convergence only)

It is intended for a future Codex agent to continue analysis without rediscovery.

## Canonical Tuners and Labels
- `fixed` -> default baseline
- `mlos` -> MLOS tuner
- `llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3` -> Tuxbot

## Core Scripts Used
- `/mydata/os-param-tuning/scripts/compare_param_count_tuners.py`
- `/mydata/os-param-tuning/scripts/plot_cycle_phase_comparison.py`
- `/mydata/os-param-tuning/scripts/plot_preconvergence_stability.py`
- `/mydata/os-param-tuning/scripts/generate_8_param_cycle_configs.py`
- `/mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh`

## Data Directory Map

### Full-param baselines
- `/mydata/os-param-tuning/all_results/results_config_full_param_dcperf_spark_tput/dcperf_spark_tput/{fixed,mlos,llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3}`
- `/mydata/os-param-tuning/all_results/results_config_full_param_tpcc_hi_p99/tpcc_hi_p99/{fixed,mlos,llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3}`
- `/mydata/os-param-tuning/all_results/results_config_full_param_silo_hi_p99/silo_hi_p99/{fixed,mlos,llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3}`
- `/mydata/os-param-tuning/all_results/results_config_full_param_sysbench_cpu_tput/sysbench_cpu_tput/{fixed,mlos,llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3}`

### Cycle-schedule runs (8-param)
- `/mydata/os-param-tuning/results/dcperf_spark_tput/*_tune{2,5,10,20,30}_stable10`
- `/mydata/os-param-tuning/results/tpcc_hi_p99/*_tune{2,5,10,20,30,40,50}_stable10`
- `/mydata/os-param-tuning/results/silo_hi_p99/*_tune{2,5,10,20,30,40,50}_stable10`
- imported `0-60` group comes from full-param dirs above.

### Param-count result roots currently available in this checkout
- Spark: `results_config_{1,2,4,16,32,41}_param_dcperf_spark_tput_*` plus full-param import for `8_param`
- TPCC: local checkout has `results_config_8_param_tpcc_hi_p99_20260227_124551` and `results_config_full_param_tpcc_hi_p99`
- Silo: local checkout has `results_config_8_param_silo_hi_p99__20260227_104440` and `results_config_full_param_silo_hi_p99`

### Other benchmarks available as results (full-param roots)
Quick listing command:
```bash
ls -d /mydata/os-param-tuning/all_results/results_config_full_param*
```

Detected full-param roots and workload subdirs:
- `results_config_full_param_dcperf_spark_tput -> dcperf_spark_tput`
- `results_config_full_param_masstree_hi_p99 -> masstree_hi_p99`
- `results_config_full_param_masstree_hi_p99_20260224_225040 -> masstree_hi_p99`
- `results_config_full_param_masstree_lo_pwr_wrt_p99 -> masstree_lo_pwr_wrt_p99`
- `results_config_full_param_sibench_hi_p99 -> sibench_hi_p99`
- `results_config_full_param_sibench_lo_pwr_wrt_p99 -> sibench_lo_pwr_wrt_p99`
- `results_config_full_param_silo_hi_p99 -> silo_hi_p99`
- `results_config_full_param_silo_lo_pwr_wrt_p99 -> silo_lo_pwr_wrt_p99`
- `results_config_full_param_sphinx_lo_pwr_wrt_p99 -> sphinx_lo_pwr_wrt_p99`
- `results_config_full_param_sphinx_tput_max -> sphinx_tput_max`
- `results_config_full_param_sysbench_cpu_p99 -> sysbench_cpu_p99`
- `results_config_full_param_sysbench_cpu_p99_wrt_power -> sysbench_cpu_p99`
- `results_config_full_param_sysbench_cpu_tput -> sysbench_cpu_tput`
- `results_config_full_param_sysbench_oltp_rw_hi_p99 -> sysbench_oltp_rw_hi_p99`
- `results_config_full_param_sysbench_oltp_rw_lo_pwr_wrt_p99 -> sysbench_oltp_rw_lo_pwr_wrt_p99`
- `results_config_full_param_tpcc_hi_p99 -> tpcc_hi_p99`
- `results_config_full_param_tpcc_lo_pwr_wrt_p99 -> tpcc_lo_pwr_wrt_p99`
- `results_config_full_param_xapian_hi_p99 -> xapian_hi_p99`
- `results_config_full_param_xapian_lo_pwr_wrt_p99 -> xapian_lo_pwr_wrt_p99`
- `results_config_full_param_ycsb_hi_p99 -> ycsb_hi_p99`
- `results_config_full_param_ycsb_lo_pwr_wrt_p99 -> ycsb_lo_pwr_wrt_p99`

Unique workload names available:
- `dcperf_spark_tput`
- `masstree_hi_p99`
- `masstree_lo_pwr_wrt_p99`
- `sibench_hi_p99`
- `sibench_lo_pwr_wrt_p99`
- `silo_hi_p99`
- `silo_lo_pwr_wrt_p99`
- `sphinx_lo_pwr_wrt_p99`
- `sphinx_tput_max`
- `sysbench_cpu_p99`
- `sysbench_cpu_tput`
- `sysbench_oltp_rw_hi_p99`
- `sysbench_oltp_rw_lo_pwr_wrt_p99`
- `tpcc_hi_p99`
- `tpcc_lo_pwr_wrt_p99`
- `xapian_hi_p99`
- `xapian_lo_pwr_wrt_p99`
- `ycsb_hi_p99`
- `ycsb_lo_pwr_wrt_p99`

### Historical param-count dirs used earlier for TPCC/Silo (may not exist now)
- TPCC historical list:
  `/mydata/os-param-tuning/all_results/results_config_1_param_tpcc_hi_p99_20260227_104406`
  `/mydata/os-param-tuning/all_results/results_config_2_param_tpcc_hi_p99_20260226_210354`
  `/mydata/os-param-tuning/all_results/results_config_4_param_tpcc_hi_p99_20260226_225957`
  `/mydata/os-param-tuning/all_results/results_config_16_param_tpcc_hi_p99_20260227_010035`
  `/mydata/os-param-tuning/all_results/results_config_32_param_tpcc_hi_p99_20260227_035000`
  `/mydata/os-param-tuning/all_results/results_config_41_param_tpcc_hi_p99_20260227_064531`
- Silo historical list:
  `/mydata/os-param-tuning/all_results/results_config_1_param_silo_hi_p99_20260226_184849`
  `/mydata/os-param-tuning/all_results/results_config_2_param_silo_hi_p99_20260226_201003`
  `/mydata/os-param-tuning/all_results/results_config_4_param_silo_hi_p99_20260226_213103`
  `/mydata/os-param-tuning/all_results/results_config_16_param_silo_hi_p99_20260226_225749`
  `/mydata/os-param-tuning/all_results/results_config_32_param_silo_hi_p99_20260227_011518`
  `/mydata/os-param-tuning/all_results/results_config_41_param_silo_hi_p99_20260227_033221`

## Existing Plot/Table Artifacts

### Param-count comparison outputs
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_param_mlos_vs_tuxbot_0_10__0_30__30_50.{md,csv,png}`
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_param_mlos_vs_tuxbot_0_20__0_60__60_80.{md,csv,png}`
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_param_mlos_vs_tuxbot_0_30__30_50.{md,csv,png}`
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_param_mlos_vs_tuxbot_0_60__60_80.{md,csv,png}`
- `/mydata/os-param-tuning/plots/tpcc_hi_p99_param_mlos_vs_tuxbot_0_20__0_60__60_80.{md,csv,png}`
- `/mydata/os-param-tuning/plots/tpcc_hi_p99_param_mlos_vs_tuxbot_0_60__60_80.{md,csv,png}`
- `/mydata/os-param-tuning/plots/silo_hi_p99_param_mlos_vs_tuxbot_0_20__0_60__60_80.{md,csv,png}`
- `/mydata/os-param-tuning/plots/silo_hi_p99_param_mlos_vs_tuxbot_0_60__60_80.{md,csv,png}`

### Phase4 (tuning vs stable, 4 bars per setup + fixed row)
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_phase4_mlos_tuxbot_tuning_vs_stable_stable10.{md,csv,png}`
- `/mydata/os-param-tuning/plots/tpcc_hi_p99_phase4_mlos_tuxbot_tuning_vs_stable_stable10.{md,csv,png}`
- `/mydata/os-param-tuning/plots/silo_hi_p99_phase4_mlos_tuxbot_tuning_vs_stable_stable10.{md,csv,png}`

### Pre-convergence stability outputs
- `/mydata/os-param-tuning/plots/dcperf_spark_tput_preconvergence_stability_0_60.{md,csv,png}`
- `/mydata/os-param-tuning/plots/tpcc_hi_p99_preconvergence_stability_0_60.{md,csv,png}`
- `/mydata/os-param-tuning/plots/silo_hi_p99_preconvergence_stability_0_60.{md,csv,png}`
- `/mydata/os-param-tuning/plots/sysbench_cpu_tput_preconvergence_stability_0_60.{md,csv,png}`

## Latest Numbers to Cite Quickly

### Pre-convergence (0-60, converged-only)
- Spark:
  Fixed `603.716`, MLOS `534.158` (`-11.52%` vs fixed), Tuxbot `659.251` (`+9.20%`).
  Mean bad-window rate: Fixed `36.00%`, MLOS `54.00%`, Tuxbot `16.80%`.
- TPCC:
  Fixed `78.106`, MLOS `76.568` (`+1.97%`), Tuxbot `69.451` (`+11.08%`).
  Mean bad-window rate: Fixed `54.33%`, MLOS `32.00%`, Tuxbot `10.33%`.
- Silo:
  Fixed `24.161`, MLOS `5.237` (`+78.32%`), Tuxbot `4.143` (`+82.85%`).
  Mean bad-window rate: Fixed `38.33%`, MLOS `2.00%`, Tuxbot `2.22%`.
- Sysbench throughput:
  Fixed `941.814`, MLOS `890.725` (`-5.42%`), Tuxbot `943.825` (`+0.21%`).
  Mean bad-window rate: Fixed `48.67%`, MLOS `33.67%`, Tuxbot `9.33%`.

### Phase4 stable-state (last 10 windows) at 0-60 setup
- Spark:
  MLOS stable `510.463` (`-23.68%` vs fixed), Tuxbot stable `655.311` (`-2.02%`).
- TPCC:
  MLOS stable `70.163` (`+10.87%`), Tuxbot stable `67.444` (`+14.33%`).
- Silo:
  MLOS stable `1.859` (`+92.08%`), Tuxbot stable `3.483` (`+85.17%`).

## Command Recipes

### 1) Param-count comparison (`compare_param_count_tuners.py`)

#### Spark (validated in this workspace)
```bash
python3 /mydata/os-param-tuning/scripts/compare_param_count_tuners.py \
  dcperf_spark_tput \
  --root /mydata/os-param-tuning \
  --windows 0-10 0-30 30-50 \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_dcperf_spark_tput \
  --full-import-buckets default,mlos,tuxbot \
  --full-fixed-tuner fixed \
  --full-mlos-tuner mlos \
  --full-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --output-dir /mydata/os-param-tuning/plots
```

#### TPCC (if historical 1/2/4/16/32/41 dirs are present)
```bash
python3 /mydata/os-param-tuning/scripts/compare_param_count_tuners.py \
  tpcc_hi_p99 \
  --results-dirs \
    /mydata/os-param-tuning/all_results/results_config_1_param_tpcc_hi_p99_20260227_104406 \
    /mydata/os-param-tuning/all_results/results_config_2_param_tpcc_hi_p99_20260226_210354 \
    /mydata/os-param-tuning/all_results/results_config_4_param_tpcc_hi_p99_20260226_225957 \
    /mydata/os-param-tuning/all_results/results_config_16_param_tpcc_hi_p99_20260227_010035 \
    /mydata/os-param-tuning/all_results/results_config_32_param_tpcc_hi_p99_20260227_035000 \
    /mydata/os-param-tuning/all_results/results_config_41_param_tpcc_hi_p99_20260227_064531 \
  --windows 0-20 0-60 60-80 \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_tpcc_hi_p99 \
  --full-import-buckets default,mlos,tuxbot \
  --full-fixed-tuner fixed \
  --full-mlos-tuner mlos \
  --full-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --output-dir /mydata/os-param-tuning/plots
```

#### Silo (if historical 1/2/4/16/32/41 dirs are present)
```bash
python3 /mydata/os-param-tuning/scripts/compare_param_count_tuners.py \
  silo_hi_p99 \
  --results-dirs \
    /mydata/os-param-tuning/all_results/results_config_1_param_silo_hi_p99_20260226_184849 \
    /mydata/os-param-tuning/all_results/results_config_2_param_silo_hi_p99_20260226_201003 \
    /mydata/os-param-tuning/all_results/results_config_4_param_silo_hi_p99_20260226_213103 \
    /mydata/os-param-tuning/all_results/results_config_16_param_silo_hi_p99_20260226_225749 \
    /mydata/os-param-tuning/all_results/results_config_32_param_silo_hi_p99_20260227_011518 \
    /mydata/os-param-tuning/all_results/results_config_41_param_silo_hi_p99_20260227_033221 \
  --windows 0-20 0-60 60-80 \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_silo_hi_p99 \
  --full-import-buckets default,mlos,tuxbot \
  --full-fixed-tuner fixed \
  --full-mlos-tuner mlos \
  --full-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --output-dir /mydata/os-param-tuning/plots
```

### 2) Phase4 cycle comparison (`plot_cycle_phase_comparison.py`)

#### Spark (validated)
```bash
python3 /mydata/os-param-tuning/scripts/plot_cycle_phase_comparison.py \
  dcperf_spark_tput \
  --results-root /mydata/os-param-tuning/results/dcperf_spark_tput \
  --tuning-setups 2 5 10 20 30 \
  --stable-windows 10 \
  --fixed-history-dir /mydata/os-param-tuning/results/dcperf_spark_tput/fixed_tune10 \
  --full-group-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_dcperf_spark_tput \
  --full-group-mlos-tuner mlos \
  --full-group-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --full-group-start 0 \
  --full-group-end 60 \
  --full-group-tuning 60 \
  --bar-stat mean \
  --converged-only \
  --include-fixed-row \
  --output-dir /mydata/os-param-tuning/plots
```

#### TPCC (validated)
```bash
python3 /mydata/os-param-tuning/scripts/plot_cycle_phase_comparison.py \
  tpcc_hi_p99 \
  --results-root /mydata/os-param-tuning/results/tpcc_hi_p99 \
  --tuning-setups 2 5 10 20 30 40 50 \
  --stable-windows 10 \
  --fixed-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_tpcc_hi_p99 \
  --fixed-tuner fixed \
  --full-group-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_tpcc_hi_p99 \
  --full-group-mlos-tuner mlos \
  --full-group-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --full-group-start 0 \
  --full-group-end 60 \
  --full-group-tuning 60 \
  --bar-stat mean \
  --converged-only \
  --include-fixed-row \
  --output-dir /mydata/os-param-tuning/plots
```

#### Silo (validated)
```bash
python3 /mydata/os-param-tuning/scripts/plot_cycle_phase_comparison.py \
  silo_hi_p99 \
  --results-root /mydata/os-param-tuning/results/silo_hi_p99 \
  --tuning-setups 2 5 10 20 30 40 50 \
  --stable-windows 10 \
  --fixed-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_silo_hi_p99 \
  --fixed-tuner fixed \
  --full-group-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_silo_hi_p99 \
  --full-group-mlos-tuner mlos \
  --full-group-tuxbot-tuner llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3 \
  --full-group-start 0 \
  --full-group-end 60 \
  --full-group-tuning 60 \
  --bar-stat mean \
  --converged-only \
  --include-fixed-row \
  --output-dir /mydata/os-param-tuning/plots
```

### 3) Pre-convergence stability (`plot_preconvergence_stability.py`)

This now produces 5 panels:
- mean bad-window rate
- P10 bad-window rate
- P90 bad-window rate
- worst excursion
- within-run variability

#### Spark
```bash
python3 /mydata/os-param-tuning/scripts/plot_preconvergence_stability.py \
  dcperf_spark_tput \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_dcperf_spark_tput \
  --converged-only \
  --start 0 \
  --end 60 \
  --output-dir /mydata/os-param-tuning/plots
```

#### TPCC
```bash
python3 /mydata/os-param-tuning/scripts/plot_preconvergence_stability.py \
  tpcc_hi_p99 \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_tpcc_hi_p99 \
  --converged-only \
  --start 0 \
  --end 60 \
  --output-dir /mydata/os-param-tuning/plots
```

#### Silo
```bash
python3 /mydata/os-param-tuning/scripts/plot_preconvergence_stability.py \
  silo_hi_p99 \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_silo_hi_p99 \
  --converged-only \
  --start 0 \
  --end 60 \
  --output-dir /mydata/os-param-tuning/plots
```

#### Sysbench CPU throughput
```bash
python3 /mydata/os-param-tuning/scripts/plot_preconvergence_stability.py \
  sysbench_cpu_tput \
  --full-results-dir /mydata/os-param-tuning/all_results/results_config_full_param_sysbench_cpu_tput \
  --converged-only \
  --start 0 \
  --end 60 \
  --output-dir /mydata/os-param-tuning/plots
```

### 4) Generate and run cycle configs (8-param schedule)

Generate configs:
```bash
python3 /mydata/os-param-tuning/scripts/generate_8_param_cycle_configs.py
```

Run all three benchmarks (5 repeats each):
```bash
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench all --repeats 5
```

Run benchmark-specific:
```bash
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench spark --repeats 5
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench tpcc --repeats 5
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench silo --repeats 5
```

Run only selected tuning windows:
```bash
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench tpcc --tunes 2,5,10,20,30,40,50 --repeats 5
sudo bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench spark --tunes 2,5,10,20,30 --repeats 5
```

No sudo:
```bash
bash /mydata/os-param-tuning/scripts/run_8_param_cycle_schedules.sh --bench silo --repeats 5 --no-sudo
```

Logs are written to:
- `/mydata/os-param-tuning/logs/8_param_cycle_schedules/<timestamp>/...`
- recent example: `/mydata/os-param-tuning/logs/8_param_cycle_schedules/20260301_115035`

### 5) Violin plots for any available benchmark (`plot_violin.py`)

`plot_violin.py` can process these other benchmarks too.

Single benchmark example (auto-detect metric):
```bash
python3 /mydata/os-param-tuning/scripts/plot_violin.py \
  -d /mydata/os-param-tuning/all_results/results_config_full_param_xapian_hi_p99/xapian_hi_p99 \
  --start 0 --end 60 \
  -o /mydata/os-param-tuning/plots/violin_xapian_hi_p99_0_60.png
```

Explicit metric example:
```bash
python3 /mydata/os-param-tuning/scripts/plot_violin.py \
  -d /mydata/os-param-tuning/all_results/results_config_full_param_sysbench_cpu_tput/sysbench_cpu_tput \
  -m throughput \
  --start 0 --end 60 \
  -o /mydata/os-param-tuning/plots/violin_sysbench_cpu_tput_0_60.png
```

Batch all listed full-param workloads:
```bash
while read -r root workload; do
  python3 /mydata/os-param-tuning/scripts/plot_violin.py \
    -d "/mydata/os-param-tuning/${root}/${workload}" \
    --start 0 --end 60 \
    -o "/mydata/os-param-tuning/plots/violin_${workload}_0_60.png"
done <<'EOF'
results_config_full_param_dcperf_spark_tput dcperf_spark_tput
results_config_full_param_masstree_hi_p99 masstree_hi_p99
results_config_full_param_masstree_hi_p99_20260224_225040 masstree_hi_p99
results_config_full_param_masstree_lo_pwr_wrt_p99 masstree_lo_pwr_wrt_p99
results_config_full_param_sibench_hi_p99 sibench_hi_p99
results_config_full_param_sibench_lo_pwr_wrt_p99 sibench_lo_pwr_wrt_p99
results_config_full_param_silo_hi_p99 silo_hi_p99
results_config_full_param_silo_lo_pwr_wrt_p99 silo_lo_pwr_wrt_p99
results_config_full_param_sphinx_lo_pwr_wrt_p99 sphinx_lo_pwr_wrt_p99
results_config_full_param_sphinx_tput_max sphinx_tput_max
results_config_full_param_sysbench_cpu_p99 sysbench_cpu_p99
results_config_full_param_sysbench_cpu_p99_wrt_power sysbench_cpu_p99
results_config_full_param_sysbench_cpu_tput sysbench_cpu_tput
results_config_full_param_sysbench_oltp_rw_hi_p99 sysbench_oltp_rw_hi_p99
results_config_full_param_sysbench_oltp_rw_lo_pwr_wrt_p99 sysbench_oltp_rw_lo_pwr_wrt_p99
results_config_full_param_tpcc_hi_p99 tpcc_hi_p99
results_config_full_param_tpcc_lo_pwr_wrt_p99 tpcc_lo_pwr_wrt_p99
results_config_full_param_xapian_hi_p99 xapian_hi_p99
results_config_full_param_xapian_lo_pwr_wrt_p99 xapian_lo_pwr_wrt_p99
results_config_full_param_ycsb_hi_p99 ycsb_hi_p99
results_config_full_param_ycsb_lo_pwr_wrt_p99 ycsb_lo_pwr_wrt_p99
EOF
```

## Metric Definitions (as implemented)
- `Mean within-run std`:
  for each run, compute std of metric across selected window; then average those std values across runs.
- `Std(run mean)`:
  std across run-level means.
- `Bad-window rate`:
  percent of windows worse than fixed reference (goal-aware).
- `P10/P90 bad-window rate`:
  10th/90th percentile of per-run bad-window rates.
- `Worst excursion`:
  run-level worst deviation from fixed (max bad point for minimize, min bad point for maximize), then averaged across runs.

## Operational Notes
- `plot_cycle_phase_comparison.py` uses a fixed dashed horizontal line and puts legend below the plot.
- `compare_param_count_tuners.py` uses fixed dashed line per subplot and legend inside top-right.
- `--bar-stat` controls bar height statistic (`mean` or `median`), not file selection.
- `--converged-only` enforces expected run length from config (`max_iterations + post_tuning_windows`).
- If direct `sudo python3 -m src.barebones_optimizer.main` fails with repository-root errors, run through `run_8_param_cycle_schedules.sh` (it sets cwd and git safe.directory env).

## Quick Continuation Checklist for Future Codex
- Confirm paths still exist before reruns (historical TPCC/Silo param dirs may have moved).
- Regenerate plots after any new run:
  `compare_param_count_tuners.py` for param-count figures.
  `plot_cycle_phase_comparison.py` for tuning-vs-stable phase figures.
  `plot_preconvergence_stability.py` for pre-convergence robustness.
- Prefer `--converged-only` for publishable tables.
- Keep fixed baseline source explicit in command for reproducibility.
