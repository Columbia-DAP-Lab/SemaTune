import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supported_sources_are_canonical_and_compatible():
    for workload, benchmark, metric in (
        ("sysbench_cpu_tput", "sysbench_cpu", "throughput"),
        ("tpcc_hi_p99", "tpcc", "latency_p99"),
    ):
        root = ROOT / "reproduction" / "configs" / "common" / workload
        fixed = json.loads((root / "fixed.json").read_text(encoding="utf-8"))
        dual = json.loads((root / "sematune_app.json").read_text(encoding="utf-8"))
        assert fixed["benchmark"] == dual["benchmark"] == benchmark
        assert fixed["optimization_metric"] == dual["optimization_metric"] == metric
        assert fixed["parameters_to_tune"] == dual["parameters_to_tune"]
        assert fixed["parameter_ranges"] == dual["parameter_ranges"]
        assert dual["tuner_type"] == "llm"
        assert dual["dual_loop_force_final_actor_before_stable"] is True


def test_runner_normalizes_both_methods_to_30_plus_20():
    helper = (ROOT / "functional_example" / "fixed_dual_comparison.py").read_text(encoding="utf-8")
    runner = (ROOT / "functional_example" / "run_fixed_dual_comparison.sh").read_text(encoding="utf-8")
    assert '"max_iterations": 30' in helper
    assert '"post_tuning_windows": 20' in helper
    assert "for method in fixed sematune_app" in runner
    assert "host_state_guard.py" in runner
