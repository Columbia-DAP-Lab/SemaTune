#!/usr/bin/env python3
import os
import json
import glob
import copy

BASE_DIR = "config/single_param"

METRICS_LIST = [
    "instructions_per_cycle", "cycles", "instructions", "branch_misses",
    "branch_miss_rate_pct", "page_faults", "context_switches_per_sec",
    "cpu_migrations_per_sec", "branches_per_sec", "power_socket0_watts",
    "power_ram_watts", "cstate_poll_pct", "cstate_c1_pct", "cstate_c1e_pct",
    "cstate_c6_pct", "cpu_load_cores_pct", "cpu_load_socket0_pct"
]

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
        f.write('\n')
    print(f"Created: {path}")

def update_results_dir(data, new_suffix):
    # Appends suffix to the last directory component of results_dir
    if "results_dir" in data:
        original = data["results_dir"].rstrip('/')
        base = os.path.dirname(original)
        name = os.path.basename(original) if os.path.basename(original) else original
        
        # Strip existing suffix if present to avoid chaining (e.g. _indirect_all_indirect_all)
        # Heuristic: if name ends with _indirect or _ipc, strip it? 
        # Better: just replace the tuner part if we can identify it.
        # But commonly results_dir is "results/scenario/tuner/".
        # We want "results/scenario/tuner_suffix/".
        
        # Let's assume standard format: results/<benchmark>/<tuner_dir>/
        # We will append the suffix to the tuner_dir.
        
        data["results_dir"] = f"{original}{new_suffix}/"

def main():
    # Get all subdirectories
    subdirs = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    
    for subdir in sorted(subdirs):
        dir_path = os.path.join(BASE_DIR, subdir)
        
        # Find a base config. Prefer llm.json, then fixed.json
        base_config_path = None
        candidates = glob.glob(os.path.join(dir_path, "*_config_llm.json"))
        if not candidates:
            candidates = glob.glob(os.path.join(dir_path, "*_config_fixed.json"))
        
        if not candidates:
            print(f"Skipping {subdir}: No base config found.")
            continue
            
        base_config_path = candidates[0]
        base_name = os.path.basename(base_config_path)
        # Remove _llm.json or _fixed.json suffix to get prefix
        prefix = base_name.replace("_config_llm.json", "").replace("_config_fixed.json", "").replace(".json", "")
        
        print(f"Processing {subdir} using base {base_name}...")
        base_data = load_json(base_config_path)
        
        # Helper to generate config
        def gen_config(suffix, modifier_func):
            new_filename = f"{prefix}_config_{suffix}.json"
            new_path = os.path.join(dir_path, new_filename)
            if os.path.exists(new_path):
                print(f"  Skipping {new_filename} (already exists)")
                return
            
            data = copy.deepcopy(base_data)
            modifier_func(data)
            save_json(new_path, data)

        # 1. IPC
        def setup_ipc(data):
            data["tuner_type"] = "llm"
            data["llm_additional_metrics"] = []
            update_results_dir(data, "_ipc")
            # Ensure indirect is off
            data["use_indirect_optimization"] = False
            
        gen_config("llm_ipc", setup_ipc)

        # 2. Indirect
        def setup_indirect(data):
            data["tuner_type"] = "llm"
            data["use_indirect_optimization"] = True
            data["llm_indirect_history_show_all_metrics"] = False
            data["llm_additional_metrics"] = METRICS_LIST
            update_results_dir(data, "_indirect")

        gen_config("llm_indirect", setup_indirect)

        # 3. Indirect All
        def setup_indirect_all(data):
            data["tuner_type"] = "llm"
            data["use_indirect_optimization"] = True
            data["llm_indirect_history_show_all_metrics"] = True
            data["llm_additional_metrics"] = METRICS_LIST
            update_results_dir(data, "_indirect_all")

        gen_config("llm_indirect_all", setup_indirect_all)
        
        # 4. MLOS Trimming
        def setup_mlos_trimming(data):
            data["tuner_type"] = "mlos"
            data["trimming_enabled"] = True
            data["trimming_cycles"] = 5
            data["trimming_model_name"] = "gemini-2.5-flash-lite" # Default cheap model for trimming
            update_results_dir(data, "_mlos_trimming")
            # Clear LLM-specific fields from base if they pollute? 
            # SimpleConfig ignores extra fields usually, but good to clean.
            # But let's keep it simple.
            
        gen_config("mlos_trimming", setup_mlos_trimming)
        
        # 5. LLM Dual Loop
        def setup_llm_dual(data):
            data["tuner_type"] = "llm"
            data["llm_actor_model"] = "gemini-2.5-flash"
            data["llm_speculator_model"] = "gemini-2.5-flash-lite"
            
            # Enable Trimming (Single Loop by default)
            # data["trimming_enabled"] = True
            # data["trimming_cycles"] = 5
            # data["trimming_strategy"] = "single_loop" # Use Actor to trim (as per user request: "default should be single loop")
            # We don't set trimming_model_name explicitly, so it will default to llm_actor_model (gemini-2.5-flash)
            # via the logic added to main.py
            
            # Ensure reasonable defaults
            if "llm_model_name" in data:
                del data["llm_model_name"] # Let actor model take precedence / clarify
            
            update_results_dir(data, "_llm_dual")
            
        gen_config("llm_dual", setup_llm_dual)

if __name__ == "__main__":
    main()
