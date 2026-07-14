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


def test_null_actor_speculator_fields_remain_single_loop():
    from optimizer.config import SimpleConfig

    single = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_single.json"))
    dual = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_dual.json"))
    assert single.llm_actor_model is None and single.llm_speculator_model is None
    assert single._explicit_dual_loop is False
    assert dual._explicit_dual_loop is True


def test_recorded_replay_preserves_roles_justification_convergence_and_final(monkeypatch):
    from optimizer.benchmark import BenchmarkMetrics
    from optimizer.config import SimpleConfig
    from optimizer.tuners.llm import LLMTuner

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_dual.json"))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_dual_trace.json")
    quick = LLMTuner(config, agent_type="quick")
    actor = LLMTuner(config, agent_type="reasoning")
    metrics = BenchmarkMetrics()
    quick_response = quick.suggest_parameters(metrics, {}, 0)
    actor_response = actor.suggest_parameters(metrics, {}, 0)
    final_response = actor.suggest_parameters(metrics, {}, 5, final_freeze_request=True)
    assert quick_response.parameters != actor_response.parameters
    assert quick_response.justification and actor_response.justification
    assert quick_response.converged is False
    assert set(final_response.parameters) == set(config.parameters_to_tune)
    assert final_response.justification
    assert quick.client is None and actor.client is None
    gist, gist_raw = actor.generate_gist([{"iteration": 1, "parameters": {}, "metrics": {}, "reward": 0.0}])
    assert gist == "Optimization run completed under provider-free trace replay."
    assert gist_raw["mode"] == "trace-replay"
    assert gist_raw["provider_requests"] == 0


