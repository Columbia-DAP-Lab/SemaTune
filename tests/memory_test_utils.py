#!/usr/bin/env python3
"""Shared helpers for offline memory pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List


class MemoryFixtureMixin:
    def build_payload(
        self,
        *,
        include_pre_tuning: bool = True,
        unchanged_prefix_len: int = 2,
        total_tuning_iterations: int = 3,
        stable_windows: int = 0,
    ) -> Dict[str, Any]:
        initial_params = {
            "min_granularity_ns": 100000,
            "latency_ns": 100000,
            "napi_busy_poll": {"value": 0, "cores": "0-3"},
        }
        tuned_params = {
            "min_granularity_ns": 200000,
            "latency_ns": 250000,
            "napi_busy_poll": {"value": 200, "cores": "0-3"},
        }

        config = {
            "benchmark": "tailbench",
            "tailbench_app": "masstree",
            "tailbench_root": "/opt/tailbench",
            "results_dir": "results/masstree_hi_p99/llm_dual_app_metrics_final_actor/",
            "llm_api_key": "secret-key",
            "openrouter_api_key": "secret-openrouter",
            "tuner_type": "llm",
            "parameter_ranges": {
                "min_granularity_ns": [100000, 1000000],
                "latency_ns": [100000, 1000000],
                "napi_busy_poll": [0, 500],
            },
            "parameters_to_tune": [
                "min_granularity_ns",
                "latency_ns",
                "napi_busy_poll",
            ],
            "fixed_parameters": {},
            "optimization_metric": "latency_p99",
            "optimization_goal": "minimize",
            "max_iterations": total_tuning_iterations,
            "post_tuning_windows": stable_windows,
            "window_duration": 5,
            "tuning_mode": "in-window",
            "llm_model_name": "gemini-2.5-flash",
            "llm_secondary_model": "gemini-2.5-flash-lite",
            "llm_actor_model": "gemini-2.5-flash",
            "llm_speculator_model": "gemini-2.5-flash-lite",
            "llm_additional_metrics": [
                "throughput",
                "latency_p99",
                "instructions_per_cycle",
                "cycles",
            ],
            "llm_actor_additional_metrics": [
                "throughput",
                "latency_p99",
                "instructions_per_cycle",
                "cycles",
            ],
            "llm_speculator_additional_metrics": [
                "throughput",
                "latency_p99",
                "instructions_per_cycle",
                "cycles",
            ],
            "use_indirect_optimization": False,
            "llm_full_metrics_prompt_mode": True,
            "trimming_enabled": False,
            "use_perf_stat": True,
        }

        history: List[Dict[str, Any]] = []
        if include_pre_tuning:
            history.append(
                self._make_entry(
                    iteration=0,
                    parameters=initial_params,
                    reward=0.950,
                    throughput=2000,
                    latency_p99=0.950,
                    instructions_per_cycle=0.55,
                    cycles=1000,
                    cpu_load=20.0,
                    power=40.0,
                    pre_tuning_default=True,
                )
            )

        for offset in range(1, total_tuning_iterations + 1):
            params = initial_params if offset < unchanged_prefix_len + 1 else tuned_params
            reward = round(0.95 - (offset * 0.08), 3) if params == tuned_params else round(0.95 + (offset * 0.01), 3)
            history.append(
                self._make_entry(
                    iteration=offset,
                    parameters=params,
                    reward=reward,
                    throughput=2000 - offset * 10,
                    latency_p99=reward,
                    instructions_per_cycle=0.55 + offset * 0.03,
                    cycles=1000 + offset * 50,
                    cpu_load=20.0 + offset,
                    power=40.0 + offset,
                    pre_tuning_default=False,
                )
            )

        for stable_offset in range(1, stable_windows + 1):
            stable_iteration = total_tuning_iterations + stable_offset
            history.append(
                self._make_entry(
                    iteration=stable_iteration,
                    parameters=tuned_params,
                    reward=0.60,
                    throughput=2100,
                    latency_p99=0.60,
                    instructions_per_cycle=0.88,
                    cycles=1800,
                    cpu_load=28.0,
                    power=46.0,
                    post_tuning_phase=True,
                )
            )

        return {
            "config": config,
            "best_parameters": tuned_params,
            "best_reward": 0.60,
            "iterations": total_tuning_iterations + stable_windows,
            "total_time": 123.45,
            "history": history,
            "reason": "completed",
            "mode": "actor-speculator",
        }

    def _make_entry(
        self,
        *,
        iteration: int,
        parameters: Dict[str, Any],
        reward: float,
        throughput: float,
        latency_p99: float,
        instructions_per_cycle: float,
        cycles: float,
        cpu_load: float,
        power: float,
        pre_tuning_default: bool = False,
        post_tuning_phase: bool = False,
    ) -> Dict[str, Any]:
        entry = {
            "iteration": iteration,
            "timestamp": 1772795000.0 + iteration,
            "parameters": json.loads(json.dumps(parameters)),
            "metrics": {
                "throughput": throughput,
                "latency_p99": latency_p99,
                "goodput": throughput,
                "duration_seconds": 5.0,
                "elapsed_time_ns": 5000000000,
                "app_name": "masstree",
                "instructions_per_cycle": instructions_per_cycle,
                "cycles": cycles,
            },
            "reward": reward,
            "post_tuning_phase": post_tuning_phase,
            "system_metrics": {
                "window_start_time": 1772795000.0 + iteration,
                "window_end_time": 1772795005.0 + iteration,
                "power_socket0_watts": power,
                "cpu_load_cores_pct": cpu_load,
                "perf_metrics": {
                    "instructions_per_cycle": instructions_per_cycle,
                    "cycles": cycles,
                    "time_elapsed_seconds": 5.0,
                },
            },
            "tuner_timing": {
                "quick": {
                    "justification": (
                        "Masstree throughput improved after lowering latency_p99; "
                        "see /tmp/masstree/results.json"
                    )
                }
            },
        }
        if pre_tuning_default:
            entry["pre_tuning_default_config"] = True
        return entry

    def write_json(self, directory: Path, filename: str, payload: Dict[str, Any]) -> Path:
        path = directory / filename
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        return path


class FakeSummaryBackend:
    model_name = "gemini-2.5-flash-lite"

    def __init__(self) -> None:
        self.prompt = ""

    def generate_summary(self, prompt, response_schema):
        self.prompt = prompt
        return (
            {
                "summary_text": "Initial unchanged system signature looked queue-light and power-stable.",
                "objective_context": "Optimize latency_p99 by finding lower-latency scheduler regions.",
                "promising_parameter_regions": [
                    "Keep min_granularity_ns and latency_ns in the low hundreds of microseconds.",
                ],
                "risky_parameter_regions": [
                    "Leaving the initial region too long without raising napi_busy_poll increased tail latency.",
                ],
                "promising_system_signatures": [
                    "Higher instructions_per_cycle with modest CPU load and flat package power.",
                ],
                "risky_system_signatures": [
                    "Falling IPC with rising CPU load after leaving the unchanged prefix.",
                ],
                "early_cycle_patterns": [
                    "The initial unchanged signature was stable for the first windows before the tuned region diverged.",
                ],
                "best_configuration_takeaways": [
                    "A modest increase in napi_busy_poll plus slightly larger scheduler quanta helped.",
                ],
                "confidence_notes": [
                    "Reward stayed visible, but only a small number of tuned windows were observed.",
                ],
            },
            {"token_usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}},
        )


class FakeEmbeddingProvider:
    def embed_documents(self, texts):
        return [self._vectorize(text) for text in texts]

    def embed_query(self, text):
        return self._vectorize(text)

    def _vectorize(self, text):
        lowered = str(text).lower()
        return [
            float(lowered.count("initial") + lowered.count("unchanged") + lowered.count("early_baseline")),
            float(lowered.count("system_metrics") + lowered.count("cpu_load") + lowered.count("power_socket0")),
            float(
                lowered.count("system_perf_metrics")
                + lowered.count("instructions_per_cycle")
                + lowered.count("cycles")
            ),
            float(lowered.count("best_reward") + lowered.count("best result")),
            float(lowered.count("summary") + lowered.count("takeaways")),
            1.0,
        ]


def sample_repo_history_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "all_results"
        / "results_config_full_param_masstree_hi_p99_20260306_051050_retry"
        / "masstree_hi_p99"
        / "llm_dual_app_metrics_final_actor"
        / "dual_loop_actor_speculator_tailbench_20260306_051948.json"
    )
