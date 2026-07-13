#!/usr/bin/env python3
"""Measure dual-loop Gemini latency by replaying actual tuner prompt contexts."""

from __future__ import annotations

import argparse
import copy
import csv
import glob
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from optimizer.benchmark import BenchmarkMetrics
from optimizer.config import SimpleConfig
from optimizer.tuners.llm import LLMTuner


PARAM_COUNTS_DEFAULT = [1, 2, 4, 8, 16, 32, 41]
SILO_RESULTS_GLOB = (
    "results_params/silo_hi_p99_final/**/ablation_params/{count}_param/"
    "silo_hi_p99/llm_dual_app_metrics_final_actor/*.json"
)
SILO_8_CONFIG = Path(
    str(Path(__file__).resolve().parents[1] / "config" / "8_param" / "silo_hi_p99") + "/"
    "tailbench_silo_config_llm_dual_app_metrics_final_actor.json"
)
LAST_TUNING_ITERATIONS = range(21, 31)


@dataclass
class ReplayContext:
    config: SimpleConfig
    iteration: int
    current_params: Dict[str, Any]
    metrics: BenchmarkMetrics
    best_reward: float
    history: List[Dict[str, Any]]
    baseline_index: int
    aggregation_interval_s: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure replayed Actor/Speculator latency using actual dual-loop prompt contexts."
    )
    parser.add_argument("--counts", nargs="+", type=int, default=PARAM_COUNTS_DEFAULT)
    parser.add_argument("--csv-output", default="")
    parser.add_argument("--json-output", default="")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def history_entry_to_metrics(entry: Dict[str, Any]) -> BenchmarkMetrics:
    metrics_dict = dict(entry.get("metrics", {}) or {})
    return BenchmarkMetrics(
        throughput=metrics_dict.pop("throughput", 0.0),
        goodput=metrics_dict.pop("goodput", 0.0),
        latency_avg=metrics_dict.pop("latency_avg", 0.0),
        latency_p95=metrics_dict.pop("latency_p95", 0.0),
        extra_metrics=metrics_dict,
    )


def compute_best_reward(history: Sequence[Dict[str, Any]], goal: str) -> float:
    rewards = [
        entry.get("reward")
        for entry in history
        if not entry.get("pre_tuning_default_config") and entry.get("reward") is not None
    ]
    if not rewards:
        return 0.0
    return min(rewards) if goal == "minimize" else max(rewards)


def filter_params(params: Dict[str, Any], config: SimpleConfig) -> Dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in params.items() if k in config.parameter_ranges}


def filtered_history_slice(
    source_history: Sequence[Dict[str, Any]],
    end_index: int,
    config: SimpleConfig,
) -> List[Dict[str, Any]]:
    snapshot = copy.deepcopy(list(source_history[: end_index + 1]))
    for entry in snapshot:
        if "parameters" in entry and isinstance(entry["parameters"], dict):
            entry["parameters"] = filter_params(entry["parameters"], config)
        if "params" in entry and isinstance(entry["params"], dict):
            entry["params"] = filter_params(entry["params"], config)
    return snapshot


def build_contexts_from_result(
    result_path: Path,
    target_config: SimpleConfig | None = None,
) -> List[ReplayContext]:
    payload = load_json(result_path)
    config = target_config or SimpleConfig.from_dict(dict(payload["config"]))
    config.llm_api_log_enabled = False
    config.llm_request_max_retries = 1
    config.llm_request_retry_backoff_sec = 0.0
    source_history = payload["history"]
    contexts: List[ReplayContext] = []

    by_iteration = {
        entry.get("iteration"): idx
        for idx, entry in enumerate(source_history)
        if not entry.get("pre_tuning_default_config")
    }

    for iteration in LAST_TUNING_ITERATIONS:
        if iteration not in by_iteration:
            continue
        idx = by_iteration[iteration]
        history_snapshot = filtered_history_slice(source_history, idx, config)
        current_entry = history_snapshot[-1]
        contexts.append(
            ReplayContext(
                config=config,
                iteration=iteration,
                current_params=filter_params(current_entry.get("parameters", {}), config),
                metrics=history_entry_to_metrics(current_entry),
                best_reward=compute_best_reward(history_snapshot, config.optimization_goal),
                history=history_snapshot,
                baseline_index=len(history_snapshot),
                aggregation_interval_s=float(getattr(config, "window_duration", 5) or 5.0),
            )
        )

    return contexts


