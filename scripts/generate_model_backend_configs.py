#!/usr/bin/env python3
"""Generate config families for alternative model backends.

Creates llm_dual_app_metrics_gemini31_final_actor and
llm_dual_app_metrics_kimi_final_actor configs by cloning the default
llm_dual_app_metrics_final_actor config and swapping model names.

Gemini backend variants use direct Gemini API model codes. Kimi variants use
OpenRouter model IDs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from barebones_optimizer.model_versions import (  # noqa: E402
    GEMINI_31_ACTOR_MODEL,
    GEMINI_COMPARISON_ACTOR_MODEL,
    GEMINI_COMPARISON_SPECULATOR_MODEL,
    KIMI_ACTOR_MODEL,
    KIMI_SPECULATOR_MODEL,
)

MODEL_BACKENDS = {
    "gemini31": {
        "llm_actor_model": GEMINI_31_ACTOR_MODEL,
        "llm_speculator_model": GEMINI_COMPARISON_SPECULATOR_MODEL,
        "suffix": "gemini31_final_actor",
    },
    "gemini3flash31lite": {
        "llm_actor_model": GEMINI_COMPARISON_ACTOR_MODEL,
        "llm_speculator_model": GEMINI_COMPARISON_SPECULATOR_MODEL,
        "suffix": "gemini3flash31lite_final_actor",
    },
    "kimi": {
        "llm_actor_model": KIMI_ACTOR_MODEL,
        "llm_speculator_model": KIMI_SPECULATOR_MODEL,
        "suffix": "kimi_final_actor",
    },
}

WORKLOADS = [
    "tpcc_hi_p99",
    "silo_hi_p99",
    "sysbench_oltp_rw_hi_p99",
    "dcperf_spark_online_tput",
]

CONFIG_ROOT = REPO_ROOT / "config" / "full_param"


def find_base_config(workload: str) -> Path | None:
    workload_dir = CONFIG_ROOT / workload
    if not workload_dir.is_dir():
        return None
    for f in sorted(workload_dir.iterdir()):
        if f.name.endswith("_llm_dual_app_metrics_final_actor.json"):
            return f
    return None


def main() -> None:
    created = 0
    for workload in WORKLOADS:
        base_path = find_base_config(workload)
        if base_path is None:
            print(f"SKIP {workload}: no base config found", file=sys.stderr)
            continue

        with base_path.open() as f:
            base = json.load(f)

        prefix = base_path.name.rsplit("_llm_dual_app_metrics_final_actor.json", 1)[0]

        for tag, spec in MODEL_BACKENDS.items():
            out_name = f"{prefix}_llm_dual_app_metrics_{spec['suffix']}.json"
            out_path = base_path.parent / out_name

            cfg = dict(base)
            cfg["llm_actor_model"] = spec["llm_actor_model"]
            cfg["llm_speculator_model"] = spec["llm_speculator_model"]
            cfg["results_dir"] = f"results/{workload}/llm_dual_app_metrics_{spec['suffix']}/"

            with out_path.open("w") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")

            print(f"Created: {out_path.relative_to(CONFIG_ROOT.parent.parent)}")
            created += 1

    print(f"\nTotal configs created: {created}")


if __name__ == "__main__":
    main()
