from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPRO = ROOT / "reproduction"


def load_suite_module():
    spec = importlib.util.spec_from_file_location("reproduction_suite", REPRO / "suite.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_covers_all_plots_with_one_rerun_and_reuse() -> None:
    manifest = json.loads((REPRO / "experiment_manifest.json").read_text())
    assert manifest["reruns_per_configuration"] == 1
    assert set(manifest["plots"]) == {str(number) for number in range(1, 8)}
    assert len(manifest["jobs"]) == 273
    assert len(manifest["aliases"]) == 9
    assert len(manifest["plots"]["1"]["jobs"]) == 91
    assert len(manifest["plots"]["6"]["jobs"]) == 21
    assert len(manifest["plots"]["7"]["jobs"]) == 9
    plot1 = set(manifest["plots"]["1"]["jobs"])
    plot4 = set(manifest["plots"]["4"]["jobs"])
    assert plot4 < plot1


def test_configs_are_complete_and_do_not_contain_provider_keys() -> None:
    manifest = json.loads((REPRO / "experiment_manifest.json").read_text())
    for job in manifest["jobs"]:
        config = json.loads((REPRO / job["config"]).read_text())
        assert config["results_dir"].endswith(job["target_results_dir"])
        assert config.get("llm_api_key") in (None, "")
        assert config.get("openrouter_api_key") in (None, "")
        assert "AIza" not in json.dumps(config)
        assert config["max_iterations"] >= 30
        assert config["max_iterations"] + config["post_tuning_windows"] >= 50


def test_dry_run_is_read_only(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            "python3",
            str(REPRO / "suite.py"),
            "run",
            "--dry-run",
            "--plots",
            "6",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "unique one-rerun configurations: 21" in completed.stdout
    assert not output.exists()


def test_aliases_reuse_completed_job_directories(tmp_path: Path) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest()
    raw = tmp_path / "raw"
    jobs = {job["id"]: job for job in manifest["jobs"]}
    selected = {alias["source_job"] for alias in manifest["aliases"]}
    for job_id in selected:
        (raw / jobs[job_id]["target_results_dir"]).mkdir(parents=True)
    suite.materialize_aliases(manifest, raw, selected)
    for alias in manifest["aliases"]:
        target = raw / alias["target_results_dir"]
        assert target.is_symlink()
        assert target.resolve() == (raw / jobs[alias["source_job"]]["target_results_dir"]).resolve()


def test_manifest_validator_accepts_checked_in_sources() -> None:
    suite = load_suite_module()
    assert suite.validate_manifest(suite.load_manifest(), verify_sources=True) == []


def test_scoped_claim_manifest_has_exactly_21_deduplicated_jobs() -> None:
    suite = load_suite_module()
    raw = json.loads((REPRO / "claim_manifest.json").read_text())
    manifest = suite.load_manifest(REPRO / "claim_manifest.json")
    assert len(raw["jobs"]) == 21
    assert len(manifest["jobs"]) == 21
    assert set(manifest["plots"]) == {"1", "2", "3", "4"}
    assert manifest["workloads"] == ["silo_hi_p99", "tpcc_hi_p99", "sysbench_oltp_rw_hi_p99"]
    assert manifest["knob_counts"] == [2, 8, 16, 41]
    assert len({job["id"] for job in manifest["jobs"]}) == 21
    assert len({job["target_results_dir"] for job in manifest["jobs"]}) == 21
    forbidden = ("single", "trimming", "bayesian", "dqn", "qlearning", "memory")
    assert not any(token in job["id"] for job in manifest["jobs"] for token in forbidden)
    assert suite.validate_manifest(manifest, verify_sources=True) == []


def test_scoped_dry_run_reports_expected_windows_and_reuse(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            "python3",
            str(REPRO / "suite.py"),
            "--manifest",
            str(REPRO / "claim_manifest.json"),
            "run",
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "unique one-rerun configurations: 21" in completed.stdout
    assert "LLM configurations: 15" in completed.stdout
    assert "total benchmark windows: 1050" in completed.stdout
    assert "claim 4: 15 configs" in completed.stdout
    assert not output.exists()


def test_optional_full_claim_dry_run_uses_canonical_plot_dependencies() -> None:
    completed = subprocess.run(
        [str(REPRO / "reproduce_claims.sh"), "--dry-run", "--full"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "unique one-rerun configurations: 232" in completed.stdout
    assert "total benchmark windows: 13430" in completed.stdout
    assert "plots: 1,2,5" in completed.stdout
    assert "plot 3:" not in completed.stdout


def complete_history(*, post_windows: int = 20, dual: bool = False) -> dict:
    tuning = 50 - post_windows
    rows = [
        {
            "iteration": iteration,
            "parameters": {"latency_ns": iteration},
            "metrics": {"latency_p99": float(100 - iteration)},
            "reward": float(100 - iteration),
            "post_tuning_phase": bool(post_windows and iteration > tuning),
        }
        for iteration in range(1, 51)
    ]
    data = {"reason": "completed", "history": rows}
    if dual:
        data.update(mode="actor-speculator", optimizer_gist="completed dual-loop run")
    return data


def completion_contract(*, post_windows: int = 20, dual: bool = False) -> dict:
    value = {
        "measurement_windows": 50,
        "tuning_windows": 50 - post_windows,
        "post_tuning_windows": post_windows,
        "optimization_metric": "latency_p99",
        "allow_iteration_zero": True,
    }
    if dual:
        value.update(required_mode="actor-speculator", require_optimizer_gist=True)
    return value


def test_strict_completion_accepts_only_complete_finite_phase_correct_histories() -> None:
    suite = load_suite_module()
    contract = completion_contract(dual=True)
    valid = complete_history(dual=True)
    assert suite.result_satisfies_completion(valid, contract)

    with_baseline = deepcopy(valid)
    with_baseline["history"].insert(
        0,
        {
            "iteration": 0,
            "parameters": {"latency_ns": 1},
            "metrics": {"latency_p99": 100.0},
            "reward": 100.0,
            "post_tuning_phase": False,
        },
    )
    assert suite.result_satisfies_completion(with_baseline, contract)

    truncated = deepcopy(valid)
    truncated["history"].pop()
    assert not suite.result_satisfies_completion(truncated, contract)

    wrong_phase = deepcopy(valid)
    wrong_phase["history"][0]["post_tuning_phase"] = True
    assert not suite.result_satisfies_completion(wrong_phase, contract)

    nonfinite = deepcopy(valid)
    nonfinite["history"][4]["metrics"]["latency_p99"] = math.nan
    assert not suite.result_satisfies_completion(nonfinite, contract)

    missing_role_record = deepcopy(valid)
    missing_role_record["optimizer_gist"] = ""
    assert not suite.result_satisfies_completion(missing_role_record, contract)

    interrupted = deepcopy(valid)
    interrupted["reason"] = "interrupted"
    assert not suite.result_satisfies_completion(interrupted, contract)


def test_resume_ignores_newer_partial_history(tmp_path: Path) -> None:
    suite = load_suite_module()
    job = {"completion": completion_contract()}
    valid = tmp_path / "optimization_history_001.json"
    partial = tmp_path / "optimization_history_002.json"
    valid.write_text(json.dumps(complete_history()))
    truncated = complete_history()
    truncated["history"] = truncated["history"][:12]
    partial.write_text(json.dumps(truncated))
    assert suite.completed_result(tmp_path, job) == valid


def test_scoped_plotter_generates_claim_outputs_and_geometric_factors(tmp_path: Path) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(REPRO / "claim_manifest.json")
    raw = tmp_path / "raw"
    for job in manifest["jobs"]:
        job_id = job["id"]
        value = 100.0
        if ":mlos_app" in job_id:
            value = 80.0
        elif ":sematune_system" in job_id:
            value = 60.0
        elif ":2_param:" in job_id:
            value = 90.0
        elif ":16_param:" in job_id:
            value = 60.0
        elif ":41_param:" in job_id:
            value = 70.0
        elif ":sematune_app" in job_id:
            value = 50.0
        data = complete_history(
            post_windows=int(job["completion"]["post_tuning_windows"]),
            dual=bool(job["completion"].get("required_mode")),
        )
        for row in data["history"]:
            row["metrics"]["latency_p99"] = value
            row["reward"] = value
        directory = raw / job["target_results_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "optimization_history_fixture.json").write_text(json.dumps(data))

    archived = tmp_path / "archived"
    archived.mkdir()
    for name in (
        "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf",
        "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf",
        "ablation_param_geomean.pdf",
        "latency_by_params.csv",
    ):
        (archived / name).write_text("fixture")

    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_claims.py"),
            "--results-dir",
            str(raw),
            "--output-dir",
            str(tmp_path / "fresh"),
            "--report-dir",
            str(tmp_path),
            "--archived-plots-dir",
            str(archived),
        ],
        cwd=ROOT,
        check=True,
    )
    report = json.loads((tmp_path / "claim_report.json").read_text())
    assert [row["claim"] for row in report["fresh_claims"]] == ["C1", "C2", "C3", "C4"]
    assert all(row["observation_status"] == "CONSISTENT" for row in report["fresh_claims"])
    assert report["fresh_claims"][0]["fresh_factor"] == 2.0
    assert report["fresh_claims"][1]["fresh_factor"] == 1.6
    assert (tmp_path / "fresh" / "plots" / "c4_parameter_scaling.pdf").stat().st_size > 0
    assert (tmp_path / "fresh" / "tables" / "claim_summary.csv").is_file()
