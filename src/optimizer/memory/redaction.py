#!/usr/bin/env python3
"""Redact optimization histories into a memory-store-friendly format."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .common import (
    INTERNAL_METRIC_KEYS,
    KNOWN_APP_METRICS,
    RAW_HISTORY_COLLECTION,
    REDACTION_SCHEMA_VERSION,
    REDACTION_VERSION,
    SAFE_CONFIG_KEYS,
    SAFE_METRIC_LIST_FIELDS,
    build_model_pair,
    compute_run_id,
    deep_copy_json,
    format_metric_block,
    is_number,
    metric_is_application,
    metric_is_system,
    metric_is_time_like,
    sanitize_free_text,
    sanitize_metric_name_list,
)


def _sanitize_config(config: Mapping[str, Any], keep_app_metrics: bool) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key in sorted(SAFE_CONFIG_KEYS):
        if key not in config:
            continue
        value = deep_copy_json(config[key])
        if key in SAFE_METRIC_LIST_FIELDS:
            value = sanitize_metric_name_list(value, keep_app_metrics)
        sanitized[key] = value
    return sanitized


def _reward_visible(config: Mapping[str, Any]) -> bool:
    return not bool(
        config.get("use_indirect_optimization")
        or config.get("llm_hide_primary_metric_value")
        or config.get("llm_hide_primary_metric")
    )


def _detect_phase(entry: Mapping[str, Any], max_iterations: int) -> str:
    if entry.get("pre_tuning_default_config"):
        return "pre_tuning_default"
    if entry.get("post_tuning_phase"):
        return "stable"
    iteration = int(entry.get("iteration") or 0)
    if max_iterations and iteration > max_iterations:
        return "stable"
    return "tuning"


def _extract_perf_metrics(
    system_metrics: Mapping[str, Any],
    metrics_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    raw_perf = system_metrics.get("perf_metrics")
    candidate: Dict[str, Any] = {}
    if isinstance(raw_perf, Mapping):
        for key, value in raw_perf.items():
            if metric_is_time_like(key):
                continue
            if metric_is_system(key) and is_number(value):
                candidate[key] = value

    if candidate:
        return candidate

    for key, value in metrics_payload.items():
        if metric_is_time_like(key):
            continue
        if key in INTERNAL_METRIC_KEYS or metric_is_application(key):
            continue
        if metric_is_system(key) and is_number(value):
            candidate[key] = value
    return candidate


def _extract_system_metrics(system_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in sorted(system_metrics.items()):
        if metric_is_time_like(key):
            continue
        if key in {
            "window_start_time",
            "window_end_time",
            "perf_start_time",
            "perf_end_time",
            "system_metrics_start_time",
            "system_metrics_end_time",
            "perf_metrics",
        }:
            continue
        if metric_is_system(key) and (is_number(value) or isinstance(value, (str, bool))):
            out[key] = value
    return out


def _extract_app_metrics(metrics_payload: Mapping[str, Any]) -> Dict[str, Any]:
    app_metrics: Dict[str, Any] = {}
    for key, value in sorted(metrics_payload.items()):
        lowered = str(key).lower()
        if metric_is_time_like(key):
            continue
        if lowered in INTERNAL_METRIC_KEYS:
            continue
        if lowered == "app_name":
            continue
        if metric_is_application(key) or lowered in KNOWN_APP_METRICS:
            if is_number(value) or isinstance(value, (str, bool)):
                app_metrics[key] = value
    return app_metrics


def _collect_reasoning(entry: Mapping[str, Any], keep_app_metrics: bool) -> str:
    reasoning_parts: List[str] = []
    timing = entry.get("tuner_timing")
    if isinstance(timing, Mapping):
        for role_key in ("quick", "reasoning"):
            block = timing.get(role_key)
            if isinstance(block, Mapping):
                text = sanitize_free_text(block.get("justification"), keep_app_metrics)
                if text:
                    role = "Speculator" if role_key == "quick" else "Actor"
                    reasoning_parts.append(f"[{role}] {text}")
        if not reasoning_parts:
            text = sanitize_free_text(timing.get("justification"), keep_app_metrics)
            if text:
                reasoning_parts.append(text)

    llm_reasoning = sanitize_free_text(entry.get("llm_justification"), keep_app_metrics)
    if llm_reasoning:
        reasoning_parts.append(f"[LLM] {llm_reasoning}")

    root_reasoning = sanitize_free_text(entry.get("justification"), keep_app_metrics)
    if root_reasoning and root_reasoning not in reasoning_parts:
        reasoning_parts.append(root_reasoning)

    return " | ".join(part for part in reasoning_parts if part)


def _parameter_delta(previous: Optional[Mapping[str, Any]], current: Mapping[str, Any]) -> Dict[str, Any]:
    if previous is None:
        return {}
    delta: Dict[str, Any] = {}
    keys = sorted(set(previous.keys()) | set(current.keys()))
    for key in keys:
        prev_value = previous.get(key)
        curr_value = current.get(key)
        if prev_value != curr_value:
            delta[key] = {
                "from": deep_copy_json(prev_value),
                "to": deep_copy_json(curr_value),
            }
    return delta


def _build_entry_text(entry: Mapping[str, Any], keep_app_metrics: bool) -> str:
    lines = [
        f"Iteration {entry['iteration']} [{entry['phase']}]",
        f"matches_initial_parameters={entry['matches_initial_parameters']}",
        f"reward_visible={entry['reward_visible']}",
        f"reward={entry['reward']}",
        f"parameters={json.dumps(entry['parameters'], sort_keys=True)}",
    ]
    if entry.get("parameter_delta_from_prev"):
        lines.append(
            "parameter_delta_from_prev="
            + json.dumps(entry["parameter_delta_from_prev"], sort_keys=True)
        )
    lines.append(f"system_metrics={format_metric_block(entry.get('system_metrics', {}))}")
    if entry.get("system_perf_metrics"):
        lines.append(
            f"system_perf_metrics={format_metric_block(entry.get('system_perf_metrics', {}))}"
        )
    if keep_app_metrics and entry.get("app_metrics"):
        lines.append(f"app_metrics={format_metric_block(entry.get('app_metrics', {}))}")
    if entry.get("sanitized_reasoning"):
        lines.append(f"sanitized_reasoning={entry['sanitized_reasoning']}")
    return "\n".join(lines)


def _reward_stats(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rewards = [float(entry["reward"]) for entry in entries if is_number(entry.get("reward"))]
    if not rewards:
        return {}
    return {
        "min": round(min(rewards), 6),
        "max": round(max(rewards), 6),
        "mean": round(sum(rewards) / len(rewards), 6),
    }


def _build_first_history_entry(
    history: Sequence[Mapping[str, Any]],
    keep_app_metrics: bool,
) -> Dict[str, Any]:
    if not history:
        return {
            "iteration": None,
            "parameters": {},
            "reward": None,
            "reward_visible": False,
            "phase": "unknown",
            "system_metrics": {},
            "system_perf_metrics": {},
            "sanitized_reasoning": "",
            "matches_initial_parameters": False,
        }
    first_entry = deep_copy_json(history[0])
    if not keep_app_metrics:
        first_entry.pop("app_metrics", None)
    return first_entry


def _common_metadata(redacted: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": redacted["run_id"],
        "optimization_metric": redacted["objective"]["optimization_metric"],
        "optimization_goal": redacted["objective"]["optimization_goal"],
        "has_app_metrics": bool(redacted["has_app_metrics"]),
        "pre_tuning_default_present": bool(redacted["pre_tuning_default_present"]),
        "model_pair": redacted["model_pair"],
        "redaction_version": redacted["redaction_version"],
    }


def _make_doc(
    redacted: Mapping[str, Any],
    *,
    doc_suffix: str,
    source_kind: str,
    phase: str,
    text: str,
    iteration_start: Optional[int] = None,
    iteration_end: Optional[int] = None,
    matches_initial_parameters: bool = False,
    anchor_window_count: int = 0,
    target_document_id: Optional[str] = None,
    target_collection: Optional[str] = None,
    target_source_kind: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = _common_metadata(redacted)
    metadata.update(
        {
            "source_kind": source_kind,
            "phase": phase,
            "iteration_start": int(iteration_start) if iteration_start is not None else -1,
            "iteration_end": int(iteration_end) if iteration_end is not None else -1,
            "matches_initial_parameters": bool(matches_initial_parameters),
            "anchor_window_count": int(anchor_window_count),
        }
    )
    if target_document_id:
        metadata["target_document_id"] = str(target_document_id)
    if target_collection:
        metadata["target_collection"] = str(target_collection)
    if target_source_kind:
        metadata["target_source_kind"] = str(target_source_kind)
    return {
        "id": f"{redacted['run_id']}::{doc_suffix}",
        "collection": RAW_HISTORY_COLLECTION,
        "metadata": metadata,
        "text": text.strip(),
    }


def _find_best_history_entry(
    normalized_history: Sequence[Mapping[str, Any]],
    best_parameters: Mapping[str, Any],
    optimization_goal: str,
) -> Optional[Mapping[str, Any]]:
    matches = [
        entry
        for entry in normalized_history
        if (entry.get("parameters") or {}) == (best_parameters or {})
        and is_number(entry.get("reward"))
    ]
    if not matches:
        return None
    reverse = str(optimization_goal).lower() == "maximize"
    return sorted(matches, key=lambda entry: float(entry["reward"]), reverse=reverse)[0]


def _build_rag_projection(redacted: Mapping[str, Any]) -> Dict[str, Any]:
    keep_app_metrics = bool(redacted["has_app_metrics"])
    normalized_history = redacted.get("history", [])
    objective = redacted["objective"]
    best_result = redacted["best_result"]
    first_entry = redacted["first_history_entry"]

    full_redacted_text_lines = [
        f"Full redacted run for objective {objective['optimization_goal']} {objective['optimization_metric']}",
        f"model_pair={redacted['model_pair']}",
        f"pre_tuning_default_present={redacted['pre_tuning_default_present']}",
        f"best_reward={best_result['best_reward']}",
        "best_parameters=" + json.dumps(best_result["best_parameters"], sort_keys=True),
        "tuner_config=" + json.dumps(redacted["tuner_config"], sort_keys=True),
        "history_entries:",
    ]
    for entry in normalized_history:
        full_redacted_text_lines.append(_build_entry_text(entry, keep_app_metrics))
    full_redacted_text = "\n".join(full_redacted_text_lines)

    full_redacted_doc = _make_doc(
        redacted,
        doc_suffix="full_redacted",
        source_kind="full_redacted",
        phase="run",
        text=full_redacted_text,
        iteration_start=best_result.get("best_history_iteration"),
        iteration_end=best_result.get("best_history_iteration"),
    )

    first_entry_to_redacted_text = "\n".join(
        [
            f"First history entry anchor for objective {objective['optimization_goal']} {objective['optimization_metric']}",
            _build_entry_text(first_entry, keep_app_metrics),
            "This anchor points to the full redacted run artifact.",
        ]
    )
    first_entry_to_redacted_doc = _make_doc(
        redacted,
        doc_suffix="first_entry_to_redacted",
        source_kind="first_entry_to_redacted",
        phase=first_entry.get("phase") or "unknown",
        text=first_entry_to_redacted_text,
        iteration_start=first_entry.get("iteration"),
        iteration_end=first_entry.get("iteration"),
        matches_initial_parameters=bool(first_entry.get("matches_initial_parameters")),
        anchor_window_count=1,
        target_document_id=full_redacted_doc["id"],
        target_collection=RAW_HISTORY_COLLECTION,
        target_source_kind=full_redacted_doc["metadata"]["source_kind"],
    )

    return {
        "first_entry_to_redacted": first_entry_to_redacted_doc,
        "full_redacted": full_redacted_doc,
    }


def redact_history_data(
    payload: Mapping[str, Any],
    *,
    keep_app_metrics: bool = False,
) -> Dict[str, Any]:
    config = payload.get("config") or {}
    raw_history = payload.get("history") or []
    sanitized_config = _sanitize_config(config, keep_app_metrics)
    reward_visible = _reward_visible(config)
    run_id = compute_run_id(config, raw_history)

    initial_parameters = deep_copy_json((raw_history[0].get("parameters") or {}) if raw_history else {})
    previous_parameters: Optional[Mapping[str, Any]] = None
    normalized_history: List[Dict[str, Any]] = []
    max_iterations = int(config.get("max_iterations") or payload.get("iterations") or 0)

    for raw_entry in raw_history:
        parameters = deep_copy_json(raw_entry.get("parameters") or {})
        metrics_payload = raw_entry.get("metrics") or {}
        system_metrics_payload = raw_entry.get("system_metrics") or {}
        normalized_entry: Dict[str, Any] = {
            "iteration": int(raw_entry.get("iteration") or 0),
            "phase": _detect_phase(raw_entry, max_iterations),
            "parameters": parameters,
            "parameter_delta_from_prev": _parameter_delta(previous_parameters, parameters),
            "reward": raw_entry.get("reward"),
            "reward_visible": reward_visible,
            "matches_initial_parameters": parameters == initial_parameters,
            "system_metrics": _extract_system_metrics(system_metrics_payload),
            "system_perf_metrics": _extract_perf_metrics(system_metrics_payload, metrics_payload),
            "sanitized_reasoning": _collect_reasoning(raw_entry, keep_app_metrics),
        }
        if keep_app_metrics:
            app_metrics = _extract_app_metrics(metrics_payload)
            if app_metrics:
                normalized_entry["app_metrics"] = app_metrics
        normalized_history.append(normalized_entry)
        previous_parameters = parameters

    first_history_entry = _build_first_history_entry(normalized_history, keep_app_metrics)
    objective = {
        "optimization_metric": sanitized_config.get("optimization_metric"),
        "optimization_goal": sanitized_config.get("optimization_goal"),
        "constraint_metric": sanitized_config.get("constraint_metric"),
        "constraint_threshold": sanitized_config.get("constraint_threshold"),
        "constraint_direction": sanitized_config.get("constraint_direction"),
    }

    best_parameters = deep_copy_json(payload.get("best_parameters") or {})
    best_entry = _find_best_history_entry(
        normalized_history,
        best_parameters=best_parameters,
        optimization_goal=str(objective.get("optimization_goal") or "maximize"),
    )

    redacted = {
        "schema_version": REDACTION_SCHEMA_VERSION,
        "redaction_version": REDACTION_VERSION,
        "run_id": run_id,
        "has_app_metrics": bool(keep_app_metrics),
        "model_pair": build_model_pair(sanitized_config),
        "pre_tuning_default_present": any(
            entry.get("phase") == "pre_tuning_default" for entry in normalized_history
        ),
        "objective": objective,
        "tuner_config": sanitized_config,
        "best_result": {
            "best_parameters": best_parameters,
            "best_reward": payload.get("best_reward"),
            "reward_visible": reward_visible,
            "iterations": payload.get("iterations"),
            "best_history_iteration": best_entry.get("iteration") if best_entry else None,
            "best_system_metrics": best_entry.get("system_metrics") if best_entry else {},
            "best_system_perf_metrics": best_entry.get("system_perf_metrics") if best_entry else {},
        },
        "history": normalized_history,
        "first_history_entry": first_history_entry,
    }
    redacted["rag_projection"] = _build_rag_projection(redacted)
    return redacted


def redact_history_file(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    keep_app_metrics: bool = False,
) -> Dict[str, Any]:
    source_path = Path(input_path)
    with source_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    redacted = redact_history_data(payload, keep_app_metrics=keep_app_metrics)

    destination = Path(output_path) if output_path else source_path.with_name(
        f"{source_path.stem}_redacted.json"
    )
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(redacted, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return redacted
