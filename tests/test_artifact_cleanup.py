from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FUNCTIONAL = ROOT / "functional_example"
PAPER_RESULTS = ROOT / "all_results" / "paper_evaluation"

RETAINED_BENCHBASE_XML = {
    "sample_sibench_hi.xml",
    "sample_tpcc_hi.xml",
    "sample_ycsb_hi.xml",
    "twitter_highload_config.xml",
    "wikipedia_highload_config.xml",
}
REMOVED_RESULT_FAMILIES = {
    "fixed_100cycles",
    "mlos_120_tuning_only",
    "llm_dual_app_metrics_memory_final_actor",
    "llm_dual_system_metrics_plain_memory_final_actor",
    "llm_indirect_all_mode3_final_actor",
    "rag_llm_dual_app_metrics_memory_last_1_raw_to_summary_final_actor",
    "rag_llm_dual_indirect_all_mode3_memory_last_1_raw_to_summary_final_actor",
    "rag_llm_dual_app_metrics_memory_top_3_raw_to_summary_final_actor",
    "rag_llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_final_actor",
}
UNRELATED_CONFIG_PREFIXES = (
    "benchbase_",
    "dcperf_",
    "django_",
    "mutilate_",
    "tailbench_",
)
UNRELATED_CONFIG_KEYS = {"respect_config_window_duration", "sysbench_cpu_max_prime"}


def test_only_declared_benchbase_xml_files_remain() -> None:
    directory = ROOT / "config" / "benchbase" / "postgres"
    assert {path.name for path in directory.glob("*.xml")} == RETAINED_BENCHBASE_XML


def test_removed_result_families_are_absent_and_plot6_inputs_remain() -> None:
    present_directories = {path.name for path in PAPER_RESULTS.rglob("*") if path.is_dir()}
    assert REMOVED_RESULT_FAMILIES.isdisjoint(present_directories)

    memory_root = PAPER_RESULTS / "results_rag"
    for workload in ("silo", "sysbench_oltp", "tpcc"):
        directory = memory_root / workload
        assert (directory / "rag_llm_dual_app_metrics_memory_top_1_raw_to_summary_final_actor").is_dir()
        assert (directory / "rag_llm_dual_indirect_all_mode3_memory_top_1_raw_to_summary_final_actor").is_dir()
        assert (
            directory
            / "rag_llm_dual_app_metrics_memory_top_3_raw_to_summary_unseen_workload_final_actor"
        ).is_dir()
        assert (
            directory
            / "rag_llm_dual_indirect_all_mode3_memory_top_3_raw_to_summary_unseen_workload_final_actor"
        ).is_dir()


def test_paper_input_manifest_has_exact_path_coverage() -> None:
    manifest = ROOT / "artifact" / "paper_plot_inputs.sha256"
    recorded = {
        line[66:]
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line
    }
    present = {
        path.relative_to(ROOT).as_posix()
        for path in PAPER_RESULTS.rglob("*")
        if path.is_file()
    }
    assert recorded == present
    assert len(recorded) == 2098


def test_functional_sysbench_configs_are_benchmark_specific() -> None:
    from optimizer.config import SimpleConfig

    suite = json.loads((FUNCTIONAL / "sysbench_suite.json").read_text(encoding="utf-8"))
    assert len(suite["methods"]) == 14
    for method in suite["methods"]:
        path = FUNCTIONAL / method["config"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["benchmark"] == "sysbench_oltp"
        assert UNRELATED_CONFIG_KEYS.isdisjoint(payload)
        assert not any(key.startswith(UNRELATED_CONFIG_PREFIXES) for key in payload)
        loaded = SimpleConfig.load(str(path))
        assert loaded.benchmark == "sysbench_oltp"
        assert loaded.window_duration == suite["window_duration_seconds"]


def test_legacy_duration_key_loads_but_no_longer_changes_duration() -> None:
    from optimizer.config import SimpleConfig

    path = ROOT / "reproduction" / "configs" / "common" / "sysbench_oltp_rw_hi_p99" / "fixed.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "respect_config_window_duration" in payload
    loaded = SimpleConfig.load(str(path))
    assert loaded.window_duration == payload["window_duration"]
    assert not hasattr(loaded, "respect_config_window_duration")

    default_config = SimpleConfig()
    assert (ROOT / default_config.benchbase_config_file).is_file()


def test_rag_plotter_uses_curated_default(monkeypatch) -> None:
    path = ROOT / "scripts" / "plot_rag_memory_app_system_split.py"
    spec = importlib.util.spec_from_file_location("artifact_rag_plotter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(path), "--output-dir", "unused"])
    args = module.parse_args()
    assert args.discover_under == "all_results/paper_evaluation/results_rag"


def test_obsolete_entrypoints_are_removed_and_maintenance_tools_are_grouped() -> None:
    assert not (ROOT / "docs" / "DCPerf_SparkBench_Walkthrough.md").exists()
    assert not (ROOT / "scripts" / "run_sysbench_41param_smoke.sh").exists()
    assert not (ROOT / "scripts" / "plot_agentic_memory_comparison.py").exists()
    assert not (FUNCTIONAL / "build_sysbench_configs.py").exists()
    assert not (FUNCTIONAL / "extract_recorded_traces.py").exists()
    for name in (
        "build_paper_input_manifest.py",
        "build_sysbench_configs.py",
        "extract_recorded_traces.py",
    ):
        assert (ROOT / "tools" / "maintenance" / name).is_file()


def test_functional_audit_documents_are_present_and_todos_are_resolved() -> None:
    expected = (
        ROOT / "artifact" / "VALIDATION_ENVIRONMENT.md",
        ROOT / "artifact" / "THIRD_PARTY_MODIFICATIONS.md",
        ROOT / "docs" / "FUNCTIONAL_REVIEWER_NOTES.md",
    )
    for path in expected:
        assert path.is_file()

    capture = ROOT / "scripts" / "capture_environment.sh"
    assert capture.is_file() and os.access(capture, os.X_OK)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Remaining Functional TODOs" not in readme
    assert "artifact/VALIDATION_ENVIRONMENT.md" in readme
    assert "docs/FUNCTIONAL_REVIEWER_NOTES.md" in readme
    assert "artifact/THIRD_PARTY_MODIFICATIONS.md" in readme