def resolve_source_result(count: int) -> Path:
    matches = sorted(glob.glob(SILO_RESULTS_GLOB.format(count=count), recursive=True))
    if not matches:
        raise FileNotFoundError(f"No archived silo final-actor result found for {count}_param")
    return Path(matches[0])


def build_contexts_for_count(count: int) -> List[ReplayContext]:
    if count == 8:
        source_result = resolve_source_result(16)
        target_config = SimpleConfig.load(str(SILO_8_CONFIG))
        target_config.llm_api_log_enabled = False
        return build_contexts_from_result(source_result, target_config=target_config)
    return build_contexts_from_result(resolve_source_result(count))


def median_or_na(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def format_median(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def measure_role_latencies(
    tuner: LLMTuner,
    contexts: Sequence[ReplayContext],
    role_label: str,
) -> List[float]:
    latencies: List[float] = []
    max_attempts = 5 if role_label == "spec" else 3
    for idx, context in enumerate(contexts, start=1):
        print(
            f"  {role_label} call {idx}/{len(contexts)} "
            f"(iter={context.iteration}, params={len(context.current_params)})",
            flush=True,
        )
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                start = time.perf_counter()
                response = tuner.suggest_parameters(
                    metrics=context.metrics,
                    current_params=context.current_params,
                    iteration=context.iteration,
                    best_reward=context.best_reward,
                    history=context.history,
                    baseline_index=context.baseline_index,
                    aggregation_interval_s=context.aggregation_interval_s,
                )
                duration = time.perf_counter() - start
                if response.parameters is None:
                    raise RuntimeError(f"{role_label} returned no parameter payload")
                latencies.append(duration)
                print(f"    completed in {duration:.2f}s", flush=True)
                break
            except Exception as exc:  # pragma: no cover - live API resilience
                last_error = exc
                if attempt == max_attempts:
                    raise
                print(
                    f"    attempt {attempt}/{max_attempts} failed: {exc}; retrying...",
                    flush=True,
                )
                time.sleep(2.0)
        if last_error and len(latencies) < idx:
            raise last_error
    return latencies


def main() -> None:
    args = parse_args()
    rows: List[Dict[str, Any]] = []

    for count in args.counts:
        contexts = build_contexts_for_count(count)
        if not contexts:
            raise RuntimeError(f"No replay contexts available for {count}_param")

        config = contexts[0].config
        actor_tuner = LLMTuner(config, agent_type="reasoning")
        spec_tuner = LLMTuner(config, agent_type="quick")
        actor_tuner.llm_request_timeout_seconds = 60.0
        spec_tuner.llm_request_timeout_seconds = 20.0

        print(f"Measuring {count} params", flush=True)
        actor_latencies = measure_role_latencies(actor_tuner, contexts, role_label="actor")
        spec_latencies = measure_role_latencies(spec_tuner, contexts, role_label="spec")

        actor_median = median_or_na(actor_latencies)
        spec_median = median_or_na(spec_latencies)
        rows.append(
            {
                "n_params": count,
                "actor_median_s": actor_median,
                "speculator_median_s": spec_median,
                "actor_calls": len(actor_latencies),
                "speculator_calls": len(spec_latencies),
                "actor_latencies_s": actor_latencies,
                "speculator_latencies_s": spec_latencies,
                "context_source": "archived_silo_final_actor" if count != 8 else "archived_silo16_replayed_with_8param_config",
            }
        )
        print(
            f"{count:>2} params  actor_median={actor_median:.2f}s  "
            f"spec_median={spec_median:.2f}s",
            flush=True,
        )

    if args.csv_output:
        out = Path(args.csv_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "n_params",
                    "actor_median_s",
                    "speculator_median_s",
                    "actor_calls",
                    "speculator_calls",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "n_params": row["n_params"],
                        "actor_median_s": format_median(row["actor_median_s"]),
                        "speculator_median_s": format_median(row["speculator_median_s"]),
                        "actor_calls": row["actor_calls"],
                        "speculator_calls": row["speculator_calls"],
                    }
                )
        print(f"Wrote CSV: {out}", flush=True)

    if args.json_output:
        out = Path(args.json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2) + "\n")
        print(f"Wrote JSON: {out}", flush=True)


if __name__ == "__main__":
    main()
