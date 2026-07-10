#!/usr/bin/env python3
"""Reusable cost extraction helpers for paper plots.

Refactored from rank_tuners_vs_fixed.py so 5.4/5.6 plot scripts
can compute per-method costs without duplicating logic.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Re-export dataclasses so callers don't need rank_tuners_vs_fixed.
__all__ = [
    "UsageBucket",
    "CostSummary",
    "load_pricing_map",
    "model_cost_usd",
    "collect_tuner_cost",
    "collect_one_completed_history_cost",
    "merge_cost_summaries",
    "efficiency_score",
]

DEFAULT_PRICING_PATH = Path(__file__).with_name("model_pricing.json")


@dataclass
class UsageBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.total_tokens += max(0, int(input_tokens)) + max(0, int(output_tokens))
        self.calls += 1
        self.cost_usd += max(0.0, float(cost_usd))


@dataclass
class CostSummary:
    source: str = "none"
    combined: UsageBucket = field(default_factory=UsageBucket)
    actor: UsageBucket = field(default_factory=UsageBucket)
    speculator: UsageBucket = field(default_factory=UsageBucket)
    single: UsageBucket = field(default_factory=UsageBucket)
    trimming: UsageBucket = field(default_factory=UsageBucket)
    model_breakdown: Dict[str, UsageBucket] = field(default_factory=dict)
    iterations: Set[int] = field(default_factory=set)
    unknown_pricing_models: Set[str] = field(default_factory=set)

    def role_bucket(self, role: str) -> UsageBucket:
        if role == "actor":
            return self.actor
        if role == "speculator":
            return self.speculator
        if role == "trimming":
            return self.trimming
        return self.single


# ---------------------------------------------------------------------------
# Pricing map
# ---------------------------------------------------------------------------

_PRICING_CACHE: Dict[str, Dict[str, Dict[str, float]]] = {}


def load_pricing_map(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Load the checked-in model pricing map and resolve aliases."""
    pricing_path = path or DEFAULT_PRICING_PATH
    cache_key = str(pricing_path)
    if cache_key in _PRICING_CACHE:
        return _PRICING_CACHE[cache_key]

    with pricing_path.open() as f:
        data = json.load(f)

    models: Dict[str, Dict[str, float]] = dict(data.get("models", {}))
    aliases: Dict[str, str] = data.get("aliases", {})
    for alias, canonical in aliases.items():
        if canonical in models and alias not in models:
            models[alias] = models[canonical]

    _PRICING_CACHE[cache_key] = models
    return models


def normalize_model_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    normalized = model_name.strip().lower()
    for prefix in ("models/", "google/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    for family in (
        "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro",
        "gemini-3.1-pro-preview", "gemini-3-pro-preview",
        "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview",
    ):
        if normalized.startswith(family):
            return family
    return normalized


