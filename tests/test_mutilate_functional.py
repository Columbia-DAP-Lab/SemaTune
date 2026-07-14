import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from optimizer.benchmarks.mutilate_client import MutilateOutputError, parse_mutilate_output
from optimizer.benchmarks.mutilate_benchmark import MutilateBenchmark
from optimizer.benchmarks.mutilate_protocol import JsonLineConnection, MSG_ACK, MSG_READY


def load_tool():
    path = ROOT / "functional_example" / "mutilate_tool.py"
    spec = importlib.util.spec_from_file_location("mutilate_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mutilate_parser_accepts_standard_positive_output():
    output = """
    #type       avg     std     min     5th    10th    90th    95th    99th
    read       70.0    52.7    25.5    40.5    43.1    99.5   120.8   229.1
    update      0.0     0.0     0.0     0.0     0.0     0.0     0.0
    Total QPS = 99708.0 (199416 / 2.0s)
    """
    assert parse_mutilate_output(output) == {
        "avg_us": 70.0,
        "p95_us": 120.8,
        "p99_us": 229.1,
        "throughput": 99708.0,
    }


@pytest.mark.parametrize(
    "output",
    [
        "read 1 1 1 1 1 1 1 1\nTotal QPS = 0.0 (0 / 1.0s)",
        "Total QPS = 1000.0 (1000 / 1.0s)",
        "read 0 0 0 0 0 0 0 0\nTotal QPS = 1000.0 (1000 / 1.0s)",
    ],
)
def test_mutilate_parser_rejects_empty_or_zero_metrics(output):
    with pytest.raises(MutilateOutputError):
        parse_mutilate_output(output)


def test_json_line_protocol_preserves_coalesced_messages():
    left, right = socket.socketpair()
    try:
        channel = JsonLineConnection(left)
        right.sendall(
            b'{"type":"CLIENT_READY","data":{"hostname":"loadgen"}}\n'
            b'{"type":"ACK","data":{"stage_num":1}}\n'
        )
        assert channel.receive() == {
            "type": MSG_READY,
            "data": {"hostname": "loadgen"},
        }
        assert channel.receive() == {"type": MSG_ACK, "data": {"stage_num": 1}}
    finally:
        left.close()
        right.close()


def test_server_aggregation_rejects_zero_only_samples():
    benchmark = object.__new__(MutilateBenchmark)
    with pytest.raises(ValueError, match="no valid positive Mutilate samples"):
        benchmark._aggregate_perf_samples(
            [{"avg_us": 0, "p95_us": 0, "p99_us": 0, "throughput": 0}]
        )


def test_server_aggregation_keeps_only_complete_positive_samples():
    benchmark = object.__new__(MutilateBenchmark)
    metrics = benchmark._aggregate_perf_samples(
        [
            {"avg_us": 200, "p95_us": 500, "p99_us": 1000, "throughput": 400000},
            {"avg_us": 0, "p95_us": 0, "p99_us": 0, "throughput": 0},
        ],
        failed_samples=1,
    )
    assert metrics.throughput == 400000
    assert metrics.latency_avg == 0.2
    assert metrics.extra_metrics["latency_p99"] == 1.0
    assert metrics.extra_metrics["num_samples"] == 1
    assert metrics.extra_metrics["num_failed_samples"] == 2


def test_materialized_functional_config_uses_supplied_addresses(tmp_path):
    tool = load_tool()
    output = tmp_path / "config.json"
    results = tmp_path / "raw"
    tool.materialize(output, results, "10.1.2.3", "10.1.2.4", 21987)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mutilate_target"] == "10.1.2.3:11211"
    assert payload["mutilate_client_host"] == "10.1.2.4"
    assert payload["mutilate_control_port"] == 21987
    assert payload["mutilate_bin_path"] == "deps/mutilate/mutilate"
    assert payload["max_iterations"] == 3
    assert payload["post_tuning_windows"] == 2
    assert payload["window_duration"] == 5
    assert payload["llm_actor_model"] == "gemini-2.5-flash-lite"
    assert payload["llm_speculator_model"] == "gemini-2.5-flash-lite"
    assert payload["llm_replay_file"] is None
    assert payload["llm_api_key"] is None


def fake_history(tool, *, zero_iteration=None):
    rows = []
    for iteration in tool.EXPECTED_ITERATIONS:
        value = 0.0 if iteration == zero_iteration else 1.0
        row = {
            "iteration": iteration,
            "metrics": {
                "throughput": 500000.0 * value,
                "goodput": 500000.0 * value,
                "latency_avg": 0.2 * value,
                "latency_p95": 0.5 * value,
                "latency_p99": 1.2 * value,
                "num_samples": 4,
                "num_failed_samples": 0,
            },
        }
        if iteration == 1:
            row["tuner_timing"] = {
                "reasoning": {
                    "tuner_type": "actor_reasoning",
                    "token_metrics": {"api_total_tokens": 123},
                },
                "quick": {
                    "tuner_type": "speculator_quick",
                    "token_metrics": {"api_total_tokens": 45},
                },
            }
        rows.append(row)
    return {
        "reason": "completed",
        "config": {
            "benchmark": "mutilate",
            "max_iterations": 3,
            "post_tuning_windows": 2,
            "window_duration": 5,
            "llm_actor_model": "gemini-2.5-flash-lite",
            "llm_speculator_model": "gemini-2.5-flash-lite",
            "llm_replay_file": None,
        },
        "history": rows,
    }


def write_fake_result(tool, output_dir, payload):
    raw = output_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "dual_loop_actor_speculator_mutilate_20300101_000000.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (output_dir / "restoration_report.json").write_text(
        json.dumps({"restore_status": "PASS", "verify_status": "PASS", "byte_mismatches": []}),
        encoding="utf-8",
    )


