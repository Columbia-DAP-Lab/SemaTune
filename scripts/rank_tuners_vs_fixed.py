#!/usr/bin/env python3
"""
Rank tuners vs fixed baseline using geomean percent improvement.

For each workload (benchmark), this script:
1. Aggregates objective values per tuner from optimization history JSON files.
2. Uses the workload's fixed tuner values as baseline.
3. Computes:
   - Mean improvement vs fixed mean
   - Median improvement vs fixed median

Global ranking:
- For each tuner, compute geomean of per-workload improvement factors.
- Convert geomean factors to percentages.
- Sort by geomean mean improvement (default) or geomean median improvement (--median).

Per-benchmark ranking:
- Same improvement math, but no geomean; one row per tuner for that benchmark.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:
    from tabulate import tabulate as _tabulate
except Exception:  # pragma: no cover - optional dependency
    _tabulate = None


HISTORY_PATTERNS: Tuple[str, ...] = ("optimization_history_*.json", "dual_loop_*.json")
EXCLUDE_WORKLOAD_DIRS = {"logs", "plots", ".git", "__pycache__"}
LLM_API_LOG_GLOB = "llm_api_*.txt"
TUNER_CANONICAL_NAME_MAP: Dict[str, str] = {
    # Merge mode3 and base indirect_all under one label.
    "llm_gemini_2_5_flash_lite_indirect_all_mode3": "llm_gemini_2_5_flash_lite_indirect_all",
}
# Gemini API pricing (USD per 1M tokens), <200k context tier.
# Source: https://ai.google.dev/gemini-api/docs/pricing
GEMINI_LT_200K_PRICING: Dict[str, Dict[str, float]] = {
    "gemini-2.5-flash-lite": {"input_per_million": 0.10, "output_per_million": 0.40},
    "gemini-2.5-flash": {"input_per_million": 0.30, "output_per_million": 2.50},
    "gemini-2.5-pro": {"input_per_million": 1.25, "output_per_million": 10.00},
}


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rank tuners vs fixed baseline across one or more results directories. "
            "By default, each workload uses fixed's optimization_metric/optimization_goal."
        )
    )
    parser.add_argument(
        "results_dirs",
        nargs="+",
        help="One or more results directories (each containing workload/tuner/history files).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="Start iteration/window (inclusive). If omitted, starts at first available entry.",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="End iteration/window (inclusive). If omitted, includes all entries.",
    )
    parser.add_argument(
        "--median",
        action="store_true",
        help="Sort tables by median-based improvement (default: sort by mean-based improvement).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Optional output file path. When set, the rendered tables are also written there.",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        help="Optional JSON output path containing machine-readable ranking data.",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default=None,
        help=(
            "Optional metric override to compare across all tuners/workloads (e.g., latency_p99). "
            "Default is per-workload fixed optimization_metric."
        ),
    )
    parser.add_argument(
        "--tuners",
        type=str,
        default=None,
        help=(
            "Optional comma-separated tuner names to include in ranking/output. "
            "The fixed baseline is still loaded internally for comparisons."
        ),
    )
    parser.add_argument(
        "--include-costs",
        action="store_true",
        help=(
            "Include Gemini token usage + cost accounting using <200k-context pricing. "
            "Per-benchmark rows include total/avg cost and dual-loop actor/speculator split."
        ),
    )
    parser.add_argument(
        "--stdout-format",
        choices=("pretty", "markdown"),
        default="pretty",
        help="Console output format (default: pretty aligned tables).",
    )
    return parser.parse_args()


def parse_tuner_filter(raw_tuners: Optional[str]) -> Optional[Set[str]]:
    if raw_tuners is None:
        return None
    names = {canonical_tuner_name(part.strip()) for part in raw_tuners.split(",") if part.strip()}
    if not names:
        raise SystemExit("Error: --tuners was provided but no valid tuner names were parsed.")
    return names


def canonical_tuner_name(name: str) -> str:
    return TUNER_CANONICAL_NAME_MAP.get(name, name)


def to_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def pick_iteration(entry: dict, fallback_pos: int) -> int:
    """
    Resolve iteration/window index for one history entry.

    Priority:
    1. entry.iteration
    2. entry.window_number
    3. entry.index
    4. fallback_pos (0-based entry position)
    """
    for key in ("iteration", "window_number", "index"):
        if key in entry:
            try:
                return int(entry[key])
            except (TypeError, ValueError):
                pass
    return fallback_pos


def extract_objective_values(
    history_data: dict, start: Optional[int], end: Optional[int], forced_metric: Optional[str] = None
) -> Tuple[List[float], Optional[str], Optional[str], Optional[str]]:
    """
    Return objective values and metadata from one history JSON:
      (values, optimization_metric, optimization_goal, tuner_type)
    """
    config = history_data.get("config", {}) or {}
    metric = forced_metric or config.get("optimization_metric")
    goal = str(config.get("optimization_goal", "")).strip().lower() or None
    tuner_type = str(config.get("tuner_type", "")).strip().lower() or None

    history = history_data.get("history", [])
    if not isinstance(history, list):
        return [], metric, goal, tuner_type

    values: List[float] = []
    for pos, entry in enumerate(history):
        if not isinstance(entry, dict):
            continue

        iteration = pick_iteration(entry, pos)
        if start is not None and iteration < start:
            continue
        if end is not None and iteration > end:
            continue

        metrics = entry.get("metrics", {}) or {}
        system_metrics = entry.get("system_metrics", {}) or {}

        if forced_metric:
            # In forced-metric mode, compare a single metric consistently across all tuners.
            value = to_float(metrics.get(metric))
            if value is None:
                value = to_float(system_metrics.get(metric))
        else:
            raw_val = entry.get("raw_metric_value")
            value = to_float(raw_val)

            if value is None and metric:
                value = to_float(metrics.get(metric))
                if value is None:
                    value = to_float(system_metrics.get(metric))

            # Last fallback for older history formats.
            if value is None:
                value = to_float(entry.get("reward"))

        if value is not None:
            values.append(value)

    return values, metric, goal, tuner_type


def discover_history_files(tuner_dir: Path) -> List[Path]:
    files: List[Path] = []
    for pattern in HISTORY_PATTERNS:
        files.extend(tuner_dir.rglob(pattern))
    # Deduplicate and keep stable ordering.
    return sorted(set(files))


def goal_from_metric(metric: Optional[str]) -> str:
    if not metric:
        return "maximize"
    name = metric.lower()
    minimize_hints = ("latency", "power", "watt", "energy", "error", "time", "duration")
    return "minimize" if any(h in name for h in minimize_hints) else "maximize"


def resolve_workload_goal(goal_counts: Counter, metric: Optional[str]) -> str:
    if goal_counts:
        return goal_counts.most_common(1)[0][0]
    return goal_from_metric(metric)


def resolve_workload_metric(metric_counts: Counter, fixed_metric_counts: Counter) -> Optional[str]:
    if fixed_metric_counts:
        return fixed_metric_counts.most_common(1)[0][0]
    if metric_counts:
        return metric_counts.most_common(1)[0][0]
    return None


def compute_factor(value: float, baseline: float, goal: str) -> Optional[float]:
    """
    Improvement factor:
      minimize: baseline / value
      maximize: value / baseline
    """
    if baseline <= 0 or value <= 0:
        return None
    if goal == "minimize":
        factor = baseline / value
    else:
        factor = value / baseline
    if factor <= 0 or not math.isfinite(factor):
        return None
    return factor


def factor_to_pct(factor: Optional[float]) -> Optional[float]:
    if factor is None:
        return None
    return (factor - 1.0) * 100.0


def geomean(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v > 0 and math.isfinite(v)]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def render_markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def fmt_pct(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:+.2f}%"


def sort_value(value: Optional[float]) -> float:
    if value is None or not math.isfinite(value):
        return -float("inf")
    return value


def render_plain_aligned_table(headers: List[str], rows: List[List[str]]) -> str:
    if not headers:
        return ""
    table_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in table_rows) for i in range(len(headers))]

    def fmt_row(values: List[str]) -> str:
        return " | ".join(str(values[i]).ljust(widths[i]) for i in range(len(widths)))

    separator = "-+-".join("-" * w for w in widths)
    lines = [fmt_row(headers), separator]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def render_console_table(headers: List[str], rows: List[List[str]]) -> str:
    if _tabulate is not None:
        try:
            return _tabulate(rows, headers=headers, tablefmt="psql", numalign="right", stralign="left")
        except Exception:
            pass
    return render_plain_aligned_table(headers, rows)


def fmt_usd(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"${value:.4f}"


def maybe_int(value: object) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out < 0:
        return None
    return out


def metric_in_range(iteration: Optional[int], start: Optional[int], end: Optional[int]) -> bool:
    if iteration is None:
        return start is None and end is None
    if start is not None and iteration < start:
        return False
    if end is not None and iteration > end:
        return False
    return True


def normalize_model_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    normalized = model_name.strip().lower()
    if normalized.startswith("models/"):
        normalized = normalized.split("/", 1)[1]
    if normalized.startswith("google/"):
        normalized = normalized.split("/", 1)[1]
    if normalized.startswith("gemini-2.5-flash-lite"):
        return "gemini-2.5-flash-lite"
    if normalized.startswith("gemini-2.5-flash"):
        return "gemini-2.5-flash"
    if normalized.startswith("gemini-2.5-pro"):
        return "gemini-2.5-pro"
    return normalized


def gemini_cost_usd(model_name: Optional[str], input_tokens: int, output_tokens: int) -> Optional[float]:
    normalized = normalize_model_name(model_name)
    if not normalized:
        return None
    rates = GEMINI_LT_200K_PRICING.get(normalized)
    if not rates:
        return None
    return (
        (max(0, input_tokens) / 1_000_000.0) * rates["input_per_million"]
        + (max(0, output_tokens) / 1_000_000.0) * rates["output_per_million"]
    )


def usage_bucket_to_dict(bucket: UsageBucket) -> Dict[str, float]:
    return {
        "input_tokens": bucket.input_tokens,
        "output_tokens": bucket.output_tokens,
        "total_tokens": bucket.total_tokens,
        "calls": bucket.calls,
        "cost_usd": bucket.cost_usd,
    }


def cost_summary_to_dict(summary: CostSummary) -> Dict[str, Any]:
    return {
        "source": summary.source,
        "combined": usage_bucket_to_dict(summary.combined),
        "actor": usage_bucket_to_dict(summary.actor),
        "speculator": usage_bucket_to_dict(summary.speculator),
        "single": usage_bucket_to_dict(summary.single),
        "trimming": usage_bucket_to_dict(summary.trimming),
        "models": {k: usage_bucket_to_dict(v) for k, v in sorted(summary.model_breakdown.items())},
        "iterations_observed": sorted(summary.iterations),
        "unknown_pricing_models": sorted(summary.unknown_pricing_models),
    }


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
        dst_bucket = dst.model_breakdown.setdefault(model_name, UsageBucket())
        dst_bucket.input_tokens += model_bucket.input_tokens
        dst_bucket.output_tokens += model_bucket.output_tokens
        dst_bucket.total_tokens += model_bucket.total_tokens
        dst_bucket.calls += model_bucket.calls
        dst_bucket.cost_usd += model_bucket.cost_usd

    dst.iterations |= src.iterations
    dst.unknown_pricing_models |= src.unknown_pricing_models
    if dst.source == "none" and src.source != "none":
        dst.source = src.source
    elif dst.source != src.source and src.source != "none":
        dst.source = "mixed"


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
) -> None:
    normalized_role = normalize_agent_role(role)
    normalized_model = normalize_model_name(model_name) or "unknown_model"
    cost = gemini_cost_usd(normalized_model, input_tokens, output_tokens)
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


def regex_first_int(text: str, patterns: Sequence[str]) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            continue
        value = maybe_int(match.group(1))
        if value is not None:
            return value
    return None


def parse_token_payload(token_payload: object) -> Optional[Tuple[int, int]]:
    if not isinstance(token_payload, dict):
        return None

    payload_text = json.dumps(token_payload)
    input_tokens = regex_first_int(
        payload_text,
        [
            r'"input_tokens"\s*:\s*(-?\d+)',
            r'"prompt_tokens"\s*:\s*(-?\d+)',
            r'"prompt_token_count"\s*:\s*(-?\d+)',
        ],
    )
    output_tokens = regex_first_int(
        payload_text,
        [
            r'"output_tokens"\s*:\s*(-?\d+)',
            r'"completion_tokens"\s*:\s*(-?\d+)',
            r'"candidates_token_count"\s*:\s*(-?\d+)',
        ],
    )
    thinking_tokens = regex_first_int(
        payload_text,
        [
            r'"thinking_tokens"\s*:\s*(-?\d+)',
            r'"thoughts_token_count"\s*:\s*(-?\d+)',
            r'"reasoning_tokens"\s*:\s*(-?\d+)',
        ],
    )
    total_tokens = regex_first_int(
        payload_text,
        [
            r'"total_tokens"\s*:\s*(-?\d+)',
            r'"total_token_count"\s*:\s*(-?\d+)',
        ],
    )

    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = max(0, total_tokens - input_tokens)
    elif output_tokens is not None and thinking_tokens is not None and total_tokens is None:
        output_tokens = max(0, output_tokens) + max(0, thinking_tokens)

    if input_tokens is None or output_tokens is None:
        return None
    return max(0, input_tokens), max(0, output_tokens)


def parse_llm_api_usage_file(log_file: Path) -> Optional[Dict[str, Any]]:
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

    iteration = regex_first_int(text, [r"^\s*Iteration:\s*(-?\d+)\s*$"])
    model_name_match = re.search(r"model_version='([^']+)'", text)
    model_name = model_name_match.group(1) if model_name_match else None
    if model_name is None:
        model_name_match = re.search(r'["\']model["\']\s*:\s*["\']([^"\']+)["\']', text)
        if model_name_match:
            model_name = model_name_match.group(1)

    input_tokens = regex_first_int(
        text,
        [
            r"prompt_token_count=(-?\d+)",
            r"prompt_tokens=(-?\d+)",
            r'"input_tokens"\s*:\s*(-?\d+)',
        ],
    )
    output_tokens = regex_first_int(
        text,
        [
            r"candidates_token_count=(-?\d+)",
            r"completion_tokens=(-?\d+)",
            r'"output_tokens"\s*:\s*(-?\d+)',
        ],
    )
    thinking_tokens = regex_first_int(
        text,
        [
            r"thoughts_token_count=(-?\d+)",
            r"reasoning_tokens=(-?\d+)",
        ],
    )
    total_tokens = regex_first_int(
        text,
        [
            r"total_token_count=(-?\d+)",
            r"total_tokens=(-?\d+)",
        ],
    )

    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = max(0, total_tokens - input_tokens)
    elif output_tokens is not None and thinking_tokens is not None and total_tokens is None:
        output_tokens = max(0, output_tokens) + max(0, thinking_tokens)

    if input_tokens is None or output_tokens is None:
        return None

    return {
        "iteration": iteration,
        "role": role,
        "model_name": model_name,
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
    }


def collect_costs_from_logs(
    tuner_dirs: Set[Path],
    start: Optional[int],
    end: Optional[int],
) -> CostSummary:
    summary = CostSummary(source="none")
    for tuner_dir in sorted(tuner_dirs):
        for log_file in sorted(tuner_dir.rglob(LLM_API_LOG_GLOB)):
            entry = parse_llm_api_usage_file(log_file)
            if not entry:
                continue
            iteration = entry.get("iteration")
            if not metric_in_range(iteration if isinstance(iteration, int) else None, start, end):
                continue
            add_usage_record(
                summary=summary,
                role=str(entry.get("role", "single")),
                model_name=entry.get("model_name"),
                input_tokens=int(entry.get("input_tokens", 0)),
                output_tokens=int(entry.get("output_tokens", 0)),
                iteration=iteration if isinstance(iteration, int) else None,
            )
    if summary.combined.calls > 0:
        summary.source = "llm_api_logs"
    return summary


def collect_costs_from_history_files(
    history_files: Sequence[Path],
    start: Optional[int],
    end: Optional[int],
    warnings: List[str],
) -> CostSummary:
    summary = CostSummary(source="none")
    for history_file in history_files:
        try:
            with history_file.open("r") as f:
                data = json.load(f)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"Failed to load {history_file} for token/cost extraction: {exc}")
            continue

        config = data.get("config", {}) or {}
        actor_model = normalize_model_name(
            config.get("llm_actor_model") or config.get("llm_model_name")
        )
        speculator_model = normalize_model_name(
            config.get("llm_speculator_model") or config.get("llm_secondary_model") or actor_model
        )
        default_model = actor_model or speculator_model

        history = data.get("history", [])
        if not isinstance(history, list):
            continue

        for pos, entry in enumerate(history):
            if not isinstance(entry, dict):
                continue
            iteration = pick_iteration(entry, pos)
            if not metric_in_range(iteration, start, end):
                continue

            found_agent_specific = False

            metrics = entry.get("metrics", {}) or {}
            all_quick = metrics.get("all_quick_tuner_timings", [])
            if isinstance(all_quick, dict):
                all_quick = [all_quick]
            if isinstance(all_quick, list):
                for quick_entry in all_quick:
                    if not isinstance(quick_entry, dict):
                        continue
                    parsed = parse_token_payload(quick_entry.get("token_metrics"))
                    if not parsed:
                        continue
                    found_agent_specific = True
                    add_usage_record(
                        summary=summary,
                        role="speculator",
                        model_name=speculator_model,
                        input_tokens=parsed[0],
                        output_tokens=parsed[1],
                        iteration=iteration,
                    )

            reasoning_candidates: List[object] = []
            reasoning_from_metrics = metrics.get("reasoning_tuner_timing")
            if reasoning_from_metrics is not None:
                reasoning_candidates.append(reasoning_from_metrics)
            entry_reasoning = entry.get("reasoning_tuner_timing")
            if entry_reasoning is not None:
                reasoning_candidates.append(entry_reasoning)
            entry_timing = entry.get("tuner_timing", {}) or {}
            if isinstance(entry_timing, dict):
                reasoning_candidates.append(entry_timing.get("reasoning"))
                reasoning_candidates.append(entry_timing.get("quick"))

            for candidate in reasoning_candidates:
                if not isinstance(candidate, dict):
                    continue
                parsed = parse_token_payload(candidate.get("token_metrics"))
                if not parsed:
                    continue
                candidate_role = "actor"
                tuner_type = str(candidate.get("tuner_type", "")).lower()
                if "quick" in tuner_type or "speculator" in tuner_type:
                    candidate_role = "speculator"
                found_agent_specific = True
                add_usage_record(
                    summary=summary,
                    role=candidate_role,
                    model_name=speculator_model if candidate_role == "speculator" else actor_model,
                    input_tokens=parsed[0],
                    output_tokens=parsed[1],
                    iteration=iteration,
                )

            if found_agent_specific:
                continue

            token_payload = entry.get("token_metrics")
            if token_payload is None and isinstance(entry_timing, dict):
                token_payload = entry_timing.get("token_metrics")
            parsed = parse_token_payload(token_payload)
            if not parsed:
                continue

            add_usage_record(
                summary=summary,
                role="single",
                model_name=default_model,
                input_tokens=parsed[0],
                output_tokens=parsed[1],
                iteration=iteration,
            )

    if summary.combined.calls > 0:
        summary.source = "history_token_metrics"
    return summary


def collect_tuner_cost_summary(
    tuner_dirs: Set[Path],
    history_files: Sequence[Path],
    start: Optional[int],
    end: Optional[int],
    warnings: List[str],
) -> CostSummary:
    from_logs = collect_costs_from_logs(tuner_dirs, start, end)
    if from_logs.combined.calls > 0:
        return from_logs
    return collect_costs_from_history_files(history_files, start, end, warnings)


def main() -> int:
    args = parse_args()
    selected_tuners = parse_tuner_filter(args.tuners)

    if args.start is not None and args.end is not None and args.start > args.end:
        raise SystemExit("Error: --start must be <= --end")

    workload_tuner_files: Dict[str, Dict[str, List[Path]]] = defaultdict(lambda: defaultdict(list))
    workload_tuner_dirs: Dict[str, Dict[str, Set[Path]]] = defaultdict(lambda: defaultdict(set))
    workload_tuner_is_llm: Dict[str, Dict[str, bool]] = defaultdict(lambda: defaultdict(bool))
    workload_tuner_values: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    workload_tuner_iteration_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    workload_goal_counts: Dict[str, Counter] = defaultdict(Counter)
    workload_fixed_goal_counts: Dict[str, Counter] = defaultdict(Counter)
    workload_metric_counts: Dict[str, Counter] = defaultdict(Counter)
    workload_fixed_metric_counts: Dict[str, Counter] = defaultdict(Counter)
    workload_fixed_goal_metric_counts: Dict[str, Dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    warnings: List[str] = []

    found_any_dir = False
    for results_dir_raw in args.results_dirs:
        results_dir = Path(results_dir_raw)
        if not results_dir.is_dir():
            warnings.append(f"Skipping missing directory: {results_dir}")
            continue
        found_any_dir = True

        for workload_dir in sorted(results_dir.iterdir()):
            if not workload_dir.is_dir() or workload_dir.name in EXCLUDE_WORKLOAD_DIRS:
                continue

            workload = workload_dir.name
            for tuner_dir in sorted(workload_dir.iterdir()):
                if not tuner_dir.is_dir():
                    continue

                history_files = discover_history_files(tuner_dir)
                if not history_files:
                    continue

                for history_file in history_files:
                    try:
                        with history_file.open("r") as f:
                            data = json.load(f)
                    except Exception as exc:  # pragma: no cover - defensive
                        warnings.append(f"Failed to load {history_file}: {exc}")
                        continue

                    config = data.get("config", {}) or {}
                    metric = config.get("optimization_metric")
                    goal = str(config.get("optimization_goal", "")).strip().lower() or None
                    tuner_type = str(config.get("tuner_type", "")).strip().lower() or None

                    is_fixed = (tuner_dir.name.lower() == "fixed") or (tuner_type == "fixed")
                    tuner_name = "fixed" if is_fixed else tuner_dir.name
                    if tuner_name != "fixed":
                        tuner_name = canonical_tuner_name(tuner_name)

                    if metric:
                        workload_metric_counts[workload][metric] += 1
                        if is_fixed:
                            workload_fixed_metric_counts[workload][metric] += 1
                    if tuner_type == "llm" or tuner_name.startswith("llm_"):
                        workload_tuner_is_llm[workload][tuner_name] = True

                    if goal in {"minimize", "maximize"}:
                        workload_goal_counts[workload][goal] += 1
                        if is_fixed:
                            workload_fixed_goal_counts[workload][goal] += 1
                            if metric:
                                workload_fixed_goal_metric_counts[workload][metric][goal] += 1

                    if selected_tuners is not None and tuner_name != "fixed" and tuner_name not in selected_tuners:
                        continue

                    workload_tuner_files[workload][tuner_name].append(history_file)
                    workload_tuner_dirs[workload][tuner_name].add(tuner_dir)

    if not found_any_dir:
        raise SystemExit("Error: none of the provided results directories exists.")

    if not workload_tuner_files:
        raise SystemExit("Error: no usable history files found in provided directories.")

    workload_metric_choice: Dict[str, str] = {}
    workload_goal_choice: Dict[str, str] = {}

    for workload in sorted(workload_tuner_files.keys()):
        if args.metric:
            metric_choice: Optional[str] = args.metric
        else:
            metric_choice = resolve_workload_metric(
                workload_metric_counts[workload], workload_fixed_metric_counts[workload]
            )
        if not metric_choice:
            warnings.append(
                f"Skipping workload '{workload}': unable to resolve comparison metric from fixed or workload configs."
            )
            continue

        fixed_goal_counts_for_metric = workload_fixed_goal_metric_counts[workload][metric_choice]
        if len(fixed_goal_counts_for_metric) == 1:
            goal_choice = fixed_goal_counts_for_metric.most_common(1)[0][0]
        elif len(fixed_goal_counts_for_metric) > 1:
            goal_choice = goal_from_metric(metric_choice)
            warnings.append(
                f"Workload '{workload}': fixed has conflicting goals for metric '{metric_choice}' "
                f"({dict(fixed_goal_counts_for_metric)}); using inferred goal '{goal_choice}'."
            )
        else:
            fixed_goal_counts = workload_fixed_goal_counts[workload]
            goal_choice = (
                fixed_goal_counts.most_common(1)[0][0]
                if fixed_goal_counts
                else resolve_workload_goal(workload_goal_counts[workload], metric_choice)
            )

        workload_metric_choice[workload] = metric_choice
        workload_goal_choice[workload] = goal_choice

    # Second pass: extract only the chosen comparison metric for each workload.
    for workload, tuner_files in workload_tuner_files.items():
        metric_choice = workload_metric_choice.get(workload)
        if not metric_choice:
            continue

        for tuner_name, files in tuner_files.items():
            for history_file in files:
                try:
                    with history_file.open("r") as f:
                        data = json.load(f)
                except Exception as exc:  # pragma: no cover - defensive
                    warnings.append(f"Failed to load {history_file}: {exc}")
                    continue

                values, _, _, _ = extract_objective_values(
                    data, args.start, args.end, forced_metric=metric_choice
                )
                if values:
                    workload_tuner_values[workload][tuner_name].extend(values)
                    workload_tuner_iteration_counts[workload][tuner_name] += len(values)

    if not workload_tuner_values:
        raise SystemExit("Error: no usable metric values found in provided directories.")

    if selected_tuners is not None:
        discovered = {
            tuner
            for tuner_map in workload_tuner_values.values()
            for tuner in tuner_map.keys()
            if tuner != "fixed"
        }
        missing = sorted(t for t in selected_tuners if t != "fixed" and t not in discovered)
        for tuner in missing:
            warnings.append(f"Requested tuner not found in input directories: {tuner}")

    workload_tuner_costs: Dict[str, Dict[str, CostSummary]] = defaultdict(dict)
    missing_llm_cost_pairs: List[str] = []
    if args.include_costs:
        for workload, tuner_files in workload_tuner_files.items():
            for tuner_name, files in tuner_files.items():
                if selected_tuners is not None and tuner_name not in selected_tuners:
                    continue
                if not workload_tuner_values.get(workload, {}).get(tuner_name):
                    continue
                tuner_dirs = workload_tuner_dirs.get(workload, {}).get(tuner_name, set())
                workload_tuner_costs[workload][tuner_name] = collect_tuner_cost_summary(
                    tuner_dirs=tuner_dirs,
                    history_files=files,
                    start=args.start,
                    end=args.end,
                    warnings=warnings,
                )
                if workload_tuner_is_llm.get(workload, {}).get(tuner_name, False):
                    if workload_tuner_costs[workload][tuner_name].combined.calls == 0:
                        missing_llm_cost_pairs.append(f"{workload}/{tuner_name}")

        if missing_llm_cost_pairs:
            preview = ", ".join(sorted(missing_llm_cost_pairs)[:10])
            suffix = " ..." if len(missing_llm_cost_pairs) > 10 else ""
            warnings.append(
                "No token usage found for "
                f"{len(missing_llm_cost_pairs)} LLM workload/tuner pair(s) "
                f"(logs missing and no history token_metrics). Reported cost for those rows is $0.00: "
                f"{preview}{suffix}"
            )

    # Per-workload improvements and global factors for geomean.
    benchmark_rows: Dict[str, List[Dict[str, Optional[float]]]] = {}
    benchmark_meta: Dict[str, Tuple[str, str]] = {}
    global_mean_factors: Dict[str, List[float]] = defaultdict(list)
    global_median_factors: Dict[str, List[float]] = defaultdict(list)
    global_workload_coverage_mean: Dict[str, set] = defaultdict(set)
    global_workload_coverage_median: Dict[str, set] = defaultdict(set)

    for workload in sorted(workload_tuner_values.keys()):
        tuner_values = workload_tuner_values[workload]
        fixed_values = tuner_values.get("fixed", [])
        if not fixed_values:
            warnings.append(f"Skipping workload '{workload}': no fixed baseline found.")
            continue

        metric = workload_metric_choice.get(workload)
        if not metric:
            warnings.append(f"Skipping workload '{workload}': no resolved comparison metric.")
            continue
        goal = workload_goal_choice.get(workload, goal_from_metric(metric))
        benchmark_meta[workload] = (metric, goal)

        fixed_mean = sum(fixed_values) / len(fixed_values)
        fixed_values_sorted = sorted(fixed_values)
        fixed_mid = len(fixed_values_sorted) // 2
        if len(fixed_values_sorted) % 2 == 1:
            fixed_median = fixed_values_sorted[fixed_mid]
        else:
            fixed_median = (fixed_values_sorted[fixed_mid - 1] + fixed_values_sorted[fixed_mid]) / 2.0

        if fixed_mean <= 0 or fixed_median <= 0:
            warnings.append(
                f"Skipping workload '{workload}': fixed baseline mean/median must be > 0 (got mean={fixed_mean}, median={fixed_median})."
            )
            continue

        rows: List[Dict[str, Optional[float]]] = []
        for tuner_name in sorted(tuner_values.keys()):
            if selected_tuners is not None and tuner_name not in selected_tuners:
                continue
            values = tuner_values[tuner_name]
            if not values:
                continue

            values_sorted = sorted(values)
            tuner_mean = sum(values) / len(values)
            mid = len(values_sorted) // 2
            if len(values_sorted) % 2 == 1:
                tuner_median = values_sorted[mid]
            else:
                tuner_median = (values_sorted[mid - 1] + values_sorted[mid]) / 2.0

            mean_factor = compute_factor(tuner_mean, fixed_mean, goal)
            median_factor = compute_factor(tuner_median, fixed_median, goal)

            mean_impr_pct = factor_to_pct(mean_factor)
            median_impr_pct = factor_to_pct(median_factor)

            if mean_factor is not None:
                global_mean_factors[tuner_name].append(mean_factor)
                global_workload_coverage_mean[tuner_name].add(workload)
            if median_factor is not None:
                global_median_factors[tuner_name].append(median_factor)
                global_workload_coverage_median[tuner_name].add(workload)

            rows.append(
                {
                    "tuner": tuner_name,
                    "mean_impr_pct": mean_impr_pct,
                    "median_impr_pct": median_impr_pct,
                }
            )

        if not rows:
            warnings.append(f"Skipping workload '{workload}': no selected tuners with valid values.")
            continue

        benchmark_rows[workload] = rows

    if not benchmark_rows:
        raise SystemExit("Error: no workloads with fixed baseline and valid metrics were found.")

    all_tuners = sorted(set(global_mean_factors.keys()) | set(global_median_factors.keys()))
    workloads_for_global = sorted(benchmark_rows.keys())
    n_workloads_global = len(workloads_for_global)
    global_rows: List[Dict[str, Optional[float]]] = []
    for tuner in all_tuners:
        mean_vals = list(global_mean_factors.get(tuner, []))
        median_vals = list(global_median_factors.get(tuner, []))

        mean_missing = max(0, n_workloads_global - len(global_workload_coverage_mean.get(tuner, set())))
        median_missing = max(0, n_workloads_global - len(global_workload_coverage_median.get(tuner, set())))
        if mean_missing > 0:
            mean_vals.extend([1.0] * mean_missing)
        if median_missing > 0:
            median_vals.extend([1.0] * median_missing)

        mean_geo_factor = geomean(mean_vals)
        median_geo_factor = geomean(median_vals)
        global_rows.append(
            {
                "tuner": tuner,
                "mean_geo_pct": factor_to_pct(mean_geo_factor),
                "median_geo_pct": factor_to_pct(median_geo_factor),
            }
        )

    sort_key_name = "median_geo_pct" if args.median else "mean_geo_pct"
    global_rows.sort(
        key=lambda r: (sort_value(r[sort_key_name]), r["tuner"]),
        reverse=True,
    )

    global_cost_summary_by_tuner: Dict[str, CostSummary] = {}
    global_iteration_counts_by_tuner: Dict[str, int] = defaultdict(int)
    if args.include_costs:
        for workload in benchmark_rows.keys():
            for row in benchmark_rows[workload]:
                tuner_name = str(row["tuner"])
                global_iteration_counts_by_tuner[tuner_name] += workload_tuner_iteration_counts.get(
                    workload, {}
                ).get(tuner_name, 0)
                per_workload_summary = workload_tuner_costs.get(workload, {}).get(tuner_name)
                if not per_workload_summary:
                    continue
                aggregate = global_cost_summary_by_tuner.setdefault(tuner_name, CostSummary(source="none"))
                merge_cost_summaries(aggregate, per_workload_summary)

    lines: List[str] = []
    console_lines: List[str] = []
    lines.append("# Global Tuner Ranking vs Fixed")
    lines.append("")
    lines.append(
        f"Sorted by {'Geomean Median Improvement' if args.median else 'Geomean Mean Improvement'}."
    )
    lines.append("")
    console_lines.append("Global Tuner Ranking vs Fixed")
    console_lines.append(
        f"Sorted by {'Geomean Median Improvement' if args.median else 'Geomean Mean Improvement'}."
    )
    console_lines.append("")

    benchmarks_included = sorted(benchmark_rows.keys())
    lines.append("Benchmarks included:")
    for bench in benchmarks_included:
        lines.append(f"- `{bench}`")
    lines.append("")
    console_lines.append(f"Benchmarks included ({len(benchmarks_included)}):")
    for bench in benchmarks_included:
        console_lines.append(f"- {bench}")
    console_lines.append("")

    if args.include_costs:
        lines.append("Cost model: Gemini API pricing, `<200k` context tier.")
        lines.append(
            "Rates: `gemini-2.5-flash-lite` in/out = `$0.10/$0.40` per 1M, "
            "`gemini-2.5-flash` = `$0.30/$2.50`, `gemini-2.5-pro` = `$1.25/$10.00`."
        )
        lines.append("")
        console_lines.append("Cost model: Gemini API pricing, <200k context tier.")
        console_lines.append(
            "Rates: gemini-2.5-flash-lite=$0.10/$0.40, gemini-2.5-flash=$0.30/$2.50, gemini-2.5-pro=$1.25/$10.00 per 1M tokens."
        )
        console_lines.append("")

    global_table_rows: List[List[str]] = []
    global_ranking_json: List[Dict[str, Any]] = []
    for rank, row in enumerate(global_rows, start=1):
        tuner_name = str(row["tuner"])
        summary = global_cost_summary_by_tuner.get(tuner_name, CostSummary(source="none"))
        total_cost = summary.combined.cost_usd
        iter_count = global_iteration_counts_by_tuner.get(tuner_name, 0)
        avg_cost = (total_cost / iter_count) if iter_count > 0 else 0.0

        rendered_row = [
            str(rank),
            tuner_name,
            fmt_pct(row["mean_geo_pct"]),
            fmt_pct(row["median_geo_pct"]),
        ]
        if args.include_costs:
            rendered_row.extend(
                [
                    fmt_usd(total_cost),
                    fmt_usd(avg_cost),
                    fmt_usd(summary.actor.cost_usd),
                    fmt_usd(summary.speculator.cost_usd),
                ]
            )
        global_table_rows.append(rendered_row)

        row_json: Dict[str, Any] = {
            "rank": rank,
            "tuner_name": tuner_name,
            "geomean_mean_improvement_pct": row["mean_geo_pct"],
            "geomean_median_improvement_pct": row["median_geo_pct"],
        }
        if args.include_costs:
            row_json["cost"] = {
                "total_llm_cost_usd": total_cost,
                "avg_llm_cost_per_iteration_usd": avg_cost,
                "actor_cost_usd": summary.actor.cost_usd,
                "speculator_cost_usd": summary.speculator.cost_usd,
                "iterations_count": iter_count,
                "usage": cost_summary_to_dict(summary),
            }
        global_ranking_json.append(row_json)

    global_headers = [
        "Rank #",
        "Tuner name",
        "Geomean Mean Improvement",
        "Geomean Median Improvement",
    ]
    if args.include_costs:
        global_headers.extend(
            [
                "Total LLM Cost (USD)",
                "Avg Cost / Iteration (USD)",
                "Actor Cost (USD)",
                "Speculator Cost (USD)",
            ]
        )
    lines.append(render_markdown_table(global_headers, global_table_rows))
    console_lines.append(render_console_table(global_headers, global_table_rows))
    lines.append("")
    console_lines.append("")

    # Per-workload tables.
    per_benchmark_json: Dict[str, Dict[str, object]] = {}
    for workload in sorted(benchmark_rows.keys()):
        metric, goal = benchmark_meta.get(workload, ("unknown_metric", "unknown_goal"))
        rows = benchmark_rows[workload]

        sort_col = "median_impr_pct" if args.median else "mean_impr_pct"
        rows = sorted(rows, key=lambda r: (sort_value(r[sort_col]), str(r["tuner"])), reverse=True)

        lines.append(f"## {workload}")
        lines.append("")
        lines.append(f"Metric: `{metric}` | Goal: `{goal}`")
        lines.append("")
        console_lines.append(workload)
        console_lines.append(f"Metric: {metric} | Goal: {goal}")
        console_lines.append("")

        per_table_rows: List[List[str]] = []
        per_rows_json: List[Dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            tuner_name = str(row["tuner"])
            summary = workload_tuner_costs.get(workload, {}).get(tuner_name, CostSummary(source="none"))
            iter_count = workload_tuner_iteration_counts.get(workload, {}).get(tuner_name, 0)
            total_cost = summary.combined.cost_usd
            avg_cost = (total_cost / iter_count) if iter_count > 0 else 0.0

            rendered_row = [
                str(rank),
                tuner_name,
                fmt_pct(row["mean_impr_pct"]),
                fmt_pct(row["median_impr_pct"]),
            ]
            if args.include_costs:
                rendered_row.extend(
                    [
                        fmt_usd(total_cost),
                        fmt_usd(avg_cost),
                        fmt_usd(summary.actor.cost_usd),
                        fmt_usd(summary.speculator.cost_usd),
                    ]
                )
            per_table_rows.append(rendered_row)

            row_json: Dict[str, Any] = {
                "rank": rank,
                "tuner_name": tuner_name,
                "mean_improvement_vs_fixed_mean_pct": row["mean_impr_pct"],
                "median_improvement_vs_fixed_median_pct": row["median_impr_pct"],
            }
            if args.include_costs:
                row_json["cost"] = {
                    "total_llm_cost_usd": total_cost,
                    "avg_llm_cost_per_iteration_usd": avg_cost,
                    "actor_cost_usd": summary.actor.cost_usd,
                    "speculator_cost_usd": summary.speculator.cost_usd,
                    "iterations_count": iter_count,
                    "usage": cost_summary_to_dict(summary),
                }
            per_rows_json.append(row_json)

        per_headers = [
            "Rank #",
            "Tuner name",
            "Mean Improvement vs Fixed Mean",
            "Median Improvement vs Fixed Median",
        ]
        if args.include_costs:
            per_headers.extend(
                [
                    "Total LLM Cost (USD)",
                    "Avg Cost / Iteration (USD)",
                    "Actor Cost (USD)",
                    "Speculator Cost (USD)",
                ]
            )
        lines.append(render_markdown_table(per_headers, per_table_rows))
        console_lines.append(render_console_table(per_headers, per_table_rows))
        lines.append("")
        console_lines.append("")
        per_benchmark_json[workload] = {
            "metric": metric,
            "goal": goal,
            "rows": per_rows_json,
        }

    if args.include_costs:
        unknown_models: Set[str] = set()
        for tuner_map in workload_tuner_costs.values():
            for summary in tuner_map.values():
                unknown_models |= summary.unknown_pricing_models
        if unknown_models:
            warnings.append(
                "Missing Gemini price mapping for model(s): "
                + ", ".join(sorted(unknown_models))
                + ". Those calls were counted in tokens but costed as $0.00."
            )

    if warnings:
        lines.append("## Notes")
        lines.append("")
        console_lines.append("Notes")
        for item in warnings:
            lines.append(f"- {item}")
            console_lines.append(f"- {item}")
        lines.append("")
        console_lines.append("")

    report_markdown = "\n".join(lines).rstrip() + "\n"
    report_console = "\n".join(console_lines).rstrip() + "\n"
    print(report_markdown if args.stdout_format == "markdown" else report_console)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_markdown)

    if args.json_output:
        json_payload = {
            "metadata": {
                "results_dirs": args.results_dirs,
                "start": args.start,
                "end": args.end,
                "sort_mode": "median" if args.median else "mean",
                "sort_key": sort_key_name,
                "comparison_metric": args.metric,
                "selected_tuners": sorted(selected_tuners) if selected_tuners is not None else None,
                "tuner_aliases": TUNER_CANONICAL_NAME_MAP,
                "include_costs": args.include_costs,
                "benchmarks_included": benchmarks_included,
                "stdout_format": args.stdout_format,
                "gemini_pricing_lt_200k_usd_per_million_tokens": GEMINI_LT_200K_PRICING if args.include_costs else None,
            },
            "global_ranking": global_ranking_json,
            "per_benchmark": per_benchmark_json,
            "warnings": warnings,
        }
        json_output_path = Path(args.json_output)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(json_payload, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
