from __future__ import annotations

import importlib.util
import csv
import json
import math
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPRO = ROOT / "reproduction"
TRACE_BASELINE = REPRO / "trace_baselines" / "c1_c4_provider"
C123_FAMILY_MANIFEST = REPRO / "c123_family_manifest.json"
C4_METHOD_MANIFEST = REPRO / "c4_method_manifest.json"
THREE_APP_PLOTS_6_7_MANIFEST = REPRO / "three_app_plots_6_7_manifest.json"
EXTENDED_CLAIM_MANIFEST = REPRO / "extended_claim_manifest.json"
FULL_CLAIM_MANIFEST = REPRO / "full_claim_manifest.json"


def load_suite_module():
    spec = importlib.util.spec_from_file_location("reproduction_suite", REPRO / "suite.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_compare_module():
    sys.path.insert(0, str(REPRO))
    try:
        spec = importlib.util.spec_from_file_location("reproduction_compare_replay", REPRO / "compare_replay.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPRO))


def load_c123_validator_module():
    sys.path.insert(0, str(REPRO))
    try:
        spec = importlib.util.spec_from_file_location(
            "reproduction_validate_c123_families",
            REPRO / "validate_c123_families.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPRO))


def test_manifest_covers_all_plots_with_one_rerun_and_reuse() -> None:
    manifest = json.loads((REPRO / "experiment_manifest.json").read_text())
    assert manifest["reruns_per_configuration"] == 1
    assert set(manifest["plots"]) == {str(number) for number in range(6, 13)}
    assert len(manifest["jobs"]) == 273
    assert len(manifest["aliases"]) == 9
    assert len(manifest["plots"]["6"]["jobs"]) == 91
    assert len(manifest["plots"]["11"]["jobs"]) == 21
    assert len(manifest["plots"]["12"]["jobs"]) == 9
    plot6 = set(manifest["plots"]["6"]["jobs"])
    plot9 = set(manifest["plots"]["9"]["jobs"])
    assert plot9 < plot6


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
    assert "unique one-rerun configurations: 91" in completed.stdout
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


def test_c123_family_manifest_selects_only_required_claim_jobs() -> None:
    suite = load_suite_module()
    raw = json.loads(C123_FAMILY_MANIFEST.read_text())
    manifest = suite.load_manifest(C123_FAMILY_MANIFEST)
    expected_workloads = {
        "masstree_hi_p99",
        "silo_hi_p99",
        "sphinx_tput_max",
        "xapian_hi_p99",
        "sibench_hi_p99",
        "tpcc_hi_p99",
        "twitter_p99",
        "wikipedia_p99",
        "ycsb_hi_p99",
        "sysbench_cpu_tput",
        "sysbench_oltp_rw_hi_p99",
    }
    assert set(raw["workloads"]) == expected_workloads
    assert len(raw["workloads"]) == 11
    assert len(manifest["jobs"]) == 44
    assert set(manifest["plots"]) == {"1", "2", "3"}
    assert len(manifest["plots"]["1"]["jobs"]) == 22
    assert len(manifest["plots"]["2"]["jobs"]) == 33
    assert len(manifest["plots"]["3"]["jobs"]) == 33
    assert len(manifest["cohorts"]["all"]["workloads"]) == 11
    assert len(manifest["cohorts"]["excluding_xapian"]["workloads"]) == 10
    assert "xapian_hi_p99" not in manifest["cohorts"]["excluding_xapian"]["workloads"]
    assert {
        job["id"].split(":")[-1] for job in manifest["jobs"]
    } == {"fixed", "mlos_app", "sematune_app", "sematune_system"}
    assert not any(job["id"].startswith("parameter:") for job in manifest["jobs"])
    sphinx = [job for job in manifest["jobs"] if ":sphinx_tput_max:" in job["id"]]
    assert len(sphinx) == 4
    assert all(job["completion"]["max_leading_empty_windows"] == 2 for job in sphinx)
    assert not any(
        "max_leading_empty_windows" in job["completion"]
        for job in manifest["jobs"]
        if job not in sphinx
    )
    assert suite.validate_manifest(manifest, verify_sources=True) == []


def test_c123_family_wrapper_dry_run_is_read_only(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_c123_families.sh"),
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "unique one-rerun configurations: 44" in completed.stdout
    assert "LLM configurations: 22" in completed.stdout
    assert "total benchmark windows: 2200" in completed.stdout
    assert "claim 4:" not in completed.stdout
    assert not output.exists()

    clean = subprocess.run(
        [
            str(REPRO / "reproduce_c123_families.sh"),
            "--dry-run",
            "--clean",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert clean.returncode == 2
    assert "--clean cannot be combined with --dry-run" in clean.stderr
    assert not output.exists()


def test_c123_validator_bounds_tailbench_startup_empty_windows() -> None:
    validator = load_c123_validator_module()
    row = {
        "post_tuning_phase": False,
        "metrics": {"request_count": 0, "intervals_aggregated": 0},
    }
    assert validator.is_allowed_leading_empty_window(row, value=0.0, index=0, maximum=2)
    assert validator.is_allowed_leading_empty_window(row, value=0.0, index=1, maximum=2)
    assert not validator.is_allowed_leading_empty_window(row, value=0.0, index=2, maximum=2)
    assert not validator.is_allowed_leading_empty_window(row, value=-1.0, index=0, maximum=2)
    assert not validator.is_allowed_leading_empty_window(
        {**row, "post_tuning_phase": True}, value=0.0, index=0, maximum=2
    )
    assert not validator.is_allowed_leading_empty_window(
        {"post_tuning_phase": False, "metrics": {"request_count": 1, "intervals_aggregated": 1}},
        value=0.0,
        index=0,
        maximum=2,
    )


def test_c4_method_manifest_omits_trim_and_mlos_at_41_knobs() -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(C4_METHOD_MANIFEST)
    assert manifest["knob_counts"] == [2, 8, 16, 41]
    assert manifest["workloads"] == [
        "silo_hi_p99",
        "tpcc_hi_p99",
        "sysbench_oltp_rw_hi_p99",
    ]
    assert len(manifest["jobs"]) == 33
    assert set(manifest["plots"]) == {"4"}
    assert len(manifest["plots"]["4"]["jobs"]) == 33
    assert sum(":llm_trimming" in job["id"] for job in manifest["jobs"]) == 6
    assert sum(job["id"].endswith(":mlos") for job in manifest["jobs"]) == 6
    assert not any("41_param" in job["id"] and ":llm_trimming" in job["id"] for job in manifest["jobs"])
    assert not any("41_param" in job["id"] and job["id"].endswith(":mlos") for job in manifest["jobs"])
    assert manifest["method_knob_counts"]["sematune_trim_app"] == [2, 8, 16]
    assert manifest["method_knob_counts"]["mlos_app"] == [2, 8, 16]
    assert sum(job["id"].endswith(":sematune_trim_app") for job in manifest["jobs"]) == 3
    assert suite.validate_manifest(manifest, verify_sources=True) == []


def test_c4_method_wrapper_dry_run_is_read_only(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_c4_methods.sh"),
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "unique one-rerun configurations: 33" in completed.stdout
    assert "total benchmark windows: 1650" in completed.stdout
    assert "15 measured MLOS/TuxBot-Trim configurations" in completed.stdout
    assert not output.exists()


def test_three_app_plots_6_7_manifest_has_exact_requested_matrix() -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(THREE_APP_PLOTS_6_7_MANIFEST)
    assert manifest["workloads"] == [
        "silo_hi_p99",
        "tpcc_hi_p99",
        "sysbench_oltp_rw_hi_p99",
    ]
    assert len(manifest["jobs"]) == 30
    assert len(manifest["plots"]["6"]["jobs"]) == 12
    assert len(manifest["plots"]["7"]["jobs"]) == 30
    assert manifest["plots"]["6"]["paper_plot"] == 6
    assert manifest["plots"]["7"]["paper_plot"] == 7
    methods = {job["id"].rsplit(":", 1)[-1] for job in manifest["jobs"]}
    assert methods == {
        "fixed", "sematune_app", "sematune_system", "sematune_ipc",
        "sematune_trim_app", "sematune_trim_ipc", "sematune_trim_cache",
        "mlos_app", "mlos_ipc", "mlos_cache",
    }
    assert all(job.get("completion") for job in manifest["jobs"])
    assert sum(int(job["completion"]["measurement_windows"]) for job in manifest["jobs"]) == 1500
    assert suite.validate_manifest(manifest, verify_sources=True) == []


def test_three_app_plots_6_7_wrapper_dry_run_is_read_only(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_three_app_plots_6_7.sh"),
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "unique one-rerun configurations: 30" in completed.stdout
    assert "total benchmark windows: 1500" in completed.stdout
    assert "15 signal jobs remain" in completed.stdout
    assert not output.exists()


def test_committed_claim_trace_baseline_is_complete_and_provider_free() -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(REPRO / "claim_manifest.json")
    bundle = suite.load_trace_bundle(TRACE_BASELINE)
    assert bundle["job_count"] == 21
    assert bundle["trace_count"] == 15
    assert bundle["measurement_windows"] == 1050
    assert bundle["provider_models"] == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    assert suite.trace_bundle_errors(manifest["jobs"], TRACE_BASELINE) == []
    for record in bundle["traces"].values():
        trace = json.loads((TRACE_BASELINE / record["path"]).read_text(encoding="utf-8"))
        assert trace["provider_requests"] == 0
        assert not str(trace["source_history"]).startswith("/")


def test_committed_claim_replay_wrapper_dry_run_is_read_only(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "replay_claims.sh"),
            "--dry-run",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "TRACE_REPLAY_BUNDLE: PASS" in completed.stdout
    assert "unique one-rerun configurations: 21" in completed.stdout
    assert "LLM configurations: 15" in completed.stdout
    assert "total benchmark windows: 1050" in completed.stdout
    assert not output.exists()


def test_live_preflight_requires_generated_database_environment(monkeypatch) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(REPRO / "claim_manifest.json")
    for name in (
        "SEMATUNE_SYSBENCH_HOST",
        "SEMATUNE_SYSBENCH_PORT",
        "SEMATUNE_SYSBENCH_USER",
        "SEMATUNE_SYSBENCH_PASSWORD",
        "SEMATUNE_SYSBENCH_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    errors = suite.live_preflight(suite.select_jobs(manifest, {1, 2, 3, 4}))
    for name in (
        "SEMATUNE_SYSBENCH_HOST",
        "SEMATUNE_SYSBENCH_PORT",
        "SEMATUNE_SYSBENCH_USER",
        "SEMATUNE_SYSBENCH_PASSWORD",
        "SEMATUNE_SYSBENCH_DB",
    ):
        assert f"missing site environment variable: {name}" in errors


def test_live_preflight_warns_but_passes_without_perf(monkeypatch, capsys) -> None:
    suite = load_suite_module()
    monkeypatch.setattr(suite.os, "geteuid", lambda: 0)
    monkeypatch.setattr(suite.os, "sched_getaffinity", lambda _pid: set(range(20)))
    monkeypatch.setattr(
        suite.shutil,
        "which",
        lambda command: None if command == "perf" else f"/usr/bin/{command}",
    )

    assert suite.live_preflight([]) == []
    assert "PREFLIGHT_WARNING: perf is unavailable" in capsys.readouterr().err


def test_clean_is_rejected_in_read_only_dry_run(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_claims.sh"),
            "--dry-run",
            "--clean",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "--clean cannot be combined with --dry-run" in completed.stderr
    assert not output.exists()


def test_claim_wrapper_reports_missing_option_values() -> None:
    cases = (
        (["--run", "--output-dir"], "--output-dir"),
        (["--dry-run", "--trace-replay-bundle"], "--trace-replay-bundle"),
        (["--dry-run", "--trace-replay-from"], "--trace-replay-from"),
    )
    for args, option in cases:
        completed = subprocess.run(
            [str(REPRO / "reproduce_claims.sh"), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2
        assert f"Missing value for {option}." in completed.stderr
        assert "Usage:" in completed.stderr


def test_trace_builder_preserves_actions_delays_and_noops(tmp_path: Path) -> None:
    suite = load_suite_module()
    history = tmp_path / "dual_loop_actor_speculator_test.json"
    history.write_text(
        json.dumps(
            {
                "mode": "actor-speculator",
                "config": {"benchmark": "tailbench", "max_iterations": 2},
                "history": [
                    {
                        "iteration": 1,
                        "tuner_timing": {
                            "quick": {
                                "proposed_parameters": {"latency_ns": 1000},
                                "tuner_duration": 1.25,
                                "parameters_applied": True,
                            },
                            "reasoning": {
                                "proposed_parameters": {"latency_ns": 2000},
                                "tuner_start_iteration": 1,
                                "tuner_duration": 3.5,
                                "parameters_applied": True,
                            },
                        },
                    },
                    {
                        "iteration": 2,
                        "tuner_timing": {
                            "reasoning_final_before_stable": {
                                "proposed_parameters": None,
                                "tuner_duration": 4.75,
                                "parameters_applied": False,
                                "justification": "Recorded final no-op.",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    trace = suite.build_replay_trace("test:dual", history)
    entries = {row["iteration"]: row["responses"] for row in trace["history"]}
    assert trace["provider_requests"] == 0
    assert trace["recorded_responses"] == 3
    assert trace["recorded_actions"] == 2
    assert trace["recorded_noops"] == 1
    assert trace["synthetic_noops"] == 2
    assert entries[0]["quick"]["parameters"] == {"latency_ns": 1000}
    assert entries[0]["quick"]["response_time_seconds"] == 1.25
    assert entries[0]["reasoning"]["parameters"] == {"latency_ns": 2000}
    assert entries[1]["quick"]["recorded_response"] is False
    assert entries[1]["reasoning"]["recorded_response"] is False
    assert entries[2]["reasoning_final"]["parameters"] == {}
    assert entries[2]["reasoning_final"]["recorded_response"] is True
    assert entries[2]["reasoning_final"]["response_time_seconds"] == 4.75


def test_replay_action_audit_counts_an_unobserved_source_action(monkeypatch, tmp_path: Path) -> None:
    compare = load_compare_module()
    source = tmp_path / "source.json"
    replay = tmp_path / "replay.json"
    trace = tmp_path / "trace.json"
    source.write_text(
        json.dumps(
            {
                "config": {"max_iterations": 2},
                "history": [
                    {
                        "iteration": 1,
                        "tuner_timing": {"quick": {"proposed_parameters": {"latency_ns": 1000}}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    replay.write_text(
        json.dumps(
            {
                "config": {"max_iterations": 2, "llm_replay_file": str(trace)},
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    trace.write_text(json.dumps({"provider_requests": 0}), encoding="utf-8")
    job = {"id": "test:replay", "target_results_dir": "unused"}
    results = iter((replay, source))
    monkeypatch.setattr(compare.suite, "materialized_config", lambda _: {"tuner_type": "llm"})
    monkeypatch.setattr(compare.suite, "completed_result", lambda *_: next(results))

    rows = compare.action_audit(
        {"jobs": [job]},
        tmp_path / "original",
        tmp_path / "replayed",
        {"jobs": {job["id"]: {"replay_trace": str(trace)}}},
    )

    assert rows[0]["source_actions"] == 1
    assert rows[0]["replay_actions"] == 0
    assert rows[0]["unobserved_actions"] == 1


def test_trace_replay_same_source_and_output_requires_clean(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_claims.sh"),
            "--run",
            "--trace-replay-from",
            str(tmp_path),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "--clean is required to archive the source first" in completed.stderr


def test_trace_replay_dry_run_rejects_missing_source_without_writes(tmp_path: Path) -> None:
    source = tmp_path / "missing-source"
    output = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            str(REPRO / "reproduce_claims.sh"),
            "--dry-run",
            "--trace-replay-from",
            str(source),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "trace replay source directory is missing" in completed.stderr
    assert not output.exists()


def test_extended_and_full_claim_manifests_cover_requested_tiers_without_four_knobs() -> None:
    extended = json.loads(EXTENDED_CLAIM_MANIFEST.read_text())
    full = json.loads(FULL_CLAIM_MANIFEST.read_text())
    three_workloads = {
        "silo_hi_p99",
        "tpcc_hi_p99",
        "sysbench_oltp_rw_hi_p99",
    }
    plot6_methods = {
        "fixed",
        "sematune_app",
        "sematune_trim_app",
        "mlos_app",
        "bayesian",
        "dqn",
        "qlearning",
    }
    plot7_methods = {
        "fixed",
        "sematune_app",
        "sematune_system",
        "sematune_ipc",
        "sematune_trim_app",
        "sematune_trim_ipc",
        "sematune_trim_cache",
        "mlos_app",
        "mlos_ipc",
        "mlos_cache",
    }

    assert extended["tier"] == "extended"
    assert set(extended["workloads"]) == three_workloads
    assert extended["expected_unique_configurations"] == len(extended["jobs"]) == 60
    assert extended["expected_measurement_windows"] == 3360
    assert len(extended["plots"]["6"]["jobs"]) == 21
    assert len(extended["plots"]["7"]["jobs"]) == 30
    assert len(extended["plots"]["10"]["jobs"]) == 33

    assert full["tier"] == "full"
    assert len(full["workloads"]) == 11
    assert set(full["parameter_workloads"]) == three_workloads
    assert full["expected_unique_configurations"] == len(full["jobs"]) == 164
    assert full["expected_measurement_windows"] == 9480
    assert len(full["plots"]["6"]["jobs"]) == 77
    assert len(full["plots"]["7"]["jobs"]) == 110
    assert len(full["plots"]["10"]["jobs"]) == 33

    for manifest in (extended, full):
        job_ids = {job["id"] for job in manifest["jobs"]}
        assert manifest["knob_counts"] == [2, 8, 16, 41]
        assert manifest["method_knob_counts"] == {
            "mlos_app": [2, 8, 16],
            "sematune_app": [2, 8, 16, 41],
            "sematune_trim_app": [2, 8, 16],
        }
        assert not any(":4_param:" in job_id for job_id in job_ids)
        assert not any(
            ":41_param:" in job_id
            and (job_id.endswith(":llm_trimming") or job_id.endswith(":mlos"))
            for job_id in job_ids
        )
        for workload in three_workloads:
            assert any(
                f":41_param:{workload}:llm_dual_app_metrics_final_actor" in job_id
                for job_id in job_ids
            )
        for workload in manifest["workloads"]:
            assert {
                f"common:{workload}:{method}" for method in plot6_methods
            } <= set(manifest["plots"]["6"]["jobs"])
            assert {
                f"common:{workload}:{method}" for method in plot7_methods
            } <= set(manifest["plots"]["7"]["jobs"])


def test_tier_dry_runs_report_exact_scope_and_do_not_write(tmp_path: Path) -> None:
    cases = (
        ("--extended", 60, 3360, "EXTENDED CLAIM WORKFLOW"),
        ("--full", 164, 9480, "FULL CLAIM WORKFLOW"),
    )
    for option, configs, windows, label in cases:
        output = tmp_path / option.removeprefix("--")
        completed = subprocess.run(
            [
                str(REPRO / "reproduce_claims.sh"),
                "--dry-run",
                option,
                "--output-dir",
                str(output),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"unique one-rerun configurations: {configs}" in completed.stdout
        assert f"total benchmark windows: {windows}" in completed.stdout
        assert "plots: 6,7,10" in completed.stdout
        assert label in completed.stdout
        assert "4-knob" not in completed.stdout
        assert not output.exists()
    assert "several days" in completed.stdout
    assert "substantial hosted-model quota" in completed.stdout


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


def test_c123_family_plotter_handles_goals_and_xapian_cohorts(tmp_path: Path) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(C123_FAMILY_MANIFEST)
    raw = tmp_path / "raw"
    for job in manifest["jobs"]:
        workload = job["id"].split(":")[1]
        method = job["id"].split(":")[2]
        metric = job["completion"]["optimization_metric"]
        goal = manifest["optimization_goals"][workload]
        factors = {
            "fixed": 1.0,
            "mlos_app": 1.25,
            "sematune_app": 2.0,
            "sematune_system": 5.0 / 3.0,
        }
        expected_factor = factors[method]
        value = 100.0 * expected_factor if goal == "maximize" else 100.0 / expected_factor
        data = complete_history(
            post_windows=int(job["completion"]["post_tuning_windows"]),
            dual=bool(job["completion"].get("required_mode")),
        )
        data["config"] = {
            "optimization_metric": metric,
            "optimization_goal": goal,
        }
        for row in data["history"]:
            row["metrics"] = {metric: value}
            row["reward"] = value
        directory = raw / job["target_results_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "optimization_history_fixture.json").write_text(json.dumps(data))

    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_c123_families.py"),
            "--results-dir",
            str(raw),
            "--output-dir",
            str(tmp_path / "fresh"),
            "--report-dir",
            str(tmp_path),
            "--manifest",
            str(C123_FAMILY_MANIFEST),
        ],
        cwd=ROOT,
        check=True,
    )
    report = json.loads((tmp_path / "c123_family_report.json").read_text())
    assert report["claims"] == ["C1", "C2", "C3"]
    assert report["c4_evaluated"] is False
    assert report["claim_plot_map"] == {
        "C1": "plot_6",
        "C2": "plot_6",
        "C3": "plot_7",
    }
    assert len(report["fresh_claims"]) == 6
    claims = {
        (row["claim"], row["cohort"]): row for row in report["fresh_claims"]
    }
    for cohort in ("all", "excluding_xapian"):
        assert claims[("C1", cohort)]["fresh_factor"] == 2.0
        assert math.isclose(claims[("C2", cohort)]["fresh_factor"], 1.6)
        assert math.isclose(claims[("C3", cohort)]["fresh_factor"], 4.0 / 3.0)
        assert all(
            claims[(claim, cohort)]["observation_status"] == "CONSISTENT"
            for claim in ("C1", "C2", "C3")
        )
    aggregate = report["aggregate_improvement"]
    assert {row["n_workloads"] for row in aggregate if row["cohort"] == "all"} == {11}
    assert {
        row["n_workloads"] for row in aggregate if row["cohort"] == "excluding_xapian"
    } == {10}
    expected_plots = {
        "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf": (
            564.1350915211,
            226.18,
            24,
        ),
        "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf": (
            566.0,
            213.646144,
            36,
        ),
    }
    for filename, (width, height, csv_count) in expected_plots.items():
        plot_path = tmp_path / "fresh" / "plots" / filename
        assert plot_path.stat().st_size > 1000
        media_box = re.search(
            rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\]",
            plot_path.read_bytes(),
        )
        assert media_box is not None
        assert math.isclose(float(media_box.group(1)), width, abs_tol=0.01)
        assert float(media_box.group(2)) + 0.01 >= height
        with plot_path.with_suffix(".csv").open() as handle:
            plot_rows = list(csv.DictReader(handle))
        assert len(plot_rows) == csv_count
        assert any(int(row["n_workloads"]) == 0 for row in plot_rows)
    with (tmp_path / "fresh" / "tables" / "c123_improvement_factors.csv").open() as handle:
        factors_table = list(csv.DictReader(handle))
    throughput = next(
        row for row in factors_table
        if row["workload"] == "sysbench_cpu_tput"
        and row["method"] == "sematune_app"
        and row["phase"] == "stable"
    )
    assert throughput["optimization_goal"] == "maximize"
    assert float(throughput["improvement_factor"]) == 2.0


def test_three_app_plotter_fills_requested_slots_and_preserves_paper_geometry(
    tmp_path: Path,
) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(THREE_APP_PLOTS_6_7_MANIFEST)
    raw = tmp_path / "raw"
    objective_values = {
        "fixed": 100.0,
        "sematune_app": 50.0,
        "sematune_system": 60.0,
        "sematune_ipc": 70.0,
        "sematune_trim_app": 65.0,
        "sematune_trim_ipc": 75.0,
        "sematune_trim_cache": 80.0,
        "mlos_app": 80.0,
        "mlos_ipc": 85.0,
        "mlos_cache": 90.0,
    }
    for job in manifest["jobs"]:
        method = job["id"].rsplit(":", 1)[-1]
        metric = job["completion"]["optimization_metric"]
        data = complete_history(
            post_windows=int(job["completion"]["post_tuning_windows"]),
            dual=bool(job["completion"].get("required_mode")),
        )
        data["config"] = {
            "optimization_metric": metric,
            "optimization_goal": "minimize",
        }
        for row in data["history"]:
            row["metrics"] = {
                "latency_p99": objective_values[method],
                metric: 1.0 if metric != "latency_p99" else objective_values[method],
            }
            row["reward"] = float(row["metrics"][metric])
        directory = raw / job["target_results_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "optimization_history_fixture.json").write_text(json.dumps(data))

    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_three_app_paper.py"),
            "--results-dir",
            str(raw),
            "--output-dir",
            str(tmp_path / "fresh"),
            "--report-dir",
            str(tmp_path),
            "--manifest",
            str(THREE_APP_PLOTS_6_7_MANIFEST),
        ],
        cwd=ROOT,
        check=True,
    )
    report = json.loads((tmp_path / "three_app_plots_6_7_report.json").read_text())
    assert report["claim_plot_map"] == {"C1": "plot_6", "C2": "plot_6", "C3": "plot_7"}
    assert all(row["status"] == "CONSISTENT" for row in report["claims"])
    expected = {
        "retry_aggregate_improvement_geomean_with_and_without_xapian.pdf": (564.1350915211, 226.18, 24),
        "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.pdf": (566.0, 213.646144, 36),
    }
    for filename, (width, height, row_count) in expected.items():
        path = tmp_path / "fresh" / "plots" / filename
        match = re.search(
            rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\]",
            path.read_bytes(),
        )
        assert match is not None
        assert math.isclose(float(match.group(1)), width, abs_tol=0.01)
        assert float(match.group(2)) + 0.01 >= height
        with path.with_suffix(".csv").open() as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == row_count
    with (
        tmp_path / "fresh" / "plots" / "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.csv"
    ).open() as handle:
        plot7_rows = list(csv.DictReader(handle))
    assert {int(row["n_workloads"]) for row in plot7_rows} == {3}


def test_base_plotters_use_paper_layout_and_preserve_unselected_slots(tmp_path: Path) -> None:
    suite = load_suite_module()
    manifest_path = REPRO / "claim_manifest.json"
    manifest = suite.load_manifest(manifest_path)
    raw = tmp_path / "raw"
    objective_values = {
        "fixed": 100.0,
        "mlos_app": 80.0,
        "sematune_app": 50.0,
        "sematune_system": 60.0,
        "llm_dual_app_metrics_final_actor": 50.0,
    }
    for job in manifest["jobs"]:
        method = job["id"].rsplit(":", 1)[-1]
        value = objective_values[method]
        data = complete_history(
            post_windows=int(job["completion"]["post_tuning_windows"]),
            dual=bool(job["completion"].get("required_mode")),
        )
        data["config"] = {
            "optimization_metric": "latency_p99",
            "optimization_goal": "minimize",
        }
        for row in data["history"]:
            row["metrics"]["latency_p99"] = value
            row["reward"] = value
        directory = raw / job["target_results_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "optimization_history_fixture.json").write_text(json.dumps(data))

    fresh = tmp_path / "fresh"
    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_three_app_paper.py"),
            "--results-dir", str(raw),
            "--output-dir", str(fresh),
            "--report-dir", str(tmp_path),
            "--manifest", str(manifest_path),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_c4_methods.py"),
            "--results-dir", str(raw),
            "--output-dir", str(fresh),
            "--report-dir", str(tmp_path),
            "--manifest", str(manifest_path),
        ],
        cwd=ROOT,
        check=True,
    )

    with (
        fresh / "plots" / "retry_aggregate_improvement_geomean_with_and_without_xapian.csv"
    ).open() as handle:
        plot6 = {(row["phase"], row["method"]): row for row in csv.DictReader(handle)}
    assert int(plot6[("stable", "TuxBot App Metrics Dual Loop")]["n_workloads"]) == 3
    assert int(plot6[("stable", "MLOS")]["n_workloads"]) == 3
    assert int(plot6[("stable", "MLOS + TuxBot")]["n_workloads"]) == 0
    assert int(plot6[("stable", "Bayesian")]["n_workloads"]) == 0

    with (
        fresh / "plots" / "retry_indirect_aggregate_improvement_geomean_with_and_without_xapian.csv"
    ).open() as handle:
        plot7 = {(row["phase"], row["method"]): row for row in csv.DictReader(handle)}
    assert int(plot7[("stable", "TuxBot App Only Dual")]["n_workloads"]) == 3
    assert int(plot7[("stable", "TuxBot Indirect Dump Dual")]["n_workloads"]) == 3
    assert int(plot7[("stable", "MLOS App Metrics")]["n_workloads"]) == 3
    assert int(plot7[("stable", "TuxBot IPC Dual")]["n_workloads"]) == 0
    assert int(plot7[("stable", "TuxBot Trim App")]["n_workloads"]) == 0

    with (fresh / "tables" / "ablation_param_geomean.csv").open() as handle:
        plot10 = {int(row["count"]): row for row in csv.DictReader(handle)}
    assert all(int(plot10[count]["llm_stable_n"]) == 3 for count in (2, 8, 16, 41))
    assert int(plot10[8]["mlos_stable_n"]) == 3
    assert all(int(plot10[count]["mlos_stable_n"]) == 0 for count in (2, 16, 41))
    assert all(int(plot10[count]["llm_trim_stable_n"]) == 0 for count in (2, 8, 16, 41))


def test_c4_plotter_preserves_empty_trim_and_mlos_41_points(tmp_path: Path) -> None:
    suite = load_suite_module()
    manifest = suite.load_manifest(C4_METHOD_MANIFEST)
    raw = tmp_path / "raw"
    for job in manifest["jobs"]:
        job_id = job["id"]
        if job_id.endswith(":fixed"):
            value = 100.0
        elif job_id.endswith(":mlos_app") or job_id.endswith(":mlos"):
            value = 80.0
        elif job_id.endswith(":sematune_trim_app") or job_id.endswith(":llm_trimming"):
            value = 65.0
        else:
            value = 50.0
        data = complete_history(
            post_windows=int(job["completion"]["post_tuning_windows"]),
            dual=bool(job["completion"].get("required_mode")),
        )
        data["config"] = {
            "optimization_metric": "latency_p99",
            "optimization_goal": "minimize",
        }
        for row in data["history"]:
            row["metrics"]["latency_p99"] = value
            row["reward"] = value
        directory = raw / job["target_results_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "optimization_history_fixture.json").write_text(json.dumps(data))

    subprocess.run(
        [
            sys.executable,
            str(REPRO / "plot_c4_methods.py"),
            "--results-dir",
            str(raw),
            "--output-dir",
            str(tmp_path / "fresh"),
            "--report-dir",
            str(tmp_path),
            "--manifest",
            str(C4_METHOD_MANIFEST),
        ],
        cwd=ROOT,
        check=True,
    )
    report = json.loads((tmp_path / "c4_method_report.json").read_text())
    omitted = next(
        row for row in report["observations"]
        if row["method"] == "MLOS" and row["knob_count"] == 41
    )
    assert omitted == {
        "method": "MLOS",
        "knob_count": 41,
        "tuning_factor": None,
        "tuning_pct": None,
        "stable_factor": None,
        "stable_pct": None,
        "workload_count": 0,
        "status": "NOT_RUN",
    }
    omitted_trim = next(
        row for row in report["observations"]
        if row["method"] == "TuxBot-Trim" and row["knob_count"] == 41
    )
    assert omitted_trim["tuning_factor"] is None
    assert omitted_trim["stable_factor"] is None
    assert omitted_trim["workload_count"] == 0
    assert omitted_trim["status"] == "NOT_RUN"
    assert report["claim_comparison"]["method"] == "TuxBot"
    assert report["claim_comparison"]["knob_count"] == 41
    with (tmp_path / "fresh" / "tables" / "ablation_param_geomean.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    row_41 = next(row for row in rows if row["count"] == "41")
    assert row_41["mlos_tuning_n"] == "0"
    assert row_41["mlos_stable_n"] == "0"
    assert row_41["mlos_tuning_factor"] == ""
    assert row_41["mlos_stable_factor"] == ""
    assert row_41["llm_trim_tuning_n"] == "0"
    assert row_41["llm_trim_stable_n"] == "0"
    assert row_41["llm_trim_tuning_factor"] == ""
    assert row_41["llm_trim_stable_factor"] == ""
