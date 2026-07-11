#!/usr/bin/env python3
"""Shared pre-convergence history helpers used by active robustness plots.

The original standalone plotting CLI was removed from the curated artifact;
these data-selection primitives remain an active dependency of Plot 4.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


HISTORY_PATTERNS = ("optimization_history_*.json", "dual_loop_*.json")


@dataclass
class WindowSpec:
    start: int
    end: int


def canonical_goal(raw_goal: Optional[str]) -> str:
    goal = (raw_goal or "").strip().lower()
    return "maximize" if goal in {"maximize", "max", "higher_is_better"} else "minimize"


def to_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def iter_history_files(tuner_dir: Path) -> Iterable[Path]:
    for pattern in HISTORY_PATTERNS:
        for path in sorted(tuner_dir.glob(pattern)):
            if path.is_file():
                yield path


def pick_iteration(entry: dict[str, Any], fallback: int) -> int:
    for key in ("iteration", "window_number", "index"):
        try:
            return int(entry[key])
        except (KeyError, TypeError, ValueError):
            continue
    return fallback


def extract_entry_value(entry: dict[str, Any], metric_name: Optional[str]) -> Optional[float]:
    metrics = entry.get("metrics", {}) or {}
    system_metrics = entry.get("system_metrics", {}) or {}
    candidates: list[Any] = [entry.get("raw_metric_value")]
    if metric_name:
        candidates.extend((metrics.get(metric_name), system_metrics.get(metric_name)))
    candidates.append(entry.get("reward"))
    for candidate in candidates:
        parsed = to_float(candidate)
        if parsed is not None:
            return parsed
    return None


def detect_metric_and_goal(history_data: dict[str, Any]) -> tuple[Optional[str], str]:
    config = history_data.get("config", {}) or {}
    metric = config.get("optimization_metric")
    return (str(metric).strip() if metric else None, canonical_goal(config.get("optimization_goal")))


def select_window_values(
    history_data: dict[str, Any], metric_name: Optional[str], window: WindowSpec
) -> list[float]:
    history = history_data.get("history", [])
    if not isinstance(history, list):
        return []
    values: list[float] = []
    for position, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        iteration = pick_iteration(entry, position)
        if not window.start <= iteration <= window.end:
            continue
        value = extract_entry_value(entry, metric_name)
        if value is not None:
            values.append(value)
    return values


def pct_worse(goal: str, fixed_ref: Optional[float], values: Sequence[float]) -> Optional[float]:
    if fixed_ref is None or not values:
        return None
    if goal == "maximize":
        bad = sum(value < fixed_ref for value in values)
    else:
        bad = sum(value > fixed_ref for value in values)
    return (bad / len(values)) * 100.0
