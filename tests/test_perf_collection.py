from __future__ import annotations

from types import SimpleNamespace

import pytest

from optimizer.benchmark import BenchmarkInterface, BenchmarkMetrics, PERF_EVENTS


class ToyBenchmark(BenchmarkInterface):
    def cleanup(self) -> None:
        pass

    def pre_execute(self) -> bool:
        return True

    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        return BenchmarkMetrics()

    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        return BenchmarkMetrics()


class FinishedProcess:
    returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def make_benchmark(tmp_path, metric: str = "latency_p99") -> ToyBenchmark:
    config = SimpleNamespace(
        results_dir=str(tmp_path),
        pin_to_cores="0-1",
        optimization_metric=metric,
    )
    return ToyBenchmark(config)


def test_perf_command_explicitly_requests_consumed_events(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FinishedProcess()

    monkeypatch.setenv("OS_PARAM_TUNING_ROOT", str(tmp_path))
    monkeypatch.setattr("optimizer.benchmark.subprocess.Popen", fake_popen)
    benchmark = make_benchmark(tmp_path)
    perf_info = benchmark.collect_perf_metrics(window_number=1, duration=5)
    perf_info["perf_output_handle"].close()

    wrapper = captured["command"][2]
    assert f"-e {','.join(PERF_EVENTS)}" in wrapper
    assert "cache-references,cache-misses" in wrapper


def test_toy_perf_output_parses_cache_misses_and_derives_ipc(tmp_path) -> None:
    benchmark = make_benchmark(tmp_path, metric="cache_misses")
    output = tmp_path / "window_1_perf_stat.txt"
    output.write_text(
        "1,000 cycles\n"
        "500 instructions\n"
        "200 cache-references\n"
        "25 cache-misses\n",
        encoding="utf-8",
    )
    output_handle = output.open("a", encoding="utf-8")

    benchmark.finalize_perf_metrics(
        1,
        {
            "perf_process": FinishedProcess(),
            "perf_output_handle": output_handle,
            "perf_output_file": str(output),
            "perf_start_time": 1.0,
            "perf_end_time": 2.0,
            "perf_duration": 1,
        },
    )

    metrics = benchmark._parse_perf_stat_output(str(output))
    assert metrics["cache_misses"] == 25
    assert metrics["instructions_per_cycle"] == 0.5
    assert (tmp_path / "window_1_perf_info.json").is_file()


def test_required_cache_metric_fails_on_first_missing_window(tmp_path) -> None:
    benchmark = make_benchmark(tmp_path, metric="cache_misses")
    output = tmp_path / "window_1_perf_stat.txt"
    output.write_text("1,000 cycles\n500 instructions\n", encoding="utf-8")
    output_handle = output.open("a", encoding="utf-8")

    with pytest.raises(RuntimeError, match="required optimization metric 'cache_misses'"):
        benchmark.finalize_perf_metrics(
            1,
            {
                "perf_process": FinishedProcess(),
                "perf_output_handle": output_handle,
                "perf_output_file": str(output),
                "perf_start_time": 1.0,
                "perf_end_time": 2.0,
                "perf_duration": 1,
            },
        )
