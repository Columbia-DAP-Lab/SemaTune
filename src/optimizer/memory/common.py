#!/usr/bin/env python3
"""Shared helpers for the offline agentic-memory pipeline."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..model_versions import (
    MEMORY_EMBEDDING_DIMENSION,
    MEMORY_EMBEDDING_MODEL,
    MEMORY_SUMMARY_MODEL,
)

REDACTION_VERSION = "v1"
REDACTION_SCHEMA_VERSION = "agentic_memory_redacted_v1"
SUMMARY_VERSION = "v1"
SUMMARY_SCHEMA_VERSION = "agentic_memory_summary_v1"

RAW_HISTORY_COLLECTION = "memory_raw_histories"
RUN_SUMMARY_COLLECTION = "memory_run_summaries"

GEMINI_SUMMARY_MODEL = MEMORY_SUMMARY_MODEL
GEMINI_EMBEDDING_MODEL = MEMORY_EMBEDDING_MODEL
GEMINI_EMBEDDING_DIMENSION = MEMORY_EMBEDDING_DIMENSION

QUERY_PRIORITY_EARLY_BASELINE_BONUS = 0.02
WINDOW_GROUP_SIZE = 5
PER_ITERATION_HISTORY_LIMIT = 10

SAFE_CONFIG_KEYS = {
    "tuner_type",
    "parameter_ranges",
    "parameter_types",
    "parameters_to_tune",
    "fixed_parameters",
    "optimization_metric",
    "optimization_goal",
    "max_iterations",
    "post_tuning_windows",
    "window_duration",
    "continuous_apply",
    "tuning_mode",
    "experiment_profile",
    "constraint_metric",
    "constraint_threshold",
    "constraint_direction",
    "constraint_penalty",
    "llm_model_name",
    "llm_secondary_model",
    "llm_actor_model",
    "llm_speculator_model",
    "llm_thinking_level",
    "llm_thinking_budget",
    "llm_temperature",
    "llm_additional_metrics",
    "llm_actor_additional_metrics",
    "llm_speculator_additional_metrics",
    "llm_speculator_hide_primary_metric",
    "llm_speculator_aggregation_interval_s",
    "dual_loop_actor_only_final",
    "dual_loop_force_final_actor_before_stable",
    "llm_explore_until_last_iteration",
    "llm_force_final_freeze_before_stable",
    "llm_measure_default_before_tuning",
    "llm_indirect_history_show_all_metrics",
    "llm_indirect_prompt_style",
    "omit_explicit_pairwise_comparison_instruction",
    "llm_full_metrics_prompt_mode",
    "llm_full_metrics_explicit_signature_compare",
    "llm_additional_metrics_dump_only",
    "llm_minimal_prompt_mode",
    "llm_hide_primary_metric_value",
    "llm_hide_primary_metric",
    "trimming_enabled",
    "trimming_cycles",
    "trimming_model_name",
    "trimming_strategy",
    "trimming_suggest_params",
    "trimming_aggressiveness",
    "use_perf_stat",
    "use_indirect_optimization",
}

SAFE_METRIC_LIST_FIELDS = {
    "llm_additional_metrics",
    "llm_actor_additional_metrics",
    "llm_speculator_additional_metrics",
}

KNOWN_APP_IDENTIFIERS = {
    "masstree",
    "silo",
    "tpcc",
    "xapian",
    "wikipedia",
    "twitter",
    "ycsb",
    "sysbench",
    "sphinx",
    "auctionmark",
    "otmetrics",
    "dcperf",
    "mediawiki",
    "django",
    "mutilate",
    "tailbench",
    "benchbase",
    "specjbb",
    "shore",
    "moses",
}

KNOWN_APP_METRICS = {
    "throughput",
    "goodput",
    "latency",
    "latency_avg",
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "latency_max",
    "latency_min",
    "latency_median",
    "request_count",
    "measured_requests",
    "queries_per_hour",
    "app_name",
    "quick_response_count",
    "all_quick_tuner_timings",
    "intervals_aggregated",
    "p_25_latency",
    "p_75_latency",
    "p_90_latency",
    "p_99_latency",
}

KNOWN_SYSTEM_METRICS = {
    "msec_cpu_clock",
    "cpus_utilized",
    "context_switches",
    "context_switches_per_sec",
    "cpu_migrations",
    "cpu_migrations_per_sec",
    "page_faults",
    "page_faults_per_sec",
    "cycles",
    "ghz",
    "instructions",
    "instructions_per_cycle",
    "branches",
    "branches_per_sec",
    "branch_misses",
    "branch_miss_rate_pct",
    "power_socket0_watts",
    "power_ram_watts",
    "cstate_poll_pct",
    "cstate_c1_pct",
    "cstate_c1e_pct",
    "cstate_c6_pct",
    "cpu_load_cores_pct",
    "cpu_load_socket0_pct",
}

INTERNAL_METRIC_KEYS = {
    "all_quick_tuner_timings",
    "quick_response_count",
}

TIMING_KEYS = {
    "window_start_time",
    "window_end_time",
    "perf_start_time",
    "perf_end_time",
    "system_metrics_start_time",
    "system_metrics_end_time",
    "tuner_call_time",
    "tuner_response_time",
    "parameters_applied_timestamp",
}

APP_METRIC_REPLACEMENTS = {
    "throughput": "application objective",
    "goodput": "application objective",
    "latency_p99": "application objective",
    "latency_p95": "application objective",
    "latency_avg": "application objective",
    "queries_per_hour": "application objective",
}

PATH_RE = re.compile(r"(?P<path>(?:/[\w.\-@+]+)+)")
TIME_LIKE_METRIC_KEYS = {
    "duration_seconds",
    "elapsed_time_ns",
    "time_elapsed_seconds",
}


def stable_json_dumps(value: Any) -> str:
    """Return deterministic JSON for hashing/comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not (
        isinstance(value, float) and (math.isnan(value) or math.isinf(value))
    )


