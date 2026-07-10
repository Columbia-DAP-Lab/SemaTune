#!/usr/bin/env python3
"""Summarize redacted histories for retrieval-backed memory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from google import genai
from google.genai import types

from .common import (
    GEMINI_SUMMARY_MODEL,
    RUN_SUMMARY_COLLECTION,
    SUMMARY_SCHEMA_VERSION,
    SUMMARY_VERSION,
    format_metric_block,
)


SUMMARY_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary_text": {"type": "string"},
        "objective_context": {"type": "string"},
        "promising_parameter_regions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risky_parameter_regions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "promising_system_signatures": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risky_system_signatures": {
            "type": "array",
            "items": {"type": "string"},
        },
        "early_cycle_patterns": {
            "type": "array",
            "items": {"type": "string"},
        },
        "best_configuration_takeaways": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence_notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "summary_text",
        "objective_context",
        "promising_parameter_regions",
        "risky_parameter_regions",
        "promising_system_signatures",
        "risky_system_signatures",
        "early_cycle_patterns",
        "best_configuration_takeaways",
        "confidence_notes",
    ],
}


class GoogleGenAISummaryBackend:
    """Gemini-backed summary generator for redacted run histories."""

    def __init__(
        self,
        *,
        model_name: str = GEMINI_SUMMARY_MODEL,
        max_attempts: int = 3,
    ) -> None:
        self.model_name = model_name
        self.max_attempts = max(1, int(max_attempts))
        resolved_api_key = os.getenv("GEMINI_API_KEY")
        if not resolved_api_key:
            raise RuntimeError("Gemini summary generation requires GEMINI_API_KEY.")
        self.client = genai.Client(api_key=resolved_api_key)

    def generate_summary(
        self,
        prompt: str,
        response_schema: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        last_error: Optional[Exception] = None
        last_response_text: Optional[str] = None

        for attempt in range(1, self.max_attempts + 1):
            attempt_prompt = prompt
            if attempt > 1:
                attempt_prompt += (
                    "\n\nThe previous response was not valid JSON. "
                    "Retry and return only compact valid JSON matching the schema exactly."
                )

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    (
                        "You summarize redacted Linux tuning histories for later vector retrieval. "
                        "Focus on goal alignment, early unchanged system state, system-metric signatures, "
                        "parameter regions, and confidence caveats. Return JSON only."
                    ),
                    attempt_prompt,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    maxOutputTokens=4096,
                    responseMimeType="application/json",
                    responseSchema=response_schema,
                ),
            )
            try:
                parsed = getattr(response, "parsed", None)
                if parsed is None:
                    text = getattr(response, "text", "") or ""
                    parsed = _parse_json_payload(text)
                usage = getattr(response, "usage_metadata", None)
                metadata = {
                    "model_name": self.model_name,
                    "response_text": getattr(response, "text", None),
                    "attempt_count": attempt,
                    "token_usage": {
                        "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
                        "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
                        "total_tokens": getattr(usage, "total_token_count", None) if usage else None,
                    },
                }
                return _coerce_summary_payload(parsed), metadata
            except Exception as exc:  # pragma: no cover - exercised with retry test
                last_error = exc
                last_response_text = getattr(response, "text", None)

        raise RuntimeError(
            "Gemini summary generation failed after "
            f"{self.max_attempts} attempts: {last_error}. "
            f"Last response preview: {str(last_response_text or '')[:400]}"
        ) from last_error


def _parse_json_payload(raw_text: str) -> Dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("summary backend returned empty response")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    decode_attempts = [text]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if 0 <= first_brace < last_brace:
        decode_attempts.append(text[first_brace : last_brace + 1])

    decoder = json.JSONDecoder()
    last_error: Optional[Exception] = None
    for candidate in decode_attempts:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            start = candidate.find("{")
            if start >= 0:
                try:
                    parsed, _ = decoder.raw_decode(candidate[start:])
                    return parsed
                except json.JSONDecodeError:
                    pass
    raise last_error or ValueError("summary backend returned invalid JSON")


def _coerce_summary_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    coerced: Dict[str, Any] = {}
    for key in SUMMARY_RESPONSE_SCHEMA["required"]:
        value = payload.get(key)
        if isinstance(SUMMARY_RESPONSE_SCHEMA["properties"][key], dict) and SUMMARY_RESPONSE_SCHEMA["properties"][key].get("type") == "array":
            coerced[key] = [str(item).strip() for item in (value or []) if str(item).strip()]
        else:
            coerced[key] = str(value or "").strip()
    return coerced


def build_summary_prompt(redacted: Mapping[str, Any]) -> str:
    objective = redacted["objective"]
    first_entry = redacted["first_history_entry"]
    best_result = redacted["best_result"]
    rag = redacted["rag_projection"]
    full_redacted_doc = rag["full_redacted"]
    prompt = f"""You are summarizing one redacted completed tuning run for future retrieval-backed warm starts.

