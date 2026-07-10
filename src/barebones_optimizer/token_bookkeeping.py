#!/usr/bin/env python3
"""Token bookkeeping helpers for LLM-backed tuning runs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple


def _as_nonnegative_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_token_metrics(token_metrics: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """Normalize token metrics to a consistent raw+billed schema.

    Conventions:
    - ``input_tokens`` is the full request-side input reported by the backend.
    - ``output_tokens`` is the raw model output token count.
    - ``thinking_tokens`` is stored separately when available.
    - ``billable_output_tokens`` treats thinking tokens as output-priced tokens.
    - ``billable_total_tokens`` = input + output + thinking.
    - ``api_total_tokens`` preserves the backend-reported total when available.
    """
    if not isinstance(token_metrics, dict):
        return None

    input_tokens = _as_nonnegative_int(
        token_metrics.get("input_tokens", token_metrics.get("prompt_token_count"))
    )
    output_tokens = _as_nonnegative_int(
        token_metrics.get(
            "output_tokens",
            token_metrics.get("candidates_token_count", token_metrics.get("completion_tokens")),
        )
    )
    explicit_thinking_tokens = token_metrics.get("thinking_tokens", token_metrics.get("thoughts_token_count"))
    thinking_tokens = _as_nonnegative_int(explicit_thinking_tokens)
    api_total_tokens = _as_nonnegative_int(
        token_metrics.get("api_total_tokens", token_metrics.get("total_tokens", token_metrics.get("total_token_count")))
    )
    cached_content_token_count = _as_nonnegative_int(token_metrics.get("cached_content_token_count"))

    # If the backend omitted an explicit total but gave input/output, reconstruct the
    # API-style total as input + raw output (without thinking).
    if api_total_tokens == 0 and (input_tokens > 0 or output_tokens > 0):
        api_total_tokens = input_tokens + output_tokens

    inferred_thinking_tokens_from_api_total = False
    if explicit_thinking_tokens is None and api_total_tokens > (input_tokens + output_tokens):
        thinking_tokens = max(0, api_total_tokens - input_tokens - output_tokens)
        inferred_thinking_tokens_from_api_total = thinking_tokens > 0

    billable_output_tokens = output_tokens + thinking_tokens
    billable_total_tokens = input_tokens + billable_output_tokens

    normalized = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "cached_content_token_count": cached_content_token_count,
        "api_total_tokens": api_total_tokens,
        "billable_output_tokens": billable_output_tokens,
        "billable_total_tokens": billable_total_tokens,
        "thinking_tokens_inferred_from_api_total": int(inferred_thinking_tokens_from_api_total),
    }

    if all(value == 0 for value in normalized.values()):
        return None
    return normalized


def _empty_bucket() -> Dict[str, float]:
    return {
        "request_count": 0,
        "requests_with_inferred_thinking_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cached_content_token_count": 0,
        "api_total_tokens": 0,
        "billable_output_tokens": 0,
        "billable_total_tokens": 0,
    }


def _finalize_bucket(bucket: Dict[str, float]) -> Dict[str, float]:
    finalized = dict(bucket)
    count = int(finalized.get("request_count", 0) or 0)
    if count > 0:
        finalized["avg_input_tokens_per_request"] = finalized["input_tokens"] / count
        finalized["avg_output_tokens_per_request"] = finalized["output_tokens"] / count
        finalized["avg_thinking_tokens_per_request"] = finalized["thinking_tokens"] / count
        finalized["avg_billable_output_tokens_per_request"] = finalized["billable_output_tokens"] / count
        finalized["avg_billable_total_tokens_per_request"] = finalized["billable_total_tokens"] / count
    else:
        finalized["avg_input_tokens_per_request"] = 0.0
        finalized["avg_output_tokens_per_request"] = 0.0
        finalized["avg_thinking_tokens_per_request"] = 0.0
        finalized["avg_billable_output_tokens_per_request"] = 0.0
        finalized["avg_billable_total_tokens_per_request"] = 0.0
    return finalized


def _merge_bucket(bucket: Dict[str, float], token_metrics: Dict[str, int]) -> None:
    bucket["request_count"] += 1
    bucket["requests_with_inferred_thinking_tokens"] += token_metrics.get(
        "thinking_tokens_inferred_from_api_total", 0
    )
    for key in (
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cached_content_token_count",
        "api_total_tokens",
        "billable_output_tokens",
        "billable_total_tokens",
    ):
        bucket[key] += token_metrics.get(key, 0)


def _phase_name_for_entry(entry: Dict[str, Any], max_iterations: int) -> str:
    if entry.get("post_tuning_phase"):
        return "stable"
    iteration = _as_nonnegative_int(entry.get("iteration"))
    if max_iterations > 0 and iteration > max_iterations:
        return "stable"
    return "tuning"


def _extract_call_records(entry: Dict[str, Any]) -> List[Tuple[str, Optional[Dict[str, int]]]]:
    """Return `(role, normalized_token_metrics)` records for one history entry."""
    records: List[Tuple[str, Optional[Dict[str, int]]]] = []
    tuner_timing = entry.get("tuner_timing")

    # Preferred path for modern dual-loop history files.
    if isinstance(tuner_timing, dict):
        has_nested_dual = any(
            key in tuner_timing for key in ("quick", "reasoning", "reasoning_final_before_stable")
        )
        if has_nested_dual:
            for key in ("quick", "reasoning", "reasoning_final_before_stable"):
                block = tuner_timing.get(key)
                if not isinstance(block, dict):
                    continue
                role = str(block.get("tuner_type") or key)
                if key == "reasoning_final_before_stable":
                    role = "actor_reasoning_final_before_stable"
                records.append((role, normalize_token_metrics(block.get("token_metrics"))))
            return [(role, metrics) for role, metrics in records if metrics]

        # Single-loop or legacy flat timing block.
        if "token_metrics" in tuner_timing:
            role = str(tuner_timing.get("tuner_type") or "single")
            metrics = normalize_token_metrics(tuner_timing.get("token_metrics"))
            if metrics:
                return [(role, metrics)]

    # Fallback for legacy quick timing storage.
    metrics = entry.get("metrics") or {}
    all_quick = metrics.get("all_quick_tuner_timings")
    if isinstance(all_quick, dict):
        all_quick = [all_quick]
    if isinstance(all_quick, list) and all_quick:
        for block in all_quick:
            if not isinstance(block, dict):
                continue
            role = str(block.get("tuner_type") or "speculator_quick")
            token_metrics = normalize_token_metrics(block.get("token_metrics"))
            if token_metrics:
                records.append((role, token_metrics))
        if records:
            return records

    # Final fallback for older single-loop history that copied token_metrics to the top level.
    top_level_metrics = normalize_token_metrics(entry.get("token_metrics"))
    if top_level_metrics:
        records.append(("single", top_level_metrics))
    return records


def summarize_token_bookkeeping(history: List[Dict[str, Any]], max_iterations: int) -> Dict[str, Any]:
    """Summarize token usage across a saved optimization history."""
    totals = _empty_bucket()
    by_phase = {"tuning": _empty_bucket(), "stable": _empty_bucket()}
    by_tuner_type: Dict[str, Dict[str, float]] = {}
    by_tuner_type_and_phase: Dict[str, Dict[str, Dict[str, float]]] = {}

    for entry in history:
        if not isinstance(entry, dict):
            continue
        phase = _phase_name_for_entry(entry, max_iterations)
        for role, token_metrics in _extract_call_records(entry):
            if not token_metrics:
                continue
            _merge_bucket(totals, token_metrics)
            _merge_bucket(by_phase[phase], token_metrics)

            role_bucket = by_tuner_type.setdefault(role, _empty_bucket())
            _merge_bucket(role_bucket, token_metrics)

            role_phase_bucket = by_tuner_type_and_phase.setdefault(
                role,
                {"tuning": _empty_bucket(), "stable": _empty_bucket()},
            )
            _merge_bucket(role_phase_bucket[phase], token_metrics)

    return {
        "version": 1,
        "input_tokens_include_full_request_context": True,
        "thinking_tokens_priced_as_output": True,
        "notes": {
            "input_tokens": "Full request-side input reported by the backend, including carried conversation/context.",
            "output_tokens": "Raw model output tokens, excluding thinking tokens.",
            "thinking_tokens": "Reasoning/thought tokens, tracked separately when available.",
            "billable_output_tokens": "Output-priced tokens = raw output tokens + thinking tokens.",
            "billable_total_tokens": "Input-priced tokens + output-priced tokens.",
        },
        "totals": _finalize_bucket(totals),
        "by_phase": {phase: _finalize_bucket(bucket) for phase, bucket in by_phase.items()},
        "by_tuner_type": {role: _finalize_bucket(bucket) for role, bucket in sorted(by_tuner_type.items())},
        "by_tuner_type_and_phase": {
            role: {phase: _finalize_bucket(bucket) for phase, bucket in phase_buckets.items()}
            for role, phase_buckets in sorted(by_tuner_type_and_phase.items())
        },
    }
