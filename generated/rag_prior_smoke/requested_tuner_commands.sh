#!/usr/bin/env bash
set -euo pipefail

./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "fixed"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_app_metrics_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_indirect_all_mode3_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor"
./run_configs_batch.sh /mydata/os-param-tuning/generated/rag_prior_smoke/batch_stage_subset 5 --tuners "llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor"
