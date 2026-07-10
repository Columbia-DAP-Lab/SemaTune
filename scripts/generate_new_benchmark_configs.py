#!/usr/bin/env python3
"""
Generate single_param base configs for auctionmark, otmetrics, and wikipedia,
then generate the additional tuner variants for both single_param and full_param.
"""
import os
import json
import copy
import glob

# Keep generated configs credential-free. The optimizer resolves GEMINI_API_KEY
# from the environment when it runs.
API_KEY = None

# Benchmark definitions: (benchmark_name, benchbase_config_file)
BENCHMARKS = {
    "auctionmark": "config/benchbase/postgres/auctionmark_highload_config.xml",
    "otmetrics":    "config/benchbase/postgres/otmetrics_highload_config.xml",
    "wikipedia":    "config/benchbase/postgres/wikipedia_highload_config.xml",
}

# Single param: min_granularity_ns only
SINGLE_PARAM_RANGES = {
    "min_granularity_ns": [100000, 100000000]
}
SINGLE_PARAMS_TO_TUNE = ["min_granularity_ns"]

# Full param: all 8 parameters
FULL_PARAM_RANGES = {
    "min_granularity_ns": [100000, 100000000],
    "latency_ns": [100000, 100000000],
    "cstate_max": ["POLL", "C6", "C3", "C1E", "C1"],
    "napi_busy_poll": [0, 1000],
    "wakeup_granularity_ns": [100000, 100000000],
    "migration_cost_ns": [100000, 100000000],
    "max_perf_pct": [0, 100],
    "min_perf_pct": [0, 100]
}
FULL_PARAMS_TO_TUNE = [
    "min_granularity_ns", "latency_ns", "cstate_max", "napi_busy_poll",
    "wakeup_granularity_ns", "migration_cost_ns", "max_perf_pct", "min_perf_pct"
]

METRICS_LIST = [
    "instructions_per_cycle", "cycles", "instructions", "branch_misses",
    "branch_miss_rate_pct", "page_faults", "context_switches_per_sec",
    "cpu_migrations_per_sec", "branches_per_sec", "power_socket0_watts",
    "power_ram_watts", "cstate_poll_pct", "cstate_c1_pct", "cstate_c1e_pct",
    "cstate_c6_pct", "cpu_load_cores_pct", "cpu_load_socket0_pct"
]

APP_METRICS_LIST = [
    "throughput",
    "goodput",
    "latency_avg",
    "latency_p95",
    "latency_p99",
    "queries_per_hour",
]

# Tuner configs: (tuner_type, model_name, extra_fields)
BASE_TUNERS = {
    "fixed": {"tuner_type": "fixed"},
    "llm":   {"tuner_type": "llm", "llm_model_name": "gemini-2.5-flash-lite", "llm_api_key": API_KEY},
    "llm_reasoning": {"tuner_type": "llm", "llm_model_name": "gemini-2.5-flash", "llm_api_key": API_KEY},
    "bayesian": {"tuner_type": "bayesian"},
    "dqn": {"tuner_type": "dqn"},
    "mlos": {"tuner_type": "mlos"},
    "qlearning": {"tuner_type": "qlearning"},
}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
        f.write('\n')
    print(f"  Created: {path}")


def make_base_config(benchmark, benchbase_config, param_ranges, params_to_tune,
                     fixed_params, results_dir):
    return {
        "benchmark": benchmark,
        "pin_to_cores": "0-9",
        "benchbase_jar_path": "deps/benchbase/target/benchbase-postgres/benchbase.jar",
        "benchbase_config_file": benchbase_config,
        "parameter_ranges": param_ranges,
        "parameters_to_tune": params_to_tune,
        "fixed_parameters": fixed_params,
        "optimization_metric": "latency_p99",
        "optimization_goal": "minimize",
        "max_iterations": 60,
        "post_tuning_windows": 20,
        "window_duration": 10,
        "tuning_mode": "outside-of-window",
        "continuous_apply": False,
        "results_dir": results_dir,
    }


def get_results_dir_suffix(tuner_name):
    """Get the results_dir suffix for a tuner."""
    suffixes = {
        "fixed": "fixed",
        "llm": "llm_gemini_2_5_flash_lite",
        "llm_reasoning": "llm_gemini_2_5_flash",
        "bayesian": "bayesian",
        "dqn": "dqn",
        "mlos": "mlos",
        "qlearning": "qlearning",
    }
    return suffixes.get(tuner_name, tuner_name)


