from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FUNCTIONAL = ROOT / "functional_example"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_checked_in_configs_and_role_replay_validate_without_credentials():
    env = {key: value for key, value in os.environ.items() if key != "GEMINI_API_KEY"}
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "functional_tool.py"), "validate-configs"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 quick + 6 representative" in result.stdout


def test_null_actor_speculator_fields_remain_single_loop():
    from barebones_optimizer.config import SimpleConfig

    single = SimpleConfig.load(str(FUNCTIONAL / "tpcc_sematune_single.json"))
    dual = SimpleConfig.load(str(FUNCTIONAL / "tpcc_sematune_dual.json"))
    assert single.llm_actor_model is None and single.llm_speculator_model is None
    assert single._explicit_dual_loop is False
    assert dual._explicit_dual_loop is True


def test_mock_replay_preserves_roles_justification_convergence_and_final(monkeypatch):
    from barebones_optimizer.benchmark import BenchmarkMetrics
    from barebones_optimizer.config import SimpleConfig
    from barebones_optimizer.tuners.llm import LLMTuner

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = SimpleConfig.load(str(FUNCTIONAL / "quick_sematune.json"))
    config.llm_replay_file = str(FUNCTIONAL / "mock_replay.json")
    quick = LLMTuner(config, agent_type="quick")
    actor = LLMTuner(config, agent_type="reasoning")
    metrics = BenchmarkMetrics()
    quick_response = quick.suggest_parameters(metrics, {}, 0)
    actor_response = actor.suggest_parameters(metrics, {}, 0)
    final_response = actor.suggest_parameters(metrics, {}, 10, final_freeze_request=True)
    assert quick_response.parameters != actor_response.parameters
    assert quick_response.justification and actor_response.justification
    assert quick_response.converged is False
    assert final_response.converged is True
    assert "freeze" in final_response.justification.lower()
    assert quick.client is None and actor.client is None


def test_tuner_package_is_lazy():
    code = """
import sys
from barebones_optimizer.tuners import FixedTuner
assert not any(name == 'torch' or name.startswith('smac') or name.startswith('chromadb') for name in sys.modules)
print(FixedTuner.__name__)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_selected_defaults_do_not_include_unrelated_controls(monkeypatch):
    from barebones_optimizer import parameter_manager

    monkeypatch.setattr(parameter_manager, "get_default_parameters", lambda: {
        "latency_ns": 24_000_000, "cstate_max": "unlimited", "busy_poll": 17,
        "scaling_governor": "powersave", "vm_swappiness": 60,
    })
    assert parameter_manager.get_selected_default_parameters(
        {"latency_ns", "cstate_max", "napi_busy_poll"}
    ) == {"latency_ns": 24_000_000, "cstate_max": "unlimited", "napi_busy_poll": 17}


def test_sysbench_credentials_stay_in_environment_and_off_command_line(monkeypatch, tmp_path):
    from barebones_optimizer.benchmarks.sysbench import SysbenchBenchmark
    from barebones_optimizer.config import SimpleConfig

    monkeypatch.setenv("OS_PARAM_TUNING_ROOT", str(ROOT))
    monkeypatch.setenv("SEMATUNE_SYSBENCH_PASSWORD", "runtime-only")
    config = SimpleConfig(benchmark="sysbench_oltp", results_dir=str(tmp_path))
    benchmark = SysbenchBenchmark(config)
    command = benchmark._build_sysbench_command(10)
    assert benchmark.password == "runtime-only"
    assert "runtime-only" not in json.dumps(config.to_dict())
    assert not any("password" in argument.lower() for argument in command)


def test_sysbench_oltp_parser_keeps_sql_rates(monkeypatch, tmp_path):
    from barebones_optimizer.benchmarks.sysbench import SysbenchBenchmark
    from barebones_optimizer.config import SimpleConfig

    monkeypatch.setenv("OS_PARAM_TUNING_ROOT", str(ROOT))
    benchmark = SysbenchBenchmark(SimpleConfig(benchmark="sysbench_oltp", results_dir=str(tmp_path)))
    output = """SQL statistics:
    transactions: 15316 (1904.55 per sec.)
    queries: 307490 (38236.57 per sec.)
General statistics:
    total time: 8.0371s
    total number of events: 15316
Latency (ms):
         min: 11.24
         avg: 20.91
         max: 256.65
         99th percentile: 49.21
         sum: 320288.37
