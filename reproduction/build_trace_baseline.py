#!/usr/bin/env python3
"""Build a portable, reviewable C1-C4 trace baseline from a completed provider run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import suite


DEFAULT_MANIFEST = Path(__file__).resolve().parent / "claim_manifest.json"
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-or-[0-9A-Za-z_-]{16,}"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Completed provider-backed result root.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Portable baseline bundle destination.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true", help="Replace an existing generated bundle.")
    args = parser.parse_args()

    source = args.source_dir.resolve()
    output = args.output_dir.resolve()
    manifest_path = args.manifest.resolve()
    manifest = suite.load_manifest(manifest_path)
    source_raw = source / "fresh" / "raw"
    claim_report = source / "claim_report.json"
    factors = source / "fresh" / "tables" / "improvement_factors.csv"
    if not claim_report.is_file() or not factors.is_file():
        parser.error(f"source lacks claim report or improvement factors: {source}")
    source_errors = suite.replay_source_errors(manifest["jobs"], source_raw)
    if source_errors:
        parser.error("provider source is incomplete: " + "; ".join(source_errors))
    if output.exists() and not args.force:
        parser.error(f"output already exists; pass --force to replace it: {output}")
    existing_readme = (output / "README.md").read_bytes() if (output / "README.md").is_file() else None

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    trace_records: dict[str, dict[str, Any]] = {}
    models: set[str] = set()
    try:
        trace_dir = temporary / "traces"
        trace_dir.mkdir()
        for job in manifest["jobs"]:
            config = suite.materialized_config(job)
            if not suite.is_llm_config(config):
                continue
            history = suite.completed_result(source_raw / job["target_results_dir"], job)
            if history is None:
                raise ValueError(f"{job['id']}: complete provider history disappeared")
            provider_config = json.loads(history.read_text(encoding="utf-8")).get("config") or {}
            for field in ("llm_actor_model", "llm_speculator_model", "llm_model_name", "llm_secondary_model"):
                if provider_config.get(field):
                    models.add(str(provider_config[field]))
            trace = suite.build_replay_trace(job["id"], history)
            trace["description"] = (
                "Committed provider-backed C1-C4 response trace; workload measurements are not included "
                "and provider requests are forbidden."
            )
            trace["source_history"] = f"provider-run/{job['id']}/{history.name}"
            serialized = json.dumps(trace)
            if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
                raise ValueError(f"{job['id']}: generated trace contains a credential-like value")
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "__", job["id"])
            relative = Path("traces") / f"{safe_name}.json"
            path = temporary / relative
            write_json(path, trace)
            trace_records[job["id"]] = {
                "path": str(relative),
                "sha256": digest(path),
                "source_history_sha256": trace["source_history_sha256"],
                "recorded_responses": trace["recorded_responses"],
                "recorded_actions": trace["recorded_actions"],
            }

        source_claims = json.loads(claim_report.read_text(encoding="utf-8"))
        portable_claims = {
            "schema_version": source_claims.get("schema_version", 1),
            "baseline_kind": "provider-backed",
            "scope": source_claims.get("scope"),
            "fresh_claims": source_claims.get("fresh_claims"),
            "fresh_parameter_scaling": source_claims.get("fresh_parameter_scaling"),
            "interpretation": source_claims.get("interpretation"),
        }
        write_json(temporary / "claim_report.json", portable_claims)
        (temporary / "tables").mkdir()
        (temporary / "tables" / "improvement_factors.csv").write_text(
            factors.read_text(encoding="utf-8").replace("\r\n", "\n"),
            encoding="utf-8",
        )
        run_status_path = source / "fresh" / "run_status.json"
        run_status = json.loads(run_status_path.read_text(encoding="utf-8")) if run_status_path.is_file() else {}
        bundle = {
            "schema_version": 1,
            "kind": "sematune-provider-trace-baseline",
            "description": (
                "Portable provider-backed response baseline for the scoped C1-C4 workflow. "
                "It contains decisions and response timing, not replay workload measurements."
            ),
            "claims": ["C1", "C2", "C3", "C4"],
            "workloads": manifest["workloads"],
            "job_count": len(manifest["jobs"]),
            "trace_count": len(trace_records),
            "measurement_windows": sum(int(job["completion"]["measurement_windows"]) for job in manifest["jobs"]),
            "provider_models": sorted(models),
            "provider_run_finished_at": run_status.get("finished_at"),
            "claim_report": {
                "path": "claim_report.json",
                "sha256": digest(temporary / "claim_report.json"),
            },
            "improvement_factors": {
                "path": "tables/improvement_factors.csv",
                "sha256": digest(temporary / "tables" / "improvement_factors.csv"),
            },
            "traces": trace_records,
        }
        write_json(temporary / "manifest.json", bundle)
        if existing_readme is not None:
            (temporary / "README.md").write_bytes(existing_readme)
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    print(f"TRACE_BASELINE: PASS ({output})")
    print(f"traces={len(trace_records)} jobs={len(manifest['jobs'])} windows={bundle['measurement_windows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
