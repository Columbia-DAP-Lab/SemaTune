#!/usr/bin/env python3
"""Generate gist-backed system-metrics dump memory configs.

These configs are cloned from the existing
`*_llm_dual_system_metrics_plain_final_actor.json` family and seeded with the
best available gist from the corresponding
`llm_dual_indirect_all_mode3_final_actor` result runs.

For hidden-metric runs, the imported gist is sanitized so prior primary-metric
values and reward values are not leaked through the memory hint.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = REPO_ROOT / "config" / "full_param"
RESULTS_ROOT = REPO_ROOT / "all_results"

APP_MEMORY_SUFFIX = "_llm_dual_app_metrics_memory_final_actor.json"
SYSTEM_BASE_SUFFIX = "_llm_dual_system_metrics_plain_final_actor.json"
SYSTEM_MEMORY_SUFFIX = "_llm_dual_system_metrics_plain_memory_final_actor.json"
SOURCE_RESULT_DIR = "llm_dual_indirect_all_mode3_final_actor"
RESULTS_ROOT_GLOB = "results_config_full_param*"
SKIP_DIRS = {"logs", "plots"}
EXTRA_MED_WORKLOADS = {
    "tpcc_hi_p99_med",
    "silo_hi_p99_med",
    "sysbench_oltp_rw_hi_p99_med",
}


def mean_reward(history: list[dict]) -> float | None:
    post_tuning_rewards = [
        entry.get("reward")
        for entry in history
        if entry.get("post_tuning_phase") is True
        and isinstance(entry.get("reward"), (int, float))
    ]
    rewards = post_tuning_rewards or [
        entry.get("reward")
        for entry in history
        if isinstance(entry.get("reward"), (int, float))
    ]
    if not rewards:
        return None
    return statistics.mean(rewards)


def iter_target_workloads() -> list[str]:
    workloads = {
        path.parent.name
        for path in CONFIG_ROOT.glob(f"*/*{APP_MEMORY_SUFFIX}")
    }
    workloads.update(EXTRA_MED_WORKLOADS)
    return sorted(workloads)


def source_workload_for_target(target_workload: str) -> str:
    if target_workload.endswith("_med"):
        return target_workload[: -len("_med")]
    return target_workload


def find_base_config(target_workload: str) -> Path | None:
    workload_dir = CONFIG_ROOT / target_workload
    if not workload_dir.is_dir():
        return None

    for config_path in sorted(workload_dir.iterdir()):
        if config_path.name.endswith(SYSTEM_BASE_SUFFIX):
            return config_path
    return None


def iter_source_result_files(source_workload: str):
    seen = set()
    for results_root in sorted(RESULTS_ROOT.glob(RESULTS_ROOT_GLOB)):
        if not results_root.is_dir():
            continue
        source_dir = results_root / source_workload / SOURCE_RESULT_DIR
        if not source_dir.is_dir():
            continue
        for result_file in sorted(source_dir.glob("*.json")):
            if result_file.name.startswith("window_"):
                continue
            if result_file in seen:
                continue
            seen.add(result_file)
            yield result_file


def choose_gist_source(source_workload: str):
    ranked_runs = []
    optimization_goal = None

    for result_file in iter_source_result_files(source_workload):
        try:
            result = json.loads(result_file.read_text())
        except json.JSONDecodeError as exc:
            print(f"SKIP {result_file}: invalid JSON ({exc})", file=sys.stderr)
            continue

        optimization_goal = (result.get("config") or {}).get("optimization_goal", optimization_goal)
        avg_reward = mean_reward(result.get("history") or [])
        if avg_reward is None:
            continue

        gist = str(result.get("optimizer_gist") or "").strip()
        ranked_runs.append(
            {
                "avg_reward": avg_reward,
                "gist": gist,
                "result_file": result_file,
                "optimization_metric": (result.get("config") or {}).get("optimization_metric"),
            }
        )

    if not ranked_runs:
        return None, f"no scored {SOURCE_RESULT_DIR} runs found for {source_workload}"

    reverse = optimization_goal == "maximize"
    ranked_runs.sort(key=lambda run: run["avg_reward"], reverse=reverse)

    for run in ranked_runs:
        if run["gist"]:
            return run, None

    return None, f"{SOURCE_RESULT_DIR} runs exist for {source_workload} but none contain optimizer_gist"


def sanitize_hidden_metric_gist(gist: str, optimization_metric: str | None) -> str:
    text = str(gist).strip()
    if not text:
        return text

    metric_pattern = re.escape(optimization_metric) if optimization_metric else None

    if metric_pattern:
        text = re.sub(
            rf"\b{metric_pattern}\b",
            "hidden primary metric",
            text,
            flags=re.IGNORECASE,
        )

    replacements = (
        (r"(?i)\brewards\b", "hidden-score signals"),
        (r"(?i)\breward\b", "hidden-score signal"),
        (
            r"(?i)highest hidden-score signal(?: obtained)?(?: was| achieved)?\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "highest hidden-score signal was achieved",
        ),
        (
            r"(?i)best hidden-score signal(?: was)?\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "best hidden-score signal",
        ),
        (
            r"(?i)hidden-score signal of\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "strong hidden-score signal",
        ),
        (
            r"(?i)yielding the highest hidden-score signal of\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "yielding the strongest hidden-score signal",
        ),
        (
            r"(?i)with (?:a )?hidden-score signal of\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "with a strong hidden-score signal",
        ),
        (
            r"(?i)hidden-score signal\s*\(\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?\s*\)",
            "hidden-score signal",
        ),
        (
            r"(?i)hidden primary metric(?:\s*(?:of|=|was|is))\s*\*?\*?[-+]?\d+(?:\.\d+)?\*?\*?",
            "hidden primary metric",
        ),
    )

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    return (
        "Hidden primary-metric values from the prior run have been redacted. "
        "Use the qualitative parameter patterns and configuration hints below.\n\n"
        + text
    )


def build_memory_config(base_config_path: Path, target_workload: str, gist: str):
    config = json.loads(base_config_path.read_text())
    prefix = base_config_path.name[: -len(SYSTEM_BASE_SUFFIX)]
    output_path = base_config_path.parent / f"{prefix}{SYSTEM_MEMORY_SUFFIX}"

    config["results_dir"] = f"results/{target_workload}/llm_dual_system_metrics_plain_memory_final_actor/"
    config["previous_run_gist"] = gist
    # Preserve hidden-metric dump semantics explicitly.
    config["llm_additional_metrics_dump_only"] = True
    config["llm_hide_primary_metric_value"] = True

    return output_path, config


def main() -> int:
    written = 0
    skipped = 0

    for target_workload in iter_target_workloads():
        base_config_path = find_base_config(target_workload)
        if base_config_path is None:
            print(
                f"SKIP {target_workload}: no base config ending with {SYSTEM_BASE_SUFFIX}",
                file=sys.stderr,
            )
            skipped += 1
            continue

        source_workload = source_workload_for_target(target_workload)
        selection, reason = choose_gist_source(source_workload)
        if selection is None:
            print(f"SKIP {target_workload}: {reason}", file=sys.stderr)
            skipped += 1
            continue

        sanitized_gist = sanitize_hidden_metric_gist(
            selection["gist"],
            selection.get("optimization_metric"),
        )
        output_path, config = build_memory_config(base_config_path, target_workload, sanitized_gist)
        output_path.write_text(json.dumps(config, indent=2) + "\n")
        print(
            f"WROTE {output_path.relative_to(REPO_ROOT)} "
            f"<- {selection['result_file'].relative_to(REPO_ROOT)} "
            f"(avg_post_tuning_reward={selection['avg_reward']:.4f})"
        )
        written += 1

    print(f"Total written: {written}")
    print(f"Total skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