def deep_copy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def safe_mean(values: Iterable[float]) -> Optional[float]:
    numeric = [float(v) for v in values if is_number(v)]
    if not numeric:
        return None
    return sum(numeric) / len(numeric)


def build_model_pair(config: Mapping[str, Any]) -> str:
    actor = str(config.get("llm_actor_model") or config.get("llm_model_name") or "").strip()
    spec = str(config.get("llm_speculator_model") or config.get("llm_secondary_model") or "").strip()
    if actor and spec:
        return f"{actor}|{spec}"
    return actor or spec or "unknown"


def compute_run_id(config: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "config": {
            "optimization_metric": config.get("optimization_metric"),
            "optimization_goal": config.get("optimization_goal"),
            "parameter_ranges": config.get("parameter_ranges"),
            "parameters_to_tune": config.get("parameters_to_tune"),
            "fixed_parameters": config.get("fixed_parameters"),
        },
        "history_shape": [
            {
                "iteration": entry.get("iteration"),
                "parameter_keys": sorted((entry.get("parameters") or {}).keys()),
            }
            for entry in history[:5]
        ],
        "history_len": len(history),
    }
    return sha256_hex(stable_json_dumps(payload))[:24]


def metric_is_application(metric_name: str) -> bool:
    lowered = str(metric_name or "").strip().lower()
    if not lowered:
        return False
    if lowered in KNOWN_APP_METRICS:
        return True
    return lowered.startswith("latency_") or lowered.startswith("p_")


def metric_is_system(metric_name: str) -> bool:
    lowered = str(metric_name or "").strip().lower()
    if not lowered:
        return False
    if lowered in KNOWN_SYSTEM_METRICS:
        return True
    system_markers = (
        "cpu",
        "power",
        "cstate",
        "branch",
        "page_fault",
        "migration",
        "context_switch",
        "instruction",
        "cycle",
        "ghz",
        "ipc",
        "perf",
        "cache",
        "socket",
        "numa",
        "io_",
        "iowait",
        "run_queue",
    )
    return any(marker in lowered for marker in system_markers)


def metric_is_time_like(metric_name: str) -> bool:
    lowered = str(metric_name or "").strip().lower()
    if not lowered:
        return False
    if lowered in TIME_LIKE_METRIC_KEYS or lowered in {key.lower() for key in TIMING_KEYS}:
        return True
    time_markers = ("timestamp", "duration", "elapsed_time", "_start_time", "_end_time")
    return any(marker in lowered for marker in time_markers)


def sanitize_metric_name_list(values: Any, keep_app_metrics: bool) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        metric_name = str(raw)
        if keep_app_metrics or metric_is_system(metric_name):
            out.append(metric_name)
    return out


def sanitize_free_text(text: Optional[str], keep_app_metrics: bool) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    sanitized = PATH_RE.sub("[REDACTED_PATH]", raw)
    for identifier in sorted(KNOWN_APP_IDENTIFIERS, key=len, reverse=True):
        sanitized = re.sub(
            rf"\b{re.escape(identifier)}\b",
            "[REDACTED_WORKLOAD]",
            sanitized,
            flags=re.IGNORECASE,
        )

    if not keep_app_metrics:
        for metric_name, replacement in APP_METRIC_REPLACEMENTS.items():
            sanitized = re.sub(
                rf"\b{re.escape(metric_name)}\b",
                replacement,
                sanitized,
                flags=re.IGNORECASE,
            )

    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def aggregate_numeric_dicts(values: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Average matching numeric leaves across a sequence of dictionaries."""
    aggregate: Dict[str, Any] = {}
    key_set = set()
    for item in values:
        key_set.update(item.keys())

    for key in sorted(key_set):
        per_key_values = [item.get(key) for item in values if key in item]
        if not per_key_values:
            continue
        if all(isinstance(v, Mapping) for v in per_key_values):
            nested = aggregate_numeric_dicts([v for v in per_key_values if isinstance(v, Mapping)])
            if nested:
                aggregate[key] = nested
            continue
        numeric_values = [float(v) for v in per_key_values if is_number(v)]
        if numeric_values:
            aggregate[key] = round(sum(numeric_values) / len(numeric_values), 6)
            continue
        first = per_key_values[0]
        if all(value == first for value in per_key_values):
            aggregate[key] = deep_copy_json(first)
    return aggregate


def flatten_metrics(metrics: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten nested metrics into deterministic dotted keys for text rendering."""
    flattened: Dict[str, Any] = {}
    for key in sorted(metrics.keys()):
        value = metrics[key]
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            flattened.update(flatten_metrics(value, prefix=full_key))
        else:
            flattened[full_key] = value
    return flattened


def format_metric_block(metrics: Mapping[str, Any]) -> str:
    flattened = flatten_metrics(metrics)
    parts: List[str] = []
    for key in sorted(flattened.keys()):
        value = flattened[key]
        if is_number(value):
            parts.append(f"{key}={value:,.4f}")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "none"


def chunked(values: Sequence[Any], chunk_size: int) -> List[List[Any]]:
    return [list(values[i:i + chunk_size]) for i in range(0, len(values), chunk_size)]


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