def test_functional_result_validator_requires_real_positive_windows(tmp_path):
    tool = load_tool()
    write_fake_result(tool, tmp_path, fake_history(tool))
    report = tool.validate_result(tmp_path, write_summary=True)
    assert report["status"] == "PASS"
    assert len(report["windows"]) == 6
    assert report["api_roles_verified"] == ["actor", "speculator"]
    assert (tmp_path / "mutilate_summary.json").is_file()
    assert (tmp_path / "mutilate_summary.csv").is_file()


def test_functional_result_validator_rejects_zero_window(tmp_path):
    tool = load_tool()
    write_fake_result(tool, tmp_path, fake_history(tool, zero_iteration=3))
    with pytest.raises(ValueError, match="iteration 3 has invalid metrics"):
        tool.validate_result(tmp_path, write_summary=False)


def test_setup_modes_are_explicit_and_do_not_hardcode_client_address():
    setup = ROOT / "scripts" / "setup.sh"
    wrapper = ROOT / "scripts" / "run_mutilate_client.sh"
    client = ROOT / "src" / "optimizer" / "benchmarks" / "mutilate_client.py"
    source = setup.read_text(encoding="utf-8")
    assert setup.stat().st_mode & stat.S_IXUSR
    assert wrapper.stat().st_mode & stat.S_IXUSR
    assert "--memcached-server" in source
    assert "--memcached-client" in source
    assert "--server-ip" in source and "--client-ip" in source
    assert "sematune-mutilate-client.service" in source
    assert "perf --version" in source
    assert "python3.10 -m lib2to3" in source
    assert 'build_dir="$(mktemp -d)"' in source
    assert "10.10." not in client.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (["--memcached-server"], "requires --server-ip and --client-ip"),
        (
            ["--base", "--server-ip", "10.10.1.2", "--client-ip", "10.10.1.3"],
            "--base does not configure Mutilate",
        ),
        (["--base", "--full"], "Choose exactly one setup mode"),
    ],
)
def test_setup_rejects_invalid_mode_combinations_before_mutation(arguments, expected):
    result = subprocess.run(
        [str(ROOT / "scripts" / "setup.sh"), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert expected in result.stderr