Objective:
- optimization_metric: {objective['optimization_metric']}
- optimization_goal: {objective['optimization_goal']}
- reward_visible_during_run: {best_result['reward_visible']}

First history entry:
- iteration: {first_entry['iteration']}
- phase: {first_entry['phase']}
- parameters: {json.dumps(first_entry['parameters'], sort_keys=True)}
- system metrics: {format_metric_block(first_entry.get('system_metrics', {}))}
- system perf metrics: {format_metric_block(first_entry.get('system_perf_metrics', {}))}
- app metrics: {format_metric_block(first_entry.get('app_metrics', {}))}
- sanitized reasoning: {first_entry.get('sanitized_reasoning', '')}

Best result:
- best_reward: {best_result['best_reward']}
- best_parameters: {json.dumps(best_result['best_parameters'], sort_keys=True)}
- best_history_iteration: {best_result.get('best_history_iteration')}

Full redacted run body:
{full_redacted_doc['text']}

Return structured JSON that:
- makes matching easy for future runs with the same optimization goal and a similar first observed system signature,
- describes how later good and bad regions diverged from that first observed signature,
- calls out promising and risky parameter regions,
- calls out promising and risky system-metric signatures,
- explains any confidence limits from hidden rewards, noise, or sparse evidence."""
    return prompt


def render_summary_text(summary_payload: Mapping[str, Any]) -> str:
    sections = [
        ("Summary", summary_payload.get("summary_text", "")),
        ("Objective Context", summary_payload.get("objective_context", "")),
        (
            "Promising Parameter Regions",
            "\n".join(f"- {item}" for item in summary_payload.get("promising_parameter_regions", [])),
        ),
        (
            "Risky Parameter Regions",
            "\n".join(f"- {item}" for item in summary_payload.get("risky_parameter_regions", [])),
        ),
        (
            "Promising System Signatures",
            "\n".join(f"- {item}" for item in summary_payload.get("promising_system_signatures", [])),
        ),
        (
            "Risky System Signatures",
            "\n".join(f"- {item}" for item in summary_payload.get("risky_system_signatures", [])),
        ),
        (
            "Early Cycle Patterns",
            "\n".join(f"- {item}" for item in summary_payload.get("early_cycle_patterns", [])),
        ),
        (
            "Best Configuration Takeaways",
            "\n".join(f"- {item}" for item in summary_payload.get("best_configuration_takeaways", [])),
        ),
        (
            "Confidence Notes",
            "\n".join(f"- {item}" for item in summary_payload.get("confidence_notes", [])),
        ),
    ]
    return "\n\n".join(
        f"{title}\n{body}".strip()
        for title, body in sections
        if str(body or "").strip()
    )


def summarize_redacted_data(
    redacted: Mapping[str, Any],
    *,
    backend: Optional[Any] = None,
) -> Dict[str, Any]:
    active_backend = backend or GoogleGenAISummaryBackend()
    prompt = build_summary_prompt(redacted)
    summary_payload, backend_metadata = active_backend.generate_summary(
        prompt, SUMMARY_RESPONSE_SCHEMA
    )
    summary_text = render_summary_text(summary_payload)
    metadata = {
        "run_id": redacted["run_id"],
        "optimization_metric": redacted["objective"]["optimization_metric"],
        "optimization_goal": redacted["objective"]["optimization_goal"],
        "has_app_metrics": bool(redacted["has_app_metrics"]),
        "pre_tuning_default_present": bool(redacted["pre_tuning_default_present"]),
        "model_pair": redacted["model_pair"],
        "redaction_version": redacted["redaction_version"],
        "run_summary_text": summary_text,
        "run_summary_model": getattr(active_backend, "model_name", GEMINI_SUMMARY_MODEL),
        "summary_model_name": getattr(active_backend, "model_name", GEMINI_SUMMARY_MODEL),
        "source_kind": "full_summary",
        "phase": "summary",
        "iteration_start": -1,
        "iteration_end": -1,
        "matches_initial_parameters": False,
        "anchor_window_count": 0,
    }
    full_summary_doc = {
        "id": f"{redacted['run_id']}::full_summary",
        "collection": RUN_SUMMARY_COLLECTION,
        "metadata": metadata,
        "text": summary_text,
    }
    first_entry = redacted["first_history_entry"]
    first_entry_to_summary_doc = {
        "id": f"{redacted['run_id']}::first_entry_to_summary",
        "collection": RUN_SUMMARY_COLLECTION,
        "metadata": {
            **metadata,
            "source_kind": "first_entry_to_summary",
            "phase": first_entry.get("phase") or "unknown",
            "iteration_start": int(first_entry.get("iteration") or -1),
            "iteration_end": int(first_entry.get("iteration") or -1),
            "matches_initial_parameters": bool(first_entry.get("matches_initial_parameters")),
            "anchor_window_count": 1,
            "target_document_id": full_summary_doc["id"],
            "target_collection": RUN_SUMMARY_COLLECTION,
            "target_source_kind": full_summary_doc["metadata"]["source_kind"],
        },
        "text": "\n".join(
            [
                f"First history entry anchor for summary of objective {redacted['objective']['optimization_goal']} {redacted['objective']['optimization_metric']}",
                f"iteration={first_entry['iteration']}",
                f"phase={first_entry['phase']}",
                "parameters=" + json.dumps(first_entry["parameters"], sort_keys=True),
                "system_metrics=" + format_metric_block(first_entry.get("system_metrics", {})),
                "system_perf_metrics=" + format_metric_block(first_entry.get("system_perf_metrics", {})),
                f"sanitized_reasoning={first_entry.get('sanitized_reasoning', '')}",
                "This anchor points to the full summary artifact.",
            ]
        ),
    }
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_version": SUMMARY_VERSION,
        "source_run_id": redacted["run_id"],
        "redaction_version": redacted["redaction_version"],
        "model_name": getattr(active_backend, "model_name", GEMINI_SUMMARY_MODEL),
        "summary": summary_payload,
        "summary_text": summary_text,
        "backend_metadata": backend_metadata,
        "rag_projection": {
            "first_entry_to_summary": first_entry_to_summary_doc,
            "full_summary": full_summary_doc,
        },
    }


def summarize_redacted_history(
    input_path: str | Path,
    *,
    output_json_path: str | Path | None = None,
    output_text_path: str | Path | None = None,
    backend: Optional[Any] = None,
) -> Dict[str, Any]:
    source_path = Path(input_path)
    with source_path.open("r", encoding="utf-8") as handle:
        redacted = json.load(handle)

    summary_result = summarize_redacted_data(redacted, backend=backend)

    json_destination = Path(output_json_path) if output_json_path else source_path.with_name(
        source_path.name.replace("_redacted.json", "_memory_summary.json")
        if source_path.name.endswith("_redacted.json")
        else f"{source_path.stem}_memory_summary.json"
    )
    text_destination = Path(output_text_path) if output_text_path else json_destination.with_suffix(".txt")

    with json_destination.open("w", encoding="utf-8") as handle:
        json.dump(summary_result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with text_destination.open("w", encoding="utf-8") as handle:
        handle.write(summary_result["summary_text"].strip() + "\n")

    return summary_result