def model_cost_usd(
    model_name: Optional[str],
    input_tokens: int,
    output_tokens: int,
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> Optional[float]:
    """Compute cost in USD for *input_tokens* + *output_tokens*."""
    if pricing is None:
        pricing = load_pricing_map()
    normalized = normalize_model_name(model_name)
    if not normalized:
        return None
    rates = pricing.get(normalized)
    if not rates:
        return None
    return (
        (max(0, input_tokens) / 1_000_000.0) * rates["input_per_million"]
        + (max(0, output_tokens) / 1_000_000.0) * rates["output_per_million"]
    )


# ---------------------------------------------------------------------------
# Agent role helpers
# ---------------------------------------------------------------------------

def normalize_agent_role(role_raw: str) -> str:
    lowered = role_raw.lower().strip()
    if lowered in {"quick", "speculator", "speculator_quick"}:
        return "speculator"
    if lowered in {"reasoning", "actor", "actor_reasoning"}:
        return "actor"
    if lowered == "trimming":
        return "trimming"
    return "single"


def add_usage_record(
    summary: CostSummary,
    role: str,
    model_name: Optional[str],
    input_tokens: int,
    output_tokens: int,
    iteration: Optional[int],
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> None:
    normalized_role = normalize_agent_role(role)
    normalized_model = normalize_model_name(model_name) or "unknown_model"
    cost = model_cost_usd(normalized_model, input_tokens, output_tokens, pricing)
    if cost is None and normalized_model != "unknown_model":
        summary.unknown_pricing_models.add(normalized_model)
        cost = 0.0
    if cost is None:
        cost = 0.0

    role_bucket = summary.role_bucket(normalized_role)
    role_bucket.add(input_tokens, output_tokens, cost)
    summary.combined.add(input_tokens, output_tokens, cost)

    model_bucket = summary.model_breakdown.setdefault(normalized_model, UsageBucket())
    model_bucket.add(input_tokens, output_tokens, cost)

    if iteration is not None:
        summary.iterations.add(iteration)


def merge_cost_summaries(dst: CostSummary, src: CostSummary) -> None:
    for role_name in ("combined", "actor", "speculator", "single", "trimming"):
        dst_bucket = getattr(dst, role_name)
        src_bucket = getattr(src, role_name)
        dst_bucket.input_tokens += src_bucket.input_tokens
        dst_bucket.output_tokens += src_bucket.output_tokens
        dst_bucket.total_tokens += src_bucket.total_tokens
        dst_bucket.calls += src_bucket.calls
        dst_bucket.cost_usd += src_bucket.cost_usd

    for model_name, model_bucket in src.model_breakdown.items():
        dst_b = dst.model_breakdown.setdefault(model_name, UsageBucket())
        dst_b.input_tokens += model_bucket.input_tokens
        dst_b.output_tokens += model_bucket.output_tokens
        dst_b.total_tokens += model_bucket.total_tokens
        dst_b.calls += model_bucket.calls
        dst_b.cost_usd += model_bucket.cost_usd

    dst.iterations |= src.iterations
    dst.unknown_pricing_models |= src.unknown_pricing_models
    if dst.source == "none" and src.source != "none":
        dst.source = src.source
    elif dst.source != src.source and src.source != "none":
        dst.source = "mixed"


# ---------------------------------------------------------------------------
# Token / cost collection from result directories
# ---------------------------------------------------------------------------

import re  # noqa: E402 (deferred for readability)

LLM_API_LOG_GLOB = "llm_api_*.txt"


def _regex_first_int(text: str, patterns: Sequence[str]) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _parse_llm_api_log(log_file: Path) -> Optional[Dict[str, Any]]:
    try:
        text = log_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    log_name = log_file.name.lower()
    if log_name.startswith("llm_api_quick_"):
        role = "speculator"
    elif log_name.startswith("llm_api_reasoning_"):
        role = "actor"
    elif log_name.startswith("llm_api_trimming_"):
        role = "trimming"
    else:
        role = "single"

    iteration = _regex_first_int(text, [r"^\s*Iteration:\s*(-?\d+)\s*$"])
    model_name_match = re.search(r"model_version='([^']+)'", text)
    model_name = model_name_match.group(1) if model_name_match else None
    if model_name is None:
        model_name_match = re.search(r'["\']model["\']\s*:\s*["\']([^"\']+)["\']', text)
        if model_name_match:
            model_name = model_name_match.group(1)

    input_tokens = _regex_first_int(text, [
        r"prompt_token_count=(-?\d+)", r"prompt_tokens=(-?\d+)",
        r'"input_tokens"\s*:\s*(-?\d+)', r'"prompt_tokens"\s*:\s*(-?\d+)',
    ])
    output_tokens = _regex_first_int(text, [
        r"candidates_token_count=(-?\d+)", r"completion_tokens=(-?\d+)",
        r'"output_tokens"\s*:\s*(-?\d+)', r'"completion_tokens"\s*:\s*(-?\d+)',
    ])
    thinking_tokens = _regex_first_int(text, [
        r"thoughts_token_count=(-?\d+)", r"reasoning_tokens=(-?\d+)",
        r'"thoughts_token_count"\s*:\s*(-?\d+)', r'"reasoning_tokens"\s*:\s*(-?\d+)',
    ])
    total_tokens = _regex_first_int(text, [
        r"total_token_count=(-?\d+)", r"total_tokens=(-?\d+)",
        r'"total_token_count"\s*:\s*(-?\d+)', r'"total_tokens"\s*:\s*(-?\d+)',
    ])

    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = max(0, total_tokens - input_tokens)
    elif output_tokens is not None and thinking_tokens is not None:
        output_tokens = max(0, output_tokens) + max(0, thinking_tokens)
    elif (
        output_tokens is not None
        and thinking_tokens is None
        and total_tokens is not None
        and input_tokens is not None
        and total_tokens > (input_tokens + output_tokens)
    ):
        output_tokens = max(0, total_tokens - input_tokens)

    if input_tokens is None or output_tokens is None:
        return None

    return {
        "iteration": iteration, "role": role, "model_name": model_name,
        "input_tokens": max(0, input_tokens), "output_tokens": max(0, output_tokens),
    }


def _parse_token_payload(token_payload: object) -> Optional[Tuple[int, int]]:
    if not isinstance(token_payload, dict):
        return None
    def _coerce_int(*keys: str) -> Optional[int]:
        for key in keys:
            raw = token_payload.get(key)
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return None

    payload_text = json.dumps(token_payload)
    input_tokens = _coerce_int("input_tokens", "prompt_tokens", "prompt_token_count")
    if input_tokens is None:
        input_tokens = _regex_first_int(payload_text, [
            r'"input_tokens"\s*:\s*(-?\d+)', r'"prompt_tokens"\s*:\s*(-?\d+)',
            r'"prompt_token_count"\s*:\s*(-?\d+)',
        ])

    explicit_billable_output_tokens = _coerce_int("billable_output_tokens")
    output_tokens = explicit_billable_output_tokens
    if output_tokens is None:
        output_tokens = _coerce_int("output_tokens", "completion_tokens", "candidates_token_count")
    if output_tokens is None:
        output_tokens = _regex_first_int(payload_text, [
            r'"output_tokens"\s*:\s*(-?\d+)', r'"completion_tokens"\s*:\s*(-?\d+)',
            r'"candidates_token_count"\s*:\s*(-?\d+)',
        ])

    thinking_tokens = _coerce_int("thinking_tokens", "thoughts_token_count", "reasoning_tokens")
    if thinking_tokens is None:
        thinking_tokens = _regex_first_int(payload_text, [
            r'"thinking_tokens"\s*:\s*(-?\d+)', r'"thoughts_token_count"\s*:\s*(-?\d+)',
            r'"reasoning_tokens"\s*:\s*(-?\d+)',
        ])

    total_tokens = _coerce_int("billable_total_tokens", "total_tokens", "total_token_count")
    if total_tokens is None:
        total_tokens = _regex_first_int(payload_text, [
            r'"billable_total_tokens"\s*:\s*(-?\d+)',
            r'"total_tokens"\s*:\s*(-?\d+)', r'"total_token_count"\s*:\s*(-?\d+)',
        ])
    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = max(0, total_tokens - input_tokens)
    elif output_tokens is not None and explicit_billable_output_tokens is None and thinking_tokens is not None:
        output_tokens = max(0, output_tokens) + max(0, thinking_tokens)
    elif (
        output_tokens is not None
        and explicit_billable_output_tokens is None
        and thinking_tokens is None
        and total_tokens is not None
        and input_tokens is not None
        and total_tokens > (input_tokens + output_tokens)
    ):
        # Older Gemini histories sometimes expose a larger total without an explicit
        # thoughts_token_count field. Treat the excess over input+output as
        # output-priced thinking tokens.
        output_tokens = max(0, total_tokens - input_tokens)
    if input_tokens is None or output_tokens is None:
        return None
    return max(0, input_tokens), max(0, output_tokens)


def _metric_in_range(iteration: Optional[int], start: Optional[int], end: Optional[int]) -> bool:
    if iteration is None:
        return start is None and end is None
    if start is not None and iteration < start:
        return False
    if end is not None and iteration > end:
        return False
    return True


def _populate_summary_from_history_data(
    summary: CostSummary,
    data: Dict[str, Any],
    start: Optional[int],
    end: Optional[int],
    pricing: Optional[Dict[str, Dict[str, float]]],
) -> None:
    config = data.get("config", {}) or {}
    actor_model = normalize_model_name(
        config.get("llm_actor_model") or config.get("llm_model_name")
    )
    speculator_model = normalize_model_name(
        config.get("llm_speculator_model") or config.get("llm_secondary_model") or actor_model
    )
    default_model = actor_model or speculator_model

    for pos, entry in enumerate(data.get("history", []) or []):
        if not isinstance(entry, dict):
            continue
        iteration = entry.get("iteration", pos + 1)
        if not _metric_in_range(iteration, start, end):
            continue

        metrics = entry.get("metrics", {}) or {}
        tuner_timing = entry.get("tuner_timing") or {}
        quick_records: List[Dict[str, Any]] = []
        all_quick = metrics.get("all_quick_tuner_timings", [])
        if isinstance(all_quick, dict):
            all_quick = [all_quick]
        if isinstance(all_quick, list):
            for qt in all_quick:
                if not isinstance(qt, dict):
                    continue
                quick_records.append(qt)
                parsed = _parse_token_payload(qt.get("token_metrics"))
                if parsed:
                    add_usage_record(summary, "speculator", speculator_model,
                                     parsed[0], parsed[1], iteration, pricing)

        if isinstance(tuner_timing, dict) and not quick_records:
            quick = tuner_timing.get("quick")
            if isinstance(quick, dict):
                parsed = _parse_token_payload(quick.get("token_metrics"))
                if parsed:
                    add_usage_record(summary, "speculator", speculator_model,
                                     parsed[0], parsed[1], iteration, pricing)

        reasoning_record = None
        if isinstance(tuner_timing, dict):
            candidate = tuner_timing.get("reasoning")
            if isinstance(candidate, dict):
                reasoning_record = candidate
        if reasoning_record is None:
            candidate = entry.get("reasoning_tuner_timing") or entry.get("metrics", {}).get("reasoning_tuner_timing")
            if isinstance(candidate, dict):
                reasoning_record = candidate
        if reasoning_record is not None:
            parsed = _parse_token_payload(reasoning_record.get("token_metrics"))
            if parsed:
                add_usage_record(summary, "actor", actor_model,
                                 parsed[0], parsed[1], iteration, pricing)

        if isinstance(tuner_timing, dict) and not quick_records and reasoning_record is None:
            parsed = _parse_token_payload(tuner_timing.get("token_metrics"))
            if parsed:
                add_usage_record(summary, "single", default_model,
                                 parsed[0], parsed[1], iteration, pricing)

        top_level_tokens = entry.get("token_metrics")
        if top_level_tokens and summary.combined.calls == 0:
            parsed = _parse_token_payload(top_level_tokens)
            if parsed:
                add_usage_record(summary, "single", default_model,
                                 parsed[0], parsed[1], iteration, pricing)


def collect_tuner_cost(
    tuner_dir: Path,
    start: Optional[int] = None,
    end: Optional[int] = None,
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> CostSummary:
    """Collect cost for a single tuner directory (logs first, history fallback)."""
    if pricing is None:
        pricing = load_pricing_map()
    summary = CostSummary(source="none")

    # Try LLM API logs first.
    for log_file in sorted(tuner_dir.rglob(LLM_API_LOG_GLOB)):
        entry = _parse_llm_api_log(log_file)
        if not entry:
            continue
        iteration = entry.get("iteration")
        if not _metric_in_range(iteration if isinstance(iteration, int) else None, start, end):
            continue
        add_usage_record(
            summary, str(entry["role"]), entry.get("model_name"),
            int(entry["input_tokens"]), int(entry["output_tokens"]),
            iteration if isinstance(iteration, int) else None, pricing,
        )
    if summary.combined.calls > 0:
        summary.source = "llm_api_logs"
        return summary

    # Fallback: history JSON files.
    from generate_full_performance_table import (
        history_run_completed_successfully,
        iter_history_files,
    )
    for history_file in iter_history_files(tuner_dir):
        try:
            with history_file.open() as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or not history_run_completed_successfully(data):
            continue
        _populate_summary_from_history_data(summary, data, start, end, pricing)

    if summary.combined.calls > 0:
        summary.source = "history_files"
    return summary


def collect_one_completed_history_cost(
    tuner_dir: Path,
    start: Optional[int] = None,
    end: Optional[int] = None,
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> CostSummary:
    """Collect cost from the first completed history file in a tuner directory.

    This is useful when we want one representative completed run per workload,
    rather than summing costs across all reruns in the directory.
    """
    if pricing is None:
        pricing = load_pricing_map()

    from generate_full_performance_table import (
        history_run_completed_successfully,
        iter_history_files,
        load_json,
    )

    fallback: Optional[CostSummary] = None
    for history_file in iter_history_files(tuner_dir):
        data = load_json(history_file)
        if not isinstance(data, dict) or not history_run_completed_successfully(data):
            continue
        summary = CostSummary(source=f"single_history_file:{history_file.name}")
        _populate_summary_from_history_data(summary, data, start, end, pricing)
        if fallback is None:
            fallback = summary
        if summary.combined.calls > 0:
            return summary
    return fallback or CostSummary(source="none")


# ---------------------------------------------------------------------------
# Efficiency metric
# ---------------------------------------------------------------------------

def efficiency_score(stable_geomean_factor: Optional[float], total_cost_usd: float) -> Optional[float]:
    r"""Cost-normalized performance efficiency.

    η = ln(stable\_geomean\_factor) / total\_cost\_usd

    Returns None when cost is zero (e.g. MLOS) or factor is non-positive.
    Higher is better: more multiplicative improvement per dollar spent.
    """
    if total_cost_usd <= 0.0:
        return None
    if stable_geomean_factor is None or stable_geomean_factor <= 0.0:
        return None
    return math.log(stable_geomean_factor) / total_cost_usd