def generate_base_configs(config_root, benchmark, benchbase_config, param_ranges,
                          params_to_tune, scenario_name):
    """Generate the 7 base tuner configs for a benchmark scenario."""
    dir_path = os.path.join(config_root, scenario_name)
    os.makedirs(dir_path, exist_ok=True)

    for tuner_name, tuner_fields in BASE_TUNERS.items():
        suffix = get_results_dir_suffix(tuner_name)
        results_dir = f"results/{scenario_name}/{suffix}/"

        # Determine fixed_parameters based on tuner
        if tuner_name == "fixed":
            fixed_params = {"latency_ns": 24000000}
        elif tuner_name == "llm":
            fixed_params = {"latency_ns": 1000}
        else:
            fixed_params = {"latency_ns": 1000}

        config = make_base_config(
            benchmark, benchbase_config, param_ranges, params_to_tune,
            fixed_params, results_dir
        )
        config.update(tuner_fields)

        filename = f"{benchmark}_config_{tuner_name}.json"
        filepath = os.path.join(dir_path, filename)
        if os.path.exists(filepath):
            print(f"  Skipping {filepath} (already exists)")
            continue
        save_json(filepath, config)


def generate_variant_configs(config_root):
    """Generate additional tuner variants for all benchmark scenario folders.
    
    This works on all subdirectories of config_root.
    """
    subdirs = [d for d in os.listdir(config_root) if os.path.isdir(os.path.join(config_root, d))]

    for subdir in sorted(subdirs):
        dir_path = os.path.join(config_root, subdir)

        # Find a base config. Prefer llm.json, then fixed.json
        candidates = glob.glob(os.path.join(dir_path, "*_config_llm.json"))
        if not candidates:
            candidates = glob.glob(os.path.join(dir_path, "*_config_fixed.json"))
        if not candidates:
            print(f"Skipping {subdir}: No base config found.")
            continue

        base_config_path = candidates[0]
        base_name = os.path.basename(base_config_path)
        prefix = base_name.replace("_config_llm.json", "").replace("_config_fixed.json", "").replace(".json", "")

        print(f"Processing {subdir} using base {base_name}...")
        with open(base_config_path, 'r') as f:
            base_data = json.load(f)

        def gen_config(suffix, modifier_func):
            new_filename = f"{prefix}_config_{suffix}.json"
            new_path = os.path.join(dir_path, new_filename)
            if os.path.exists(new_path):
                print(f"  Skipping {new_filename} (already exists)")
                return
            data = copy.deepcopy(base_data)
            data["post_tuning_windows"] = 20
            modifier_func(data)
            save_json(new_path, data)

        def set_results_dir(data, tuner_dir_name):
            """Set the tuner directory component of results_dir."""
            if "results_dir" in data:
                original = data["results_dir"].rstrip('/')
                # results_dir format: results/<scenario>/<tuner_name>/
                # We want to replace the last path component
                scenario_dir = os.path.dirname(original)
                data["results_dir"] = f"{scenario_dir}/{tuner_dir_name}/"

        def metric_list_with_primary(data, base_metrics):
            """Return metrics list with optimization metric appended (deduped)."""
            metrics = list(base_metrics)
            primary = data.get("optimization_metric")
            if primary and primary not in metrics:
                metrics.append(primary)
            return metrics

        def setup_dual_defaults(data):
            data["tuner_type"] = "llm"
            data["tuning_mode"] = "in-window"
            data["llm_actor_model"] = "gemini-2.5-flash"
            data["llm_speculator_model"] = "gemini-2.5-flash-lite"
            if "llm_model_name" in data:
                del data["llm_model_name"]
            if "llm_secondary_model" in data:
                del data["llm_secondary_model"]

        # 1. IPC
        def setup_ipc(data):
            data["tuner_type"] = "llm"
            data["llm_additional_metrics"] = []
            set_results_dir(data, "llm_gemini_2_5_flash_lite_ipc")
            data["use_indirect_optimization"] = False
        gen_config("llm_ipc", setup_ipc)

        # 2. Indirect
        def setup_indirect(data):
            data["tuner_type"] = "llm"
            data["use_indirect_optimization"] = True
            data["llm_indirect_history_show_all_metrics"] = False
            data["llm_additional_metrics"] = METRICS_LIST
            set_results_dir(data, "llm_gemini_2_5_flash_lite_indirect")
        gen_config("llm_indirect", setup_indirect)

        # 3. Indirect All
        def setup_indirect_all(data):
            data["tuner_type"] = "llm"
            data["use_indirect_optimization"] = True
            data["llm_indirect_history_show_all_metrics"] = True
            data["llm_additional_metrics"] = METRICS_LIST
            set_results_dir(data, "llm_gemini_2_5_flash_lite_indirect_all")
        gen_config("llm_indirect_all", setup_indirect_all)

        # 4. MLOS Trimming
        def setup_mlos_trimming(data):
            data["tuner_type"] = "mlos"
            data["trimming_enabled"] = True
            data["trimming_cycles"] = 5
            data["trimming_model_name"] = "gemini-2.5-flash-lite"
            set_results_dir(data, "llm_gemini_2_5_flash_lite_mlos_trimming")
        gen_config("mlos_trimming", setup_mlos_trimming)

        # 5. LLM Dual Loop
        def setup_llm_dual(data):
            setup_dual_defaults(data)
            # data["trimming_enabled"] = True
            # data["trimming_cycles"] = 5
            # data["trimming_strategy"] = "single_loop"
            set_results_dir(data, "llm_gemini_2_5_flash_lite_llm_dual")
        gen_config("llm_dual", setup_llm_dual)

        # 6. LLM Dual Full Metrics Mode2
        def setup_llm_dual_full_metrics_mode2(data):
            setup_dual_defaults(data)
            data["use_indirect_optimization"] = False
            data["llm_full_metrics_prompt_mode"] = True
            data["llm_full_metrics_explicit_signature_compare"] = False
            data["llm_additional_metrics"] = metric_list_with_primary(data, METRICS_LIST)
            set_results_dir(data, "llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode2")
        gen_config("llm_dual_full_metrics_mode2", setup_llm_dual_full_metrics_mode2)

        # 7. LLM Dual Full Metrics Mode3
        def setup_llm_dual_full_metrics_mode3(data):
            setup_dual_defaults(data)
            data["use_indirect_optimization"] = False
            data["llm_full_metrics_prompt_mode"] = True
            data["llm_full_metrics_explicit_signature_compare"] = True
            data["llm_additional_metrics"] = metric_list_with_primary(data, METRICS_LIST)
            set_results_dir(data, "llm_gemini_2_5_flash_lite_llm_dual_full_metrics_mode3")
        gen_config("llm_dual_full_metrics_mode3", setup_llm_dual_full_metrics_mode3)

        # 8. LLM Dual App Metrics Mode2
        def setup_llm_dual_app_metrics_mode2(data):
            setup_dual_defaults(data)
            data["use_indirect_optimization"] = False
            data["llm_full_metrics_prompt_mode"] = True
            data["llm_full_metrics_explicit_signature_compare"] = False
            data["llm_additional_metrics"] = metric_list_with_primary(data, APP_METRICS_LIST)
            set_results_dir(data, "llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode2")
        gen_config("llm_dual_app_metrics_mode2", setup_llm_dual_app_metrics_mode2)

        # 9. LLM Dual App Metrics Mode3
        def setup_llm_dual_app_metrics_mode3(data):
            setup_dual_defaults(data)
            data["use_indirect_optimization"] = False
            data["llm_full_metrics_prompt_mode"] = True
            data["llm_full_metrics_explicit_signature_compare"] = True
            data["llm_additional_metrics"] = metric_list_with_primary(data, APP_METRICS_LIST)
            set_results_dir(data, "llm_gemini_2_5_flash_lite_llm_dual_app_metrics_mode3")
        gen_config("llm_dual_app_metrics_mode3", setup_llm_dual_app_metrics_mode3)


def main():
    print("=" * 60)
    print("Step 1: Generate single_param base configs")
    print("=" * 60)
    for benchmark, benchbase_config in BENCHMARKS.items():
        scenario = f"{benchmark}_hi_p99"
        print(f"\n--- {scenario} ---")
        generate_base_configs(
            "config/single_param", benchmark, benchbase_config,
            SINGLE_PARAM_RANGES, SINGLE_PARAMS_TO_TUNE, scenario
        )

    print("\n" + "=" * 60)
    print("Step 2: Generate missing variant configs for single_param")
    print("=" * 60)
    generate_variant_configs("config/single_param")

    print("\n" + "=" * 60)
    print("Step 3: Generate missing variant configs for full_param")
    print("=" * 60)
    generate_variant_configs("config/full_param")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
