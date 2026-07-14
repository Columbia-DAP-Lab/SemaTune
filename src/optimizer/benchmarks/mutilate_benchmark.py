#!/usr/bin/env python3
"""Distributed memcached/Mutilate benchmark implementation."""

from __future__ import annotations

import logging
import math
import os
import socket
import subprocess
import tempfile
import time
from numbers import Real
from pathlib import Path
from typing import Any, Optional

from ..benchmark import BenchmarkInterface, BenchmarkMetrics
from .mutilate_protocol import (
    JsonLineConnection,
    MSG_ACK,
    MSG_ALL_DONE,
    MSG_ERROR,
    MSG_PERF_DATA,
    MSG_READY,
    MSG_STAGE_END,
    MSG_STAGE_START,
    SYNC_PORT,
)


logger = logging.getLogger(__name__)


class ServerCoordinator:
    """Accept one known load generator and coordinate measurement stages."""

    def __init__(
        self,
        bind_host: str,
        expected_client_host: str,
        port: int = SYNC_PORT,
        accept_timeout: float = 300.0,
    ) -> None:
        self.bind_host = bind_host
        self.expected_client_host = expected_client_host
        self.port = port
        self.accept_timeout = accept_timeout
        self.sock: socket.socket | None = None
        self.client_conn: socket.socket | None = None
        self.channel: JsonLineConnection | None = None

    def _expected_addresses(self) -> set[str]:
        return {
            result[4][0]
            for result in socket.getaddrinfo(
                self.expected_client_host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }

    def start_server(self, client_config: dict[str, Any]) -> dict[str, Any]:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.bind_host, self.port))
        self.sock.listen(4)
        expected = self._expected_addresses()
        deadline = time.monotonic() + self.accept_timeout
        logger.info(
            "Waiting for Mutilate client %s on %s:%d",
            self.expected_client_host,
            self.bind_host,
            self.port,
        )

        while self.client_conn is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Mutilate client {self.expected_client_host} did not connect within "
                    f"{self.accept_timeout:.0f}s"
                )
            self.sock.settimeout(remaining)
            candidate, address = self.sock.accept()
            if address[0] not in expected:
                logger.warning("Rejecting unexpected Mutilate client from %s", address[0])
                candidate.close()
                continue
            self.client_conn = candidate
            self.client_conn.settimeout(120)
            self.channel = JsonLineConnection(self.client_conn)
            logger.info("Accepted Mutilate client from %s", address[0])

        assert self.channel is not None
        message = self.channel.receive()
        if message["type"] != MSG_READY:
            raise ValueError(f"expected {MSG_READY}, got {message['type']}")
        received = message["data"]
        runtime_config = dict(client_config)
        advertised_bin = received.get("mutilate_bin")
        if isinstance(advertised_bin, str) and advertised_bin:
            runtime_config["mutilate_bin"] = advertised_bin
        self.channel.send(MSG_ACK, runtime_config)
        return received

    def start_stage(self, stage_number: int) -> None:
        if self.channel is None:
            raise RuntimeError("Mutilate client is not connected")
        self.channel.send(MSG_STAGE_START, {"stage_num": stage_number})
        acknowledgement = self.channel.receive()
        if acknowledgement["type"] == MSG_ERROR:
            raise RuntimeError(acknowledgement["data"].get("message", "client stage error"))
        if acknowledgement["type"] != MSG_ACK:
            raise ValueError(f"expected {MSG_ACK}, got {acknowledgement['type']}")

    def end_stage(self, stage_number: int) -> dict[str, Any]:
        if self.channel is None:
            raise RuntimeError("Mutilate client is not connected")
        self.channel.send(MSG_STAGE_END, {"stage_num": stage_number})
        message = self.channel.receive()
        if message["type"] == MSG_ERROR:
            raise RuntimeError(message["data"].get("message", "client measurement error"))
        if message["type"] != MSG_PERF_DATA:
            raise ValueError(f"expected {MSG_PERF_DATA}, got {message['type']}")
        reported_stage = int(message["data"].get("stage_num", stage_number))
        if reported_stage != stage_number:
            raise ValueError(f"received samples for stage {reported_stage}, expected {stage_number}")
        return message["data"]

    def signal_all_done(self) -> None:
        if self.channel is not None:
            self.channel.send(MSG_ALL_DONE)

    def close(self) -> None:
        if self.client_conn is not None:
            self.client_conn.close()
            self.client_conn = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.channel = None