def test_tuner_package_is_lazy():
    code = """
import sys
from optimizer.tuners import FixedTuner
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
    from optimizer import parameter_manager

    monkeypatch.setattr(parameter_manager, "get_default_parameters", lambda: {
        "latency_ns": 24_000_000, "cstate_max": "unlimited", "busy_poll": 17,
        "scaling_governor": "powersave", "vm_swappiness": 60,
    })
    assert parameter_manager.get_selected_default_parameters(
        {"latency_ns", "cstate_max", "napi_busy_poll"}
    ) == {"latency_ns": 24_000_000, "cstate_max": "unlimited", "napi_busy_poll": 17}


def test_sysbench_credentials_stay_in_environment_and_off_command_line(monkeypatch, tmp_path):
    from optimizer.benchmarks.sysbench import SysbenchBenchmark
    from optimizer.config import SimpleConfig

    monkeypatch.setenv("OS_PARAM_TUNING_ROOT", str(ROOT))
    monkeypatch.setenv("SEMATUNE_SYSBENCH_PASSWORD", "runtime-only")
    config = SimpleConfig(benchmark="sysbench_oltp", results_dir=str(tmp_path))
    benchmark = SysbenchBenchmark(config)
    command = benchmark._build_sysbench_command(10)
    assert benchmark.password == "runtime-only"
    assert "runtime-only" not in json.dumps(config.to_dict())
    assert not any("password" in argument.lower() for argument in command)


def test_sysbench_oltp_parser_keeps_sql_rates(monkeypatch, tmp_path):
    from optimizer.benchmarks.sysbench import SysbenchBenchmark
    from optimizer.config import SimpleConfig

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


def test_sysbench_fourteen_method_suite_and_trace_validate_without_credentials():
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{FUNCTIONAL}"}
    env.pop("GEMINI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "tpcc_tool.py"), "validate"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "14 methods" in result.stdout
    suite = json.loads((FUNCTIONAL / "sysbench_suite.json").read_text())
    assert [method["id"] for method in suite["methods"]] == [
        "fixed", "mlos", "mlos_ipc", "mlos_cache", "bayesian", "dqn",
        "qlearning", "sematune_single", "sematune_dual", "sematune_system",
        "sematune_ipc", "sematune_trim", "sematune_trim_ipc",
        "sematune_trim_cache",
    ]
    assert suite["tuning_windows"] == 5
    assert suite["stable_windows"] == 5
    for method in suite["methods"]:
        config = json.loads((FUNCTIONAL / method["config"]).read_text())
        assert len(config["parameters_to_tune"]) == 8
        assert config["max_iterations"] == config["post_tuning_windows"] == 5
        if config.get("llm_actor_model") and config.get("llm_speculator_model"):
            assert config["llm_actor_model"] == "gemini-2.5-flash-lite"
            assert config["llm_speculator_model"] == "gemini-2.5-flash-lite"


def test_sysbench_preflight_warns_but_passes_without_perf(monkeypatch, capsys):
    tool = load_module("functional_tpcc_tool_without_perf", FUNCTIONAL / "tpcc_tool.py")
    monkeypatch.setattr(
        tool.shutil,
        "which",
        lambda command: None if command == "perf" else f"/usr/bin/{command}",
    )
    monkeypatch.setattr(tool.os, "sched_getaffinity", lambda _pid: set(range(20)))

    assert tool.preflight(live=False, real_llm=False) == 0
    output = capsys.readouterr().out
    assert "SYSBENCH_PREFLIGHT_WARNING: perf is unavailable" in output
    assert "SYSBENCH_PREFLIGHT: PASS" in output


def test_sysbench_fourteen_method_operational_plot(tmp_path):
    suite = json.loads((FUNCTIONAL / "sysbench_suite.json").read_text())
    methods = {}
    for index, method in enumerate(suite["methods"], start=1):
        tuning = [float(index)] * 5
        stable = [float(index) + 0.5] * 5
        history = tmp_path / f"{method['id']}.json"
        history.write_text(json.dumps({"history": []}))
        methods[method["id"]] = {
            "label": method["label"],
            "color": method["color"],
            "tuning_values": tuning,
            "stable_values": stable,
            "tuning_mean": float(index),
            "stable_mean": float(index) + 0.5,
            "tuning_std": 0.0,
            "stable_std": 0.0,
            "history_file": str(history),
        }
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "tuning_windows": 5,
        "stable_windows": 5,
        "methods": methods,
    }))
    output = tmp_path / "plots"
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "plot_tpcc_suite.py"),
         "--summary", str(summary), "--output-dir", str(output)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [sys.executable, str(FUNCTIONAL / "plot_paper_style_equivalents.py"),
         "--summary", str(summary), "--output-dir", str(output)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "validation.json").read_text())
    assert report["checks"]["methods"] == 14
    assert report["checks"]["performance_result_validation"] is False
    for figure in (6, 7, 8, 9):
        stem = output / f"functional_figure_{figure}_equivalent"
        assert stem.with_suffix(".pdf").is_file()
        assert stem.with_suffix(".png").is_file()
        assert stem.with_suffix(".csv").is_file()


def test_sysbench_trim_replay_applies_recorded_search_space_actions(monkeypatch):
    from optimizer.benchmark import BenchmarkMetrics
    from optimizer.config import SimpleConfig
    from optimizer.tuners.llm_trimming import LLMTrimmingTuner

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_trim.json"))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_trim_trace.json")
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
    from optimizer.benchmarks.benchbase import BenchBaseBenchmark
    from optimizer.config import SimpleConfig
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
    config = json.loads((FUNCTIONAL / "sysbench_sematune_trim.json").read_text())
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


def test_trim_history_validator_reports_empty_required_candidate_accurately():
    tool = load_module("functional_tpcc_tool_empty_proposal", FUNCTIONAL / "tpcc_tool.py")
    config = json.loads((FUNCTIONAL / "sysbench_sematune_trim.json").read_text())
    payload = {"history": [{
        "iteration": 2,
        "trimming_phase": True,
        "parameters": {"cstate_max": "unlimited"},
        "tuner_timing": {
            "proposed_parameters": {},
            "justification": "Only the search ranges changed.",
            "token_metrics": {"input_tokens": 10, "output_tokens": 5},
        },
    }]}
    assert tool.validate_llm_trace(payload, config["parameter_ranges"]) == [
        "trimming response did not provide the required parameter configuration"
    ]


def test_trim_history_validator_uses_recorded_active_parameters_after_elimination():
    tool = load_module("functional_tpcc_tool_eliminated", FUNCTIONAL / "tpcc_tool.py")
    config = json.loads((FUNCTIONAL / "sysbench_sematune_trim.json").read_text())
    required = [name for name in config["parameter_ranges"] if name != "cstate_max"]
    proposed = {name: config["parameter_ranges"][name][0] for name in required}
    payload = {"history": [{
        "iteration": 3,
        "trimming_phase": True,
        "tuner_timing": {
            "proposed_parameters": proposed,
            "required_parameters": required,
            "justification": "cstate_max was eliminated in the prior cycle.",
            "token_metrics": {"input_tokens": 10, "output_tokens": 5},
        },
    }]}
    assert tool.validate_llm_trace(payload, config["parameter_ranges"]) == []


def test_trim_history_validator_accepts_empty_candidate_after_all_eliminated():
    tool = load_module("functional_tpcc_tool_all_eliminated", FUNCTIONAL / "tpcc_tool.py")
    config = json.loads((FUNCTIONAL / "sysbench_sematune_trim.json").read_text())
    payload = {"history": [{
        "iteration": 4,
        "trimming_phase": True,
        "tuner_timing": {
            "proposed_parameters": {},
            "required_parameters": [],
            "justification": "All parameters were eliminated in prior cycles.",
            "token_metrics": {"input_tokens": 10, "output_tokens": 5},
        },
    }]}
    assert tool.validate_llm_trace(payload, config["parameter_ranges"]) == []


def test_trim_prompt_schema_and_runtime_require_complete_active_candidate(monkeypatch):
    from optimizer.benchmark import BenchmarkMetrics
    from optimizer.config import SimpleConfig
    from optimizer.tuners.base import TunerResponse
    from optimizer.tuners.llm import LLMTuner
    from optimizer.tuners.llm_trimming import LLMTrimmingTuner

    config = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_trim.json"))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_trim_trace.json")
    tuner = LLMTrimmingTuner(config, agent_type="single")
    tuner.replay_history_file = None

    prompt = tuner._create_base_prompt()
    assert "DO NOT retain or copy any such out-of-range value" in prompt
    schema = tuner._build_response_schema()
    assert set(config.parameter_ranges) <= set(schema["required"])
    assert {"suggested_ranges", "eliminated_params", "justification"} <= set(schema["required"])

    valid = {name: values[0] for name, values in config.parameter_ranges.items()}
    replies = iter([
        TunerResponse(parameters={}, justification="Only changed ranges."),
        TunerResponse(parameters=valid, justification="Complete retry."),
    ])
    monkeypatch.setattr(LLMTuner, "suggest_parameters", lambda self, *args, **kwargs: next(replies))
    response = tuner.suggest_parameters(BenchmarkMetrics(), {}, 1)
    assert response.parameters == valid


def test_trim_schema_preserves_integer_categories_in_41_knob_config():
    from optimizer.config import SimpleConfig
    from optimizer.tuners.llm_trimming import LLMTrimmingTuner

    config_path = (
        ROOT
        / "reproduction/configs/parameter_count/silo_hi_p99_final/41_param/"
        / "silo_hi_p99/llm_trimming.json"
    )
    config = SimpleConfig.load(str(config_path))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_trim_trace.json")
    tuner = LLMTrimmingTuner(config, agent_type="single")
    schema = tuner._build_response_schema()

    assert tuner.effective_ranges["tcp_mtu_probing"] == [0, 1, 2]
    assert schema["properties"]["tcp_mtu_probing"] == {
        "type": "integer",
        "enum": [0, 1, 2],
        "description": schema["properties"]["tcp_mtu_probing"]["description"],
    }
    range_values = schema["properties"]["suggested_ranges"]["properties"][
        "tcp_mtu_probing"
    ]["properties"]["values"]
    assert range_values["items"] == {"type": "integer", "enum": [0, 1, 2]}

    candidate = {name: allowed[0] for name, allowed in tuner.effective_ranges.items()}
    parsed, _justification, warnings = tuner._parse_structured_response(
        {
            **candidate,
            "suggested_ranges": {},
            "eliminated_params": [],
            "justification": "Complete 41-knob candidate.",
            "converged": False,
        }
    )
    assert parsed["tcp_mtu_probing"] == 0
    assert set(parsed) == set(tuner.effective_ranges)
    assert not tuner._last_candidate_parse_error
    assert not any("INVALID TRIMMING CANDIDATE" in warning for warning in warnings)

    parsed_from_provider_string, _justification, warnings = tuner._parse_structured_response(
        {
            **candidate,
            "tcp_mtu_probing": "0",
            "suggested_ranges": {},
            "eliminated_params": [],
            "justification": "Provider encoded an allowed integer category as text.",
            "converged": False,
        }
    )
    assert parsed_from_provider_string["tcp_mtu_probing"] == 0
    assert not tuner._last_candidate_parse_error
    assert not any("INVALID TRIMMING CANDIDATE" in warning for warning in warnings)


def test_trim_range_changes_are_transactional_for_invalid_candidate():
    from optimizer.config import SimpleConfig
    from optimizer.tuners.llm_trimming import LLMTrimmingTuner

    config = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_trim.json"))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_trim_trace.json")
    tuner = LLMTrimmingTuner(config, agent_type="single")
    before = dict(tuner.effective_ranges)
    parsed = {
        "suggested_ranges": {"latency_ns": {"min": 100000, "max": 200000}},
        "eliminated_params": [],
        "justification": "Narrow latency.",
        "cstate_max": "unlimited",
    }
    tuner._parse_structured_response(parsed)
    assert tuner.effective_ranges == before
    assert tuner._last_candidate_parse_error


def test_trim_runtime_fails_after_one_invalid_retry(monkeypatch):
    import pytest

    from optimizer.benchmark import BenchmarkMetrics
    from optimizer.config import SimpleConfig
    from optimizer.optimizer import _is_fatal_llm_http_error
    from optimizer.tuners.base import TunerResponse
    from optimizer.tuners.llm import LLMTuner
    from optimizer.tuners.llm_trimming import (
        InvalidTrimmingCandidateError,
        LLMTrimmingTuner,
    )

    config = SimpleConfig.load(str(FUNCTIONAL / "sysbench_sematune_trim.json"))
    config.llm_replay_file = str(FUNCTIONAL / "traces/sysbench_sematune_trim_trace.json")
    tuner = LLMTrimmingTuner(config, agent_type="single")
    tuner.replay_history_file = None
    monkeypatch.setattr(
        LLMTuner,
        "suggest_parameters",
        lambda self, *args, **kwargs: TunerResponse(parameters={}, justification="Incomplete."),
    )

    with pytest.raises(InvalidTrimmingCandidateError) as exc_info:
        tuner.suggest_parameters(BenchmarkMetrics(), {}, 1)
    assert "after 2 attempt(s)" in str(exc_info.value)
    assert _is_fatal_llm_http_error(exc_info.value)
