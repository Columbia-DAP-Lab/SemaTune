import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_smoke_sources_are_fixed_benchbase_workloads():
    for workload, directory in (
        ("wikipedia", "wikipedia_p99"),
        ("twitter", "twitter_p99"),
        ("ycsb", "ycsb_hi_p99"),
    ):
        path = ROOT / "reproduction" / "configs" / "common" / directory / "fixed.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["benchmark"] == workload
        assert payload["tuner_type"] == "fixed"
        assert payload["optimization_metric"] == "latency_p99"


def test_smoke_materialization_is_exactly_one_window():
    helper = (ROOT / "functional_example" / "benchbase_smoke.py").read_text(encoding="utf-8")
    runner = (ROOT / "functional_example" / "run_benchbase_smoke.sh").read_text(encoding="utf-8")
    assert '"max_iterations": 1' in helper
    assert '"post_tuning_windows": 0' in helper
    assert "for workload in wikipedia twitter ycsb" in runner
    assert "dropdb --if-exists --force" in runner
    assert "host_state_guard.py" in runner
