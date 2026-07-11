from __future__ import annotations

import importlib.util
import json
import subprocess
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
