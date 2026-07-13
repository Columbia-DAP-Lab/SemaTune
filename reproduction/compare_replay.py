#!/usr/bin/env python3
"""Compare one provider-backed C1-C4 run with a trace-replayed rerun."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import suite


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def response_map(history_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    payload = load(history_path)
    config = payload.get("config") or {}
    final_iteration = int(config.get("max_iterations", 30))
    responses: dict[tuple[int, str], dict[str, Any]] = {}
    for row in payload.get("history") or []:
        row_iteration = int(row.get("iteration", -1))
        timing = row.get("tuner_timing") or {}
        if not isinstance(timing, dict):
            continue
        quick = timing.get("quick")
        if isinstance(quick, dict) and row_iteration >= 1:
            responses[(row_iteration - 1, "quick")] = suite._replay_response(quick, recorded=True)
        reasoning = timing.get("reasoning")
        if isinstance(reasoning, dict):
            start = int(reasoning.get("tuner_start_iteration", row_iteration))
            responses[(max(0, start - 1), "reasoning")] = suite._replay_response(reasoning, recorded=True)
        final = timing.get("reasoning_final_before_stable") or timing.get("reasoning_final")
        if isinstance(final, dict):
            responses[(final_iteration, "reasoning_final")] = suite._replay_response(final, recorded=True)
    return responses


def trace_response_map(trace_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    payload = load(trace_path)
    responses: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in payload.get("history") or []:
        iteration = int(entry.get("iteration", -1))
        for role, response in (entry.get("responses") or {}).items():
            if isinstance(response, dict) and response.get("recorded_response") is True:
                responses[(iteration, str(role))] = response
    return responses


def action_signature(role: str, response: dict[str, Any]) -> tuple[str, str]:
    return role, json.dumps(response.get("parameters") or {}, sort_keys=True, separators=(",", ":"))


def action_audit(
    manifest: dict[str, Any], original: Path | None, replay: Path, run_status: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in manifest["jobs"]:
        config = suite.materialized_config(job)
        if not suite.is_llm_config(config):
            continue
        replay_history = suite.completed_result(replay / "fresh" / "raw" / job["target_results_dir"], job)
        source_history = (
            suite.completed_result(original / "fresh" / "raw" / job["target_results_dir"], job)
            if original is not None
            else None
        )
        if (original is not None and source_history is None) or replay_history is None:
            raise ValueError(f"{job['id']}: baseline or replay history is incomplete")
        status = run_status.get("jobs", {}).get(job["id"], {})
        trace_path = Path(str(status.get("replay_trace", "")))
        if not trace_path.is_file():
            raise ValueError(f"{job['id']}: replay trace was not recorded in run status")
        trace = load(trace_path)
        if trace.get("provider_requests") != 0:
            raise ValueError(f"{job['id']}: trace does not forbid provider requests")
        replay_config = (load(replay_history).get("config") or {})
        if not replay_config.get("llm_replay_file"):
            raise ValueError(f"{job['id']}: replay history does not identify its trace")
        if replay_config.get("llm_api_key") or replay_config.get("openrouter_api_key"):
            raise ValueError(f"{job['id']}: replay history retained an API key")

        expected = response_map(source_history) if source_history is not None else trace_response_map(trace_path)
        observed = response_map(replay_history)
        expected_actions = {key: value for key, value in expected.items() if value.get("parameters")}
        observed_actions = {key: value for key, value in observed.items() if value.get("parameters")}
        exact = {
            key
            for key, value in expected_actions.items()
            if key in observed_actions and value.get("parameters") == observed_actions[key].get("parameters")
        }
        observed_signatures = {
            action_signature(key[1], value) for key, value in observed_actions.items()
        }
        shifted = {
            key
            for key, value in expected_actions.items()
            if key not in exact and action_signature(key[1], value) in observed_signatures
        }
        rows.append(
            {
                "job_id": job["id"],
                "source_recorded_responses": len(expected),
                "source_actions": len(expected_actions),
                "replay_observed_responses": len(observed),
                "replay_actions": len(observed_actions),
                "exact_iteration_actions": len(exact),
                "shifted_actions": len(shifted),
                "unobserved_actions": len(set(expected_actions) - exact - shifted),
                "trace_sha256": trace.get("source_history_sha256"),
            }
        )
    return rows


def claim_rows(original_report: dict[str, Any], replay_report: dict[str, Any]) -> list[dict[str, Any]]:
    original = {row["claim"]: row for row in original_report["fresh_claims"]}
    replay = {row["claim"]: row for row in replay_report["fresh_claims"]}
    rows = []
    for claim in ("C1", "C2", "C3", "C4"):
        live = original[claim]
        repeated = replay[claim]
        rows.append(
            {
                "claim": claim,
                "comparison": repeated["comparison"],
                "live_factor": float(live["fresh_factor"]),
                "live_pct": float(live["fresh_pct"]),
                "replay_factor": float(repeated["fresh_factor"]),
                "replay_pct": float(repeated["fresh_pct"]),
                "delta_pct_points": float(repeated["fresh_pct"]) - float(live["fresh_pct"]),
                "live_observation": live["observation_status"],
                "replay_observation": repeated["observation_status"],
                "same_observation": live["observation_status"] == repeated["observation_status"],
            }
        )
    return rows


def comparison_records(
    original_factors: Path, replay: Path, claims: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in claims:
        records.append(
            {
                "record_type": "claim",
                "claim": row["claim"],
                "workload": "",
                "method": row["comparison"],
                "knob_count": "",
                "phase": "stable",
                "live_factor": row["live_factor"],
                "replay_factor": row["replay_factor"],
                "delta_pct_points": row["delta_pct_points"],
                "same_direction": row["same_observation"],
            }
        )

    live_factors = {
        (row["workload"], row["method"], row["knob_count"], row["phase"]): row
        for row in read_csv(original_factors)
    }
    replay_factors = {
        (row["workload"], row["method"], row["knob_count"], row["phase"]): row
        for row in read_csv(replay / "fresh" / "tables" / "improvement_factors.csv")
    }
    if live_factors.keys() != replay_factors.keys():
        raise ValueError("live and replay improvement-factor tables have different rows")
    for key in sorted(live_factors):
        live_factor = float(live_factors[key]["improvement_factor"])
        replay_factor = float(replay_factors[key]["improvement_factor"])
        records.append(
            {
                "record_type": "workload_phase",
                "claim": "",
                "workload": key[0],
                "method": key[1],
                "knob_count": key[2],
                "phase": key[3],
                "live_factor": live_factor,
                "replay_factor": replay_factor,
                "delta_pct_points": (replay_factor - live_factor) * 100.0,
                "same_direction": (live_factor > 1.0) == (replay_factor > 1.0),
            }
        )
    return records


def write_outputs(replay: Path, report: dict[str, Any], records: list[dict[str, Any]]) -> None:
    (replay / "replay_comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fields = [
        "record_type", "claim", "workload", "method", "knob_count", "phase",
        "live_factor", "replay_factor", "delta_pct_points", "same_direction",
    ]
    with (replay / "replay_comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    lines = [
        "# C1–C4 trace-replay comparison",
        "",
        f"Provider isolation: **{'PASS' if report['provider_isolation'] else 'FAIL'}**. ",
        f"Claim-direction match: **{'PASS' if report['all_claim_observations_match'] else 'DIVERGENT'}**.",
        "",
        "| Claim | Live | Trace replay | Difference | Observation |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report["claims"]:
        lines.append(
            f"| {row['claim']} | {row['live_factor']:.4f}× ({row['live_pct']:+.2f}%) | "
            f"{row['replay_factor']:.4f}× ({row['replay_pct']:+.2f}%) | "
            f"{row['delta_pct_points']:+.2f} pp | {row['replay_observation']} |"
        )
    totals = report["action_totals"]
    direction_totals = report["workload_phase_direction_totals"]
    lines.extend(
        [
            "",
            "Workload/phase direction audit:",
            "",
            f"- Matching directions: {direction_totals['matching_rows']}/{direction_totals['total_rows']}",
            f"- Different directions: {direction_totals['different_rows']}/{direction_totals['total_rows']}",
            "",
            "Replay action audit:",
            "",
            f"- Source actions: {totals['source_actions']}",
            f"- Exact-iteration matches: {totals['exact_iteration_actions']}",
            f"- Shifted matches: {totals['shifted_actions']}",
            f"- Unobserved source actions: {totals['unobserved_actions']}",
            "",
            "The action counts describe actions observed in completed optimizer histories. "
            "Every recorded source response remains in its hashed replay trace; because response delays "
            "are preserved, fresh workload timing can shift an asynchronous response to another window "
            "or supersede it before application.",
            "",
            "Per-job action counts and per-workload phase factors are retained in "
            "`replay_comparison.json` and `replay_comparison.csv`.",
            "",
        ]
    )
    (replay / "replay_comparison.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    baseline = parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument("--original-dir", type=Path)
    baseline.add_argument("--baseline-bundle", type=Path)
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    replay = args.replay_dir.resolve()
    manifest = suite.load_manifest(args.manifest.resolve())
    original: Path | None = args.original_dir.resolve() if args.original_dir else None
    if args.baseline_bundle:
        bundle_root = args.baseline_bundle.resolve()
        bundle_errors = suite.trace_bundle_errors(manifest["jobs"], bundle_root)
        if bundle_errors:
            raise ValueError("invalid committed baseline: " + "; ".join(bundle_errors))
        bundle = suite.load_trace_bundle(bundle_root)
        claim_path = bundle_root / str(bundle["claim_report"]["path"])
        factors_path = bundle_root / str(bundle["improvement_factors"]["path"])
        for path, record in (
            (claim_path, bundle["claim_report"]),
            (factors_path, bundle["improvement_factors"]),
        ):
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
                raise ValueError(f"committed baseline artifact checksum mismatch: {path}")
        baseline_label = str(bundle_root)
    else:
        assert original is not None
        claim_path = original / "claim_report.json"
        factors_path = original / "fresh" / "tables" / "improvement_factors.csv"
        baseline_label = str(original)
    original_report = load(claim_path)
    replay_report = load(replay / "claim_report.json")
    run_status = load(replay / "fresh" / "run_status.json")
    if run_status.get("execution_mode") != "trace-replay" or run_status.get("provider_requests_expected") != 0:
        raise ValueError("replay run status does not enforce provider isolation")

    claims = claim_rows(original_report, replay_report)
    actions = action_audit(manifest, original, replay, run_status)
    totals = {
        key: sum(int(row[key]) for row in actions)
        for key in (
            "source_actions", "replay_actions", "exact_iteration_actions",
            "shifted_actions", "unobserved_actions",
        )
    }
    records = comparison_records(factors_path, replay, claims)
    workload_phase_records = [row for row in records if row["record_type"] == "workload_phase"]
    matching_phase_rows = sum(bool(row["same_direction"]) for row in workload_phase_records)
    report = {
        "schema_version": 1,
        "baseline": baseline_label,
        "baseline_kind": "committed-provider-traces" if args.baseline_bundle else "completed-results",
        "replay_dir": str(replay),
        "provider_isolation": True,
        "all_claim_observations_match": all(row["same_observation"] for row in claims),
        "claims": claims,
        "action_totals": totals,
        "action_jobs": actions,
        "workload_phase_direction_totals": {
            "total_rows": len(workload_phase_records),
            "matching_rows": matching_phase_rows,
            "different_rows": len(workload_phase_records) - matching_phase_rows,
        },
    }
    write_outputs(replay, report, records)
    status = "CONSISTENT" if report["all_claim_observations_match"] else "DIVERGENT"
    print(f"REPLAY_COMPARISON: {status} ({replay / 'replay_comparison.md'})")
    for row in claims:
        print(f"{row['claim']}: live {row['live_pct']:+.2f}% -> replay {row['replay_pct']:+.2f}% ({row['replay_observation']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
