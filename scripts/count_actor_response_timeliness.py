#!/usr/bin/env python3
"""Count Actor (reasoning) response timeliness in dual-loop history JSON files.

Definitions used by this script:
- on-time: Actor response lands in the same iteration/window where it was started.
- postponed: Actor response lands in a later iteration/window.
- unresolved: Actor request is inferred to still be pending at run end.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ResponseRecord:
    response_iteration: int
    start_iteration: int
    delay_windows: int
    duration_s: Optional[float]


@dataclass
class FileSummary:
    file_path: Path
    tuning_mode: str
    window_duration_s: Optional[float]
    max_iterations: int
    actor_only_final: bool
    requests_dispatched: int
    responses_total: int
    on_time: int
    postponed: int
    unresolved: int
    total_postponed_windows: int
    records: List[ResponseRecord]
    anomalies: List[str]


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_reasoning_timing(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    timing = entry.get("tuner_timing")
    if isinstance(timing, dict):
        reasoning = timing.get("reasoning")
        if isinstance(reasoning, dict):
            return reasoning
    legacy = entry.get("reasoning_tuner_timing")
    if isinstance(legacy, dict):
        return legacy
    return None


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def summarize_file(path: Path) -> FileSummary:
    data = _load_json(path)
    config = data.get("config", {}) if isinstance(data.get("config"), dict) else {}
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []

    entries: List[Tuple[int, Dict[str, Any]]] = []
    for idx, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        iteration = _as_int(entry.get("iteration"))
        if iteration is None:
            iteration = idx
        entries.append((iteration, entry))
    entries.sort(key=lambda item: item[0])

    tuning_mode = str(config.get("tuning_mode", "unknown"))
    window_duration_s = _as_float(config.get("window_duration"))
    max_iterations = _as_int(config.get("max_iterations")) or len(entries)
    actor_only_final = bool(config.get("dual_loop_actor_only_final", False))

    pending_start_iteration: Optional[int] = None
    requests_dispatched = 0
    responses_total = 0
    on_time = 0
    postponed = 0
    total_postponed_windows = 0
    anomalies: List[str] = []
    records: List[ResponseRecord] = []

    for iteration, entry in entries:
        if not actor_only_final and pending_start_iteration is None:
            pending_start_iteration = iteration
            requests_dispatched += 1

        timing = _extract_reasoning_timing(entry)
        if timing is None:
            continue

        responses_total += 1
        start_iteration = _as_int(timing.get("tuner_start_iteration"))
        if start_iteration is None:
            start_iteration = pending_start_iteration if pending_start_iteration is not None else iteration

        if pending_start_iteration is not None and start_iteration != pending_start_iteration:
            anomalies.append(
                f"iteration {iteration}: start_iteration={start_iteration} "
                f"but inferred pending={pending_start_iteration}"
            )
            pending_start_iteration = start_iteration

        delay = max(0, iteration - start_iteration)
        if delay == 0:
            on_time += 1
        else:
            postponed += 1
            total_postponed_windows += delay

        records.append(
            ResponseRecord(
                response_iteration=iteration,
                start_iteration=start_iteration,
                delay_windows=delay,
                duration_s=_as_float(timing.get("tuner_duration")),
            )
        )
        pending_start_iteration = None

    unresolved = 0
    if not actor_only_final and pending_start_iteration is not None:
        unresolved = 1

    return FileSummary(
        file_path=path,
        tuning_mode=tuning_mode,
        window_duration_s=window_duration_s,
        max_iterations=max_iterations,
        actor_only_final=actor_only_final,
        requests_dispatched=requests_dispatched,
        responses_total=responses_total,
        on_time=on_time,
        postponed=postponed,
        unresolved=unresolved,
        total_postponed_windows=total_postponed_windows,
        records=records,
        anomalies=anomalies,
    )


def _print_human(summary: FileSummary, show_details: bool) -> None:
    print(f"file: {summary.file_path}")
    print(
        "config: "
        f"tuning_mode={summary.tuning_mode}, "
        f"window_duration_s={summary.window_duration_s}, "
        f"max_iterations={summary.max_iterations}, "
        f"actor_only_final={summary.actor_only_final}"
    )
    print(
        "actor_counts: "
        f"dispatched={summary.requests_dispatched}, "
        f"responses={summary.responses_total}, "
        f"on_time={summary.on_time}, "
        f"postponed={summary.postponed}, "
        f"unresolved={summary.unresolved}"
    )
    print(f"postponed_windows_total: {summary.total_postponed_windows}")

    if summary.anomalies:
        print("anomalies:")
        for item in summary.anomalies:
            print(f"  - {item}")

    if show_details and summary.records:
        print("responses:")
        for rec in summary.records:
            duration = f"{rec.duration_s:.3f}s" if rec.duration_s is not None else "n/a"
            print(
                f"  - response_iter={rec.response_iteration}, "
                f"start_iter={rec.start_iteration}, "
                f"delay_windows={rec.delay_windows}, "
                f"duration={duration}"
            )


def _print_json(summary: FileSummary) -> None:
    payload = {
        "file": str(summary.file_path),
        "config": {
            "tuning_mode": summary.tuning_mode,
            "window_duration_s": summary.window_duration_s,
            "max_iterations": summary.max_iterations,
            "actor_only_final": summary.actor_only_final,
        },
        "actor_counts": {
            "requests_dispatched": summary.requests_dispatched,
            "responses": summary.responses_total,
            "on_time": summary.on_time,
            "postponed": summary.postponed,
            "unresolved": summary.unresolved,
            "postponed_windows_total": summary.total_postponed_windows,
        },
        "responses": [
            {
                "response_iteration": rec.response_iteration,
                "start_iteration": rec.start_iteration,
                "delay_windows": rec.delay_windows,
                "duration_s": rec.duration_s,
            }
            for rec in summary.records
        ],
        "anomalies": summary.anomalies,
    }
    print(json.dumps(payload, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count Actor on-time vs postponed responses from dual-loop history JSON."
    )
    parser.add_argument(
        "history_file",
        type=Path,
        help="Path to dual_loop_actor_speculator_*.json file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Include per-response details in text output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.history_file.exists():
        raise SystemExit(f"File not found: {args.history_file}")

    summary = summarize_file(args.history_file)
    if args.json:
        _print_json(summary)
    else:
        _print_human(summary, show_details=args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
