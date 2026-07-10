#!/usr/bin/env python3
"""
Generate 8-param cycle-schedule configs for Spark, Silo, and TPCC.

Creates configs for two tuners:
- mlos
- llm_dual_full_metrics_mode3

Schedules generated:
- Spark: tuning {1,2,5,10,20,30} + stable 10
- Silo:  tuning {1,2,5,10,20,30,40,50} + stable 10
- TPCC:  tuning {1,2,5,10,20,30,40,50} + stable 10

Each generated config sets:
- max_iterations = tuning
- post_tuning_windows = stable
- results_dir suffixed with _tune{tuning}_stable{stable}
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "config" / "8_param"
OUTPUT_ROOT = REPO_ROOT / "config" / "8_param_cycle_schedules"

STABLE_WINDOWS = 10
SPARK_TUNING_WINDOWS = [1, 2, 5, 10, 20, 30]
TAILBENCH_TUNING_WINDOWS = [1, 2, 5, 10, 20, 30, 40, 50]


BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "dcperf_spark_tput": {
        "prefix": "dcperf_spark_config",
        "tuning_windows": SPARK_TUNING_WINDOWS,
    },
    "silo_hi_p99": {
        "prefix": "tailbench_silo_config",
        "tuning_windows": TAILBENCH_TUNING_WINDOWS,
    },
    "tpcc_hi_p99": {
        "prefix": "tpcc_config",
        "tuning_windows": TAILBENCH_TUNING_WINDOWS,
    },
}

TUNERS = {
    "mlos": "mlos",
    "llm_dual_full_metrics_mode3": "llm_dual_full_metrics_mode3",
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def with_schedule_suffix(results_dir: str, tuning_windows: int, stable_windows: int) -> str:
    clean = results_dir.rstrip("/")
    return f"{clean}_tune{tuning_windows}_stable{stable_windows}/"


def generate() -> int:
    generated = 0

    for bench_name, bench_meta in BENCHMARKS.items():
        prefix = bench_meta["prefix"]
        tuning_windows_list: List[int] = bench_meta["tuning_windows"]
        source_dir = SOURCE_ROOT / bench_name
        output_dir = OUTPUT_ROOT / bench_name

        for tuner_key, tuner_suffix in TUNERS.items():
            source_file = source_dir / f"{prefix}_{tuner_suffix}.json"
            if not source_file.is_file():
                raise FileNotFoundError(f"Missing source config: {source_file}")

            base = load_json(source_file)

            for tuning_windows in tuning_windows_list:
                payload = copy.deepcopy(base)
                # max_iterations = tuning windows; post_tuning_windows adds stable windows.
                payload["max_iterations"] = tuning_windows
                payload["post_tuning_windows"] = STABLE_WINDOWS
                if isinstance(payload.get("results_dir"), str) and payload["results_dir"].strip():
                    payload["results_dir"] = with_schedule_suffix(
                        payload["results_dir"], tuning_windows=tuning_windows, stable_windows=STABLE_WINDOWS
                    )

                out_name = (
                    f"{prefix}_{tuner_suffix}_tune{tuning_windows}_stable{STABLE_WINDOWS}.json"
                )
                out_file = output_dir / out_name
                write_json(out_file, payload)
                generated += 1

    return generated


def main() -> None:
    generated = generate()
    print(f"Generated {generated} config files under: {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
