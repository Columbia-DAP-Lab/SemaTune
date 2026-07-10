#!/usr/bin/env python3
"""Set parameter_ranges and parameters_to_tune to max_perf_pct + cstate_max for all two_param *lo_pwr_wrt_p99* configs.
   Also set constraint for two-param: latency_p99 (ms) with relaxed thresholds appropriate when tuning two power params."""

import json
from pathlib import Path

TWO_DIR = Path(__file__).resolve().parent.parent / "config" / "two_param"

POWER_PARAM_RANGES = {
    "max_perf_pct": [20, 100],
    "cstate_max": ["POLL", "C6", "C1E", "C1"],
}
POWER_PARAMS = ["max_perf_pct", "cstate_max"]
POWER_PARAM_TYPES = {"max_perf_pct": "integer", "cstate_max": "categorical"}

# Constraint for two_param lo_pwr_wrt_p99: metric is latency_p99 (ms). Thresholds relaxed for two-param power tuning.
CONSTRAINT_BY_GROUP = {
    "masstree_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 3.0, "constraint_direction": "less_than"},
    "silo_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 3.0, "constraint_direction": "less_than"},
    "sphinx_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 1000.0, "constraint_direction": "less_than"},
    "sysbench_oltp_rw_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 10.0, "constraint_direction": "less_than"},
    "tpcc_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 100.0, "constraint_direction": "less_than"},
    "ycsb_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 100.0, "constraint_direction": "less_than"},
    "xapian_lo_pwr_wrt_p99": {"constraint_metric": "latency_p99", "constraint_threshold": 25.0, "constraint_direction": "less_than"},
}


def main():
    for group_path in sorted(TWO_DIR.iterdir()):
        if not group_path.is_dir() or "lo_pwr_wrt_p99" not in group_path.name:
            continue
        group_name = group_path.name
        constraint = CONSTRAINT_BY_GROUP.get(group_name, {})
        for config_path in sorted(group_path.glob("*.json")):
            with open(config_path) as f:
                data = json.load(f)
            data["parameter_ranges"] = {k: list(v) for k, v in POWER_PARAM_RANGES.items()}
            data["parameters_to_tune"] = POWER_PARAMS.copy()
            # Remove fixed_parameters that we now tune
            fp = data.get("fixed_parameters") or {}
            data["fixed_parameters"] = {k: v for k, v in fp.items() if k not in POWER_PARAMS}
            if not data["fixed_parameters"]:
                del data["fixed_parameters"]
            # Update parameter_types if present
            if "parameter_types" in data:
                data["parameter_types"] = POWER_PARAM_TYPES.copy()
            # Set constraint for two_param lo_pwr_wrt_p99 (latency_p99 in ms, relaxed thresholds)
            if constraint:
                data["constraint_metric"] = constraint["constraint_metric"]
                data["constraint_threshold"] = constraint["constraint_threshold"]
                data["constraint_direction"] = constraint["constraint_direction"]
                if "constraint_penalty" not in data:
                    data["constraint_penalty"] = 100000.0
            with open(config_path, "w") as f:
                json.dump(data, f, indent=2)
            print(config_path.relative_to(TWO_DIR.parent))


if __name__ == "__main__":
    main()
