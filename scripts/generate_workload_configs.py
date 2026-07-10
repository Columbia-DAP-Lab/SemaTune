#!/usr/bin/env python3
import os
import json
import copy

BASE_DIR = "config/single_param"
WORKLOAD_SUFFIX = "_workload_change"

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
        f.write('\n')
    print(f"Created: {path}")

def main():
    # Targets: tpcc, ycsb, sibench
    # We'll look for *existing* configs for these benchmarks and create new ones with workload changes.
    # We'll use the 'mlos' or 'llm' config as base. Prefer 'mlos' for stability/simplicity if available?
    # User asked for "create a config for tpcc ycsb and sibench... In the end, give single commands to test... for each of these configs you implemented in the previous discussion so far."
    # Wait, the user might want *all* variations (llm, mlos, etc.) or just one representative.
    # The request: "Create a config for tpcc ycsb and sibench... Also add this in the run batch."
    # Let's create `_mlos_workload.json` and `_llm_workload.json` for each if possible.
    # But let's start with a standard one, e.g., `_llm_indirect_all_workload.json` or just `_workload.json` inheriting from `_llm_indirect_all` (best context).
    
    # Actually, simpler: take the `_llm_indirect_all.json` config (which has history) and add workload changes.
    
    targets = ["tpcc", "ycsb", "sibench"]
    
    # We need to find where these are located.
    # config/single_param/<benchmark>_<scenario>/...
    
    # Let's scan BASE_DIR
    for subdir in sorted(os.listdir(BASE_DIR)):
        subdir_path = os.path.join(BASE_DIR, subdir)
        if not os.path.isdir(subdir_path):
            continue
            
        # Check if this subdir belongs to one of our targets
        is_target = False
        for target in targets:
            if target in subdir:
                is_target = True
                break
        
        if not is_target:
            continue
            
        print(f"Processing directory: {subdir}")
        
        # Find base config: prefer *_config_llm_indirect_all.json
        base_candidates = [f for f in os.listdir(subdir_path) if f.endswith("_config_llm_indirect_all.json")]
        if not base_candidates:
            # Fallback to _llm.json
            base_candidates = [f for f in os.listdir(subdir_path) if f.endswith("_config_llm.json")]
            
        if not base_candidates:
            print(f"  No base config found in {subdir}")
            continue
            
        base_filename = base_candidates[0]
        base_path = os.path.join(subdir_path, base_filename)
        base_data = load_json(base_path)
        
        # Create new config data
        new_data = copy.deepcopy(base_data)
        
        # Add workload change settings
        new_data["workload_change_type"] = "cyclic_halving"
        new_data["workload_change_interval"] = 5
        # metric to change is determined by benchmark logic (rate), no need to set param name explicitly if logic is hardcoded
        # but we can set it for documentation
        new_data["workload_change_param"] = "rate"
        
        # Update results_dir
        if "results_dir" in new_data:
            original_results = new_data["results_dir"].rstrip('/')
            new_data["results_dir"] = f"{original_results}{WORKLOAD_SUFFIX}/"
            
        # Create new filename
        # e.g., tailbench_tpcc_config_llm_indirect_all.json -> tailbench_tpcc_config_workload.json
        # Or better: keep the base flavor and append workload? 
        # User said "Create a config...". Let's name it `_workload.json` to be distinct.
        # But wait, we want to know WHICH tuner it uses.
        # So `tailbench_tpcc_config_llm_indirect_all_workload.json` seems best.
        
        new_filename = base_filename.replace(".json", f"{WORKLOAD_SUFFIX}.json")
        new_path = os.path.join(subdir_path, new_filename)
        
        save_json(new_path, new_data)

if __name__ == "__main__":
    main()