class MutilateBenchmark(BenchmarkInterface):
    """Run memcached locally while a trusted remote node runs Mutilate."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        if not config.mutilate_client_host:
            raise ValueError("mutilate_client_host must be specified in config")

        self.client_host = str(config.mutilate_client_host)
        self.target = str(config.mutilate_target)
        self.memcached_host, self.memcached_port = self._parse_target(self.target)
        self.control_port = int(getattr(config, "mutilate_control_port", SYNC_PORT))
        self.memcached_bin = str(config.mutilate_memcached_bin)
        self.client_config = {
            "threads": config.mutilate_threads,
            "clients": config.mutilate_clients,
            "qps": config.mutilate_qps,
            "iadist": config.mutilate_iadist,
            "depth": config.mutilate_depth,
            "mutilate_bin": config.mutilate_bin_path,
            "target": self.target,
        }
        self.memcached_proc: subprocess.Popen[bytes] | None = None
        self.coordinator: ServerCoordinator | None = None
        self.setup_done = False
        self.memcached_cores = self._parse_cores_spec(self.pin_to_cores) if self.pin_to_cores else None

    @staticmethod
    def _parse_target(target: str) -> tuple[str, int]:
        if target.startswith("[") and "]:" in target:
            host, port_text = target[1:].rsplit("]:", 1)
        elif ":" in target:
            host, port_text = target.rsplit(":", 1)
        else:
            host, port_text = target, "11211"
        if not host:
            raise ValueError("mutilate_target must include a server host")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"invalid Mutilate target port: {port_text}") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Mutilate target port must be between 1 and 65535")
        return host, port

    @staticmethod
    def _parse_cores_spec(cores_spec: str) -> Optional[set[int]]:
        if not cores_spec.strip():
            return None
        cores: set[int] = set()
        for part in cores_spec.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                cores.update(range(int(start), int(end) + 1))
            else:
                cores.add(int(part))
        return cores

    def _stop_existing_memcached(self, sudo_prefix: list[str]) -> None:
        for command in (("systemctl", "stop", "memcached"), ("service", "memcached", "stop")):
            try:
                subprocess.run(
                    sudo_prefix + list(command),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        subprocess.run(
            sudo_prefix + ["pkill", "-9", "memcached"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            if probe.connect_ex((self.memcached_host, self.memcached_port)) == 0:
                subprocess.run(
                    sudo_prefix + ["fuser", "-k", f"{self.memcached_port}/tcp"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1)

    def _start_memcached(self) -> None:
        sudo_prefix = [] if os.geteuid() == 0 else ["sudo"]
        self._stop_existing_memcached(sudo_prefix)
        command = [
            self.memcached_bin,
            "-u",
            "root",
            "-t",
            str(self.config.mutilate_threads),
            "-m",
            "1024",
            "-l",
            self.memcached_host,
            "-p",
            str(self.memcached_port),
        ]
        if self.memcached_cores is not None:
            command = ["taskset", "-c", self.pin_to_cores, *command]
        logger.info("Starting memcached: %s", " ".join(command))

        descriptor, stderr_name = tempfile.mkstemp(prefix="sematune-memcached-", suffix=".log")
        os.close(descriptor)
        stderr_path = Path(stderr_name)
        with stderr_path.open("wb") as stderr_handle:
            self.memcached_proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
            )
        time.sleep(2)
        if self.memcached_proc.poll() is not None:
            diagnostic = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            stderr_path.unlink(missing_ok=True)
            raise RuntimeError(f"memcached failed to start: {diagnostic or 'no diagnostic output'}")
        stderr_path.unlink(missing_ok=True)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                if probe.connect_ex((self.memcached_host, self.memcached_port)) == 0:
                    logger.info("Memcached is ready on %s", self.target)
                    return
            time.sleep(0.2)
        raise RuntimeError(f"memcached did not become reachable on {self.target}")

    def pre_execute(self) -> bool:
        if self.setup_done:
            return True
        logger.info("Starting distributed Mutilate benchmark setup")
        self._start_memcached()
        self.coordinator = ServerCoordinator(
            self.memcached_host,
            self.client_host,
            port=self.control_port,
        )
        received = self.coordinator.start_server(self.client_config)
        logger.info("Mutilate client ready: %s", received)
        self.setup_done = True
        return True

    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        if self.coordinator is None:
            raise RuntimeError("Mutilate client is not connected; call pre_execute first")
        logger.info("Executing Mutilate window %d for %ds", window_number, duration)
        window_start_time = self.start_system_measurement(window_number, duration)
        self.collect_perf_metrics(window_number, duration)
        self.coordinator.start_stage(window_number)
        time.sleep(duration)
        window_end_time = time.time()
        performance = self.coordinator.end_stage(window_number)
        samples = performance.get("samples")
        if not isinstance(samples, list):
            raise ValueError("Mutilate client returned a non-list samples field")
        failed_samples = int(performance.get("failed_samples", 0))
        logger.info(
            "Received %d Mutilate sample(s), %d client-side failures",
            len(samples),
            failed_samples,
        )
        time.sleep(2.0)
        metrics = self._aggregate_perf_samples(samples, failed_samples=failed_samples)
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        return metrics

    @staticmethod
    def _positive_number(value: Any) -> bool:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value > 0

    def _aggregate_perf_samples(
        self,
        performance_samples: list[dict[str, Any]],
        *,
        failed_samples: int = 0,
    ) -> BenchmarkMetrics:
        required = ("avg_us", "p95_us", "p99_us", "throughput")
        valid: list[dict[str, float]] = []
        invalid_count = 0
        for sample in performance_samples:
            if not isinstance(sample, dict) or any(
                not self._positive_number(sample.get(name)) for name in required
            ):
                invalid_count += 1
                continue
            valid.append({name: float(sample[name]) for name in required})
        if not valid:
            raise ValueError(
                f"no valid positive Mutilate samples received "
                f"({invalid_count + failed_samples} invalid/failed)"
            )

        def average(name: str) -> float:
            return sum(sample[name] for sample in valid) / len(valid)

        avg_us = average("avg_us")
        p95_us = average("p95_us")
        p99_us = average("p99_us")
        throughput = average("throughput")
        metrics = BenchmarkMetrics(
            throughput=throughput,
            goodput=throughput,
            latency_avg=avg_us / 1000.0,
            latency_p95=p95_us / 1000.0,
            extra_metrics={
                "latency_p99": p99_us / 1000.0,
                "num_samples": len(valid),
                "num_failed_samples": failed_samples + invalid_count,
                "avg_us": avg_us,
                "p95_us": p95_us,
                "p99_us": p99_us,
            },
        )
        logger.info(
            "Aggregated Mutilate metrics: throughput=%.1f ops/s p99=%.3fms samples=%d",
            throughput,
            p99_us / 1000.0,
            len(valid),
        )
        return metrics

    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        del output_dir
        return BenchmarkMetrics()

    def cleanup(self) -> None:
        logger.info("Cleaning up distributed Mutilate benchmark")
        if self.coordinator is not None:
            try:
                self.coordinator.signal_all_done()
            except Exception as exc:
                logger.warning("Could not signal Mutilate client completion: %s", exc)
            finally:
                self.coordinator.close()
                self.coordinator = None
        if self.memcached_proc is not None:
            try:
                self.memcached_proc.terminate()
                self.memcached_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.memcached_proc.kill()
                self.memcached_proc.wait()
            except Exception as exc:
                logger.warning("Could not stop memcached cleanly: %s", exc)
            self.memcached_proc = None
        self.setup_done = False