"""
    metrics = benchmark._parse_final_summary(output)
    assert metrics.goodput == 1904.55
    assert metrics.throughput == 38236.57
    assert metrics.extra_metrics["latency_p99"] == 49.21


def write_history(path: Path, method: str, stable_value: float) -> None:
    history = [
        {"iteration": iteration, "post_tuning_phase": iteration > 10,
         "metrics": {"latency_p99": stable_value if iteration > 10 else stable_value * 1.2}}
        for iteration in range(1, 16)
    ]
    payload = {"config": {"max_iterations": 10, "post_tuning_windows": 5}, "history": history}
    if method == "sematune":
        payload["reason"] = "completed"
        filename = "dual_loop_actor_speculator_sysbench_oltp_20260101_000000.json"
    else:
        payload["terminated_reason"] = "completed"
        filename = "optimization_history_fixed_20260101_000000.json"
    (path / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_quick_plot_uses_two_methods_and_both_phases(tmp_path):
    write_history(tmp_path, "fixed", 10.0)
    write_history(tmp_path, "sematune", 8.0)
    output = tmp_path / "plots"
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "plot_quick.py"), "--results-dir", str(tmp_path), "--output-dir", str(output)],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{FUNCTIONAL}"},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = (output / "quick_tuning_vs_stable.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 5
    assert (output / "quick_tuning_vs_stable.pdf").is_file()
    assert (output / "quick_tuning_vs_stable.png").is_file()


def test_host_snapshot_rejects_non_whitelisted_paths(tmp_path):
    guard = load_module("functional_host_state_guard", FUNCTIONAL / "host_state_guard.py")
    raw = b"x\n"
    files = [{"path": str(tmp_path / "not-a-control"), "value_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}]
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"schema_version": 2, "files": files, "files_sha256": hashlib.sha256(guard.canonical(files)).hexdigest()}), encoding="utf-8")
    try:
        guard.load_snapshot(snapshot)
    except ValueError as exc:
        assert "non-whitelisted" in str(exc)
    else:
        raise AssertionError("unsafe snapshot path was accepted")


def test_recovered_twitter_histories_and_resolution():
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "functional_tool.py"), "verify-recovered"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 checksummed histories" in result.stdout


def test_tpcc_eight_method_suite_and_trace_validate_without_credentials():
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{FUNCTIONAL}"}
    env.pop("GEMINI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "tpcc_tool.py"), "validate"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 methods" in result.stdout
    suite = json.loads((FUNCTIONAL / "tpcc_suite.json").read_text())
    assert [method["id"] for method in suite["methods"]] == [
        "fixed", "mlos", "bayesian", "dqn", "qlearning",
        "sematune_single", "sematune_dual", "sematune_trim",
    ]


def test_tpcc_trim_replay_applies_recorded_search_space_actions(monkeypatch):
    from barebones_optimizer.benchmark import BenchmarkMetrics
    from barebones_optimizer.config import SimpleConfig
    from barebones_optimizer.tuners.llm_trimming import LLMTrimmingTuner

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = SimpleConfig.load(str(FUNCTIONAL / "tpcc_sematune_trim.json"))
    config.llm_replay_file = str(FUNCTIONAL / "tpcc_trace_replay.json")
    tuner = LLMTrimmingTuner(config, agent_type="single")
    before = dict(tuner.effective_ranges)
    tuner._create_update_message(
        BenchmarkMetrics(), {}, 1, 1.0,
        phase_instruction_override="continue trimming",
        final_freeze_request=False,
    )
    response = tuner.suggest_parameters(BenchmarkMetrics(), {}, 1)
    assert response.justification
    assert tuner.effective_ranges != before


def test_benchbase_runtime_credentials_only_enter_temporary_xml(monkeypatch, tmp_path):
    from barebones_optimizer.benchmarks.benchbase import BenchBaseBenchmark
    from barebones_optimizer.config import SimpleConfig
    import xml.etree.ElementTree as ET

    source = tmp_path / "tpcc.xml"
    source.write_text(
        "<parameters><url>jdbc:old</url><username>old</username>"
        "<password>old</password><work><time>5</time></work></parameters>", encoding="utf-8",
    )
    jar = tmp_path / "benchbase.jar"
    jar.write_bytes(b"placeholder")
    monkeypatch.setenv("OS_PARAM_TUNING_ROOT", str(ROOT))
    monkeypatch.setenv("SEMATUNE_BENCHBASE_HOST", "host")
    monkeypatch.setenv("SEMATUNE_BENCHBASE_PORT", "5432")
    monkeypatch.setenv("SEMATUNE_BENCHBASE_DB", "db")
    monkeypatch.setenv("SEMATUNE_BENCHBASE_USER", "runtime-user")
    monkeypatch.setenv("SEMATUNE_BENCHBASE_PASSWORD", "runtime-secret")
    config = SimpleConfig(
        benchmark="tpcc", results_dir=str(tmp_path / "results"),
        benchbase_jar_path=str(jar), benchbase_config_file=str(source),
    )
    benchmark = BenchBaseBenchmark(config)
    generated = Path(benchmark._create_temp_config(5))
    root = ET.parse(generated).getroot()
    assert root.findtext("url").startswith("jdbc:postgresql://host:5432/db?")
    assert root.findtext("username") == "runtime-user"
    assert root.findtext("password") == "runtime-secret"
    assert "runtime-secret" not in json.dumps(config.to_dict())


def test_trim_history_validator_distinguishes_token_metrics_from_dual_roles():
    tool = load_module("functional_tpcc_tool", FUNCTIONAL / "tpcc_tool.py")
    config = json.loads((FUNCTIONAL / "tpcc_sematune_trim.json").read_text())
    parameters = {
        name: ({"value": values[0], "cores": "0-9"} if name in {
            "cstate_max", "max_perf_pct", "min_perf_pct", "napi_busy_poll"
        } else values[0])
        for name, values in config["parameter_ranges"].items()
    }
    payload = {"history": [{
        "iteration": 2,
        "trimming_phase": True,
        "parameters": parameters,
        "llm_justification": "A recorded trimming decision.",
        "tuner_timing": {
            "justification": "A recorded trimming decision.",
            "token_metrics": {"input_tokens": 10, "output_tokens": 5},
        },
    }]}
    assert tool.validate_llm_trace(payload, config["parameter_ranges"]) == []
