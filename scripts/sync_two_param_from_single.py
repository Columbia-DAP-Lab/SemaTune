#!/usr/bin/env python3
"""
Sync config/two_param from config/single_param so that every two_param config
matches the corresponding single_param config in all fields except the number
of tuned parameters (two_param has 2: min_granularity_ns and latency_ns).
Run from repo root.
"""

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SINGLE_DIR = REPO_ROOT / "config" / "single_param"
TWO_DIR = REPO_ROOT / "config" / "two_param"

# Standard range for scheduler params (ns)
DEFAULT_PARAM_RANGE = [100_000, 100_000_000]

PARAMS_TWO = ["min_granularity_ns", "latency_ns"]


def single_to_two(single: dict) -> dict:
    """Convert single-param config to two-param: same content, 2 tuned params."""
    two = {}
    for k, v in single.items():
        if k in ("parameter_ranges", "parameters_to_tune", "fixed_parameters"):
            continue
        two[k] = v

    # parameter_ranges: 2 params. Use single's range for min_granularity_ns if present.
    pr = single.get("parameter_ranges") or {}
    if len(pr) == 1:
        r = list(pr.values())[0]
        two["parameter_ranges"] = {
            "min_granularity_ns": list(r),
            "latency_ns": list(r),
        }
    else:
        two["parameter_ranges"] = {
            "min_granularity_ns": list(pr.get("min_granularity_ns", DEFAULT_PARAM_RANGE)),
            "latency_ns": list(pr.get("latency_ns", DEFAULT_PARAM_RANGE)),
        }

    two["parameters_to_tune"] = PARAMS_TWO.copy()

    # fixed_parameters: keep only params that are not being tuned
    fp_single = single.get("fixed_parameters") or {}
    fp_two = {k: v for k, v in fp_single.items() if k not in PARAMS_TWO}
    if fp_two:
        two["fixed_parameters"] = fp_two

    # Some tuners use parameter_types (e.g. mlos)
    if "parameter_types" in single:
        two["parameter_types"] = {
            "min_granularity_ns": single["parameter_types"].get("min_granularity_ns", "integer"),
            "latency_ns": single["parameter_types"].get("latency_ns", "integer"),
        }
    elif single.get("tuner_type") != "fixed":
        two["parameter_types"] = {"min_granularity_ns": "integer", "latency_ns": "integer"}

    return two


def main():
    single_dir = SINGLE_DIR
    two_dir = TWO_DIR
    if not single_dir.is_dir():
        print(f"Missing {single_dir}")
        return
    two_dir.mkdir(parents=True, exist_ok=True)

    for group_path in sorted(single_dir.iterdir()):
        if not group_path.is_dir():
            continue
        group = group_path.name
        two_group = two_dir / group
        two_group.mkdir(parents=True, exist_ok=True)

        for config_path in sorted(group_path.glob("*.json")):
            name = config_path.name
            with open(config_path) as f:
                single = json.load(f)
            two = single_to_two(single)
            out_path = two_group / name
            with open(out_path, "w") as f:
                json.dump(two, f, indent=2)
            print(f"  {group}/{name}")

    # Remove two_param files that don't exist in single_param
    for group_path in sorted(two_dir.iterdir()):
        if not group_path.is_dir():
            continue
        group = group_path.name
        single_group = single_dir / group
        for config_path in list(group_path.glob("*.json")):
            name = config_path.name
            if not (single_group / name).exists():
                config_path.unlink()
                print(f"  removed (no single): {group}/{name}")


if __name__ == "__main__":
    main()
