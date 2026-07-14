#!/usr/bin/env python3
"""Extract compact, credential-free replay traces from a completed Functional run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FUNCTIONAL_DIR = ROOT / "functional_example"


METHODS = {
    "sematune_single": "single",
    "sematune_dual": "dual",
    "sematune_system": "dual",
    "sematune_ipc": "dual",
    "sematune_trim": "trim",
    "sematune_trim_ipc": "trim",
    "sematune_trim_cache": "trim",
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scalar(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def response(timing: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": parameters,
        "confidence": 1.0,
        "converged": bool(timing.get("converged")),
        "justification": timing.get("justification") or "Recorded Functional model response.",
        "token_metrics": timing.get("token_metrics"),
    }


def complete_trim_candidate(
    timing: dict[str, Any], row: dict[str, Any], ranges: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    proposed = {name: scalar(value) for name, value in (timing.get("proposed_parameters") or {}).items()}
    notes: list[str] = []
    if set(proposed) != set(ranges):
        recorded = {name: scalar(value) for name, value in (row.get("parameters") or {}).items()}
        proposed = {name: recorded.get(name) for name in ranges}
        notes.append(
            f"iteration {row['iteration']}: the recorded Flash-Lite response adjusted ranges but "
            "omitted a complete candidate; replay carries forward the recorded configuration"
        )
    for name, allowed in ranges.items():
        value = proposed.get(name)
        numeric = len(allowed) == 2 and all(isinstance(item, (int, float)) for item in allowed)
        valid = allowed[0] <= value <= allowed[1] if numeric and isinstance(value, (int, float)) else value in allowed
        if not valid:
            replacement = min(max(value, allowed[0]), allowed[1]) if numeric and isinstance(value, (int, float)) else allowed[1]
            notes.append(
                f"iteration {row['iteration']}: normalized recorded {name}={value!r} to "
                f"{replacement!r}, the nearest replayable configured value"
            )
            proposed[name] = replacement
    return proposed, notes


def extract(method: str, kind: str, history_path: Path, config_path: Path) -> dict[str, Any]:
    payload = load(history_path)
    config = load(config_path)
    entries: dict[int, dict[str, Any]] = {}
    notes: list[str] = []
    ranges = config["parameter_ranges"]
    actions_by_cycle: dict[int, dict[str, Any]] = {}
    for action in (payload.get("trimming") or {}).get("trimming_actions", []):
        cycle = int(action["cycle"])
        new_range = action["new_range"]
        adjustment = {"min": new_range[0], "max": new_range[1]} if all(
            isinstance(item, (int, float)) for item in new_range
        ) else {"values": new_range}
        actions_by_cycle.setdefault(cycle, {})[action["param"]] = adjustment

    for row in payload.get("history", []):
        timing = row.get("tuner_timing") or {}
        if kind == "dual" and timing.get("quick") and timing.get("reasoning"):
            replay_iteration = int(row["iteration"]) - 1
            entries[replay_iteration] = {
                "iteration": replay_iteration,
                "responses": {
                    role: response(timing[role], timing[role].get("proposed_parameters") or {})
                    for role in ("quick", "reasoning")
                },
            }
            final = timing.get("reasoning_final_before_stable")
            if final:
                entries[int(row["iteration"])] = {
                    "iteration": int(row["iteration"]),
                    "responses": {
                        "reasoning_final": response(final, final.get("proposed_parameters") or {})
                    },
                }
        elif kind == "single" and timing.get("target_iteration"):
            replay_iteration = int(timing["target_iteration"]) - 1
            entries[replay_iteration] = {
                "iteration": replay_iteration,
                "responses": {
                    "reasoning": response(timing, timing.get("proposed_parameters") or {})
                },
            }
            final = timing.get("final_freeze_before_stable")
            if final:
                final_iteration = int(row["iteration"])
                entries[final_iteration] = {
                    "iteration": final_iteration,
                    "responses": {
                        "reasoning_final": response(final, final.get("proposed_parameters") or {})
                    },
                }
        elif kind == "trim" and row.get("trimming_phase") and timing.get("target_iteration"):
            replay_iteration = int(timing["target_iteration"]) - 1
            candidate, candidate_notes = complete_trim_candidate(timing, row, ranges)
            notes.extend(candidate_notes)
            recorded = response(timing, candidate)
            recorded["suggested_ranges"] = actions_by_cycle.get(int(row["iteration"]), {})
            recorded["eliminated_params"] = []
            entries[replay_iteration] = {
                "iteration": replay_iteration,
                "responses": {"reasoning": recorded},
            }

    if not entries:
        raise ValueError(f"{history_path}: no replayable responses")
    result = {
        "schema_version": 1,
        "benchmark": "sysbench_oltp_rw",
        "method": method,
        "description": "Recorded Gemini 2.5 Flash-Lite Functional responses; no provider request is made.",
        "provider_requests": 0,
        "source_history": history_path.name,
        "history": [entries[index] for index in sorted(entries)],
    }
    if notes:
        result["replay_normalizations"] = notes
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for method, kind in METHODS.items():
        candidates = sorted((args.results_dir / "raw" / method).glob("*history*.json"))
        if not candidates:
            candidates = sorted((args.results_dir / "raw" / method).glob("*actor_speculator*.json"))
        if len(candidates) != 1:
            raise ValueError(f"{method}: expected exactly one history, found {len(candidates)}")
        trace = extract(
            method,
            kind,
            candidates[0],
            FUNCTIONAL_DIR / f"sysbench_{method}.json",
        )
        output = args.output_dir / f"sysbench_{method}_trace.json"
        output.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
