#!/usr/bin/env python3
"""Restartable remote load generator for the distributed Mutilate benchmark."""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import select
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

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


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class MutilateOutputError(ValueError):
    """The load generator completed without usable performance metrics."""


def parse_mutilate_output(output: str) -> dict[str, float]:
    """Parse one standard Mutilate epoch and require real positive metrics."""

    avg_us: float | None = None
    p95_us: float | None = None
    p99_us: float | None = None
    throughput: float | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("read"):
            numbers: list[float] = []
            for token in line.split()[1:]:
                try:
                    numbers.append(float(token))
                except ValueError:
                    continue
            if len(numbers) >= 3:
                avg_us = numbers[0]
                p95_us = numbers[-2]
                p99_us = numbers[-1]
        if "Total QPS" in line:
            match = re.search(r"Total QPS\s*=\s*([0-9]+(?:\.[0-9]+)?)", line)
            if match:
                throughput = float(match.group(1))

    metrics = {
        "avg_us": avg_us,
        "p95_us": p95_us,
        "p99_us": p99_us,
        "throughput": throughput,
    }
    invalid = [
        name
        for name, value in metrics.items()
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
    ]
    if invalid:
        raise MutilateOutputError(f"missing or non-positive metrics: {', '.join(invalid)}")
    return {name: float(value) for name, value in metrics.items() if value is not None}


class MutilateClient:
    def __init__(
        self,
        server_host: str,
        *,
        client_host: str | None = None,
        control_port: int = SYNC_PORT,
        mutilate_bin: str | None = None,
        retry_seconds: float = 2.0,
    ) -> None:
        self.server_host = server_host
        self.client_host = client_host
        self.control_port = control_port
        self.requested_mutilate_bin = mutilate_bin
        self.retry_seconds = retry_seconds
        self.sock: socket.socket | None = None
        self.channel: JsonLineConnection | None = None
        self.config: dict[str, Any] = {}

    def connect(self) -> None:
        """Connect to the coordinator, retrying while the server is idle."""

        while True:
            candidate: socket.socket | None = None
            try:
                candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                candidate.settimeout(10)
                if self.client_host:
                    candidate.bind((self.client_host, 0))
                candidate.connect((self.server_host, self.control_port))
                # Provider calls can leave the control connection idle between
                # stages for several minutes; measurement exchanges themselves
                # are bounded by the server-side runner timeout.
                candidate.settimeout(900)
                self.sock = candidate
                self.channel = JsonLineConnection(candidate)
                logger.info(
                    "Connected to coordinator at %s:%d from %s",
                    self.server_host,
                    self.control_port,
                    candidate.getsockname()[0],
                )
                return
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                if candidate is not None:
                    candidate.close()
                logger.info("Coordinator unavailable (%s); retrying in %.1fs", exc, self.retry_seconds)
                time.sleep(self.retry_seconds)

    def _resolve_mutilate_bin(self, server_value: str | None = None) -> str:
        candidates = [self.requested_mutilate_bin, server_value, "mutilate"]
        for candidate in candidates:
            if not candidate:
                continue
            expanded = os.path.expanduser(candidate)
            path = Path(expanded)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
            located = shutil.which(expanded)
            if located:
                return located
        raise FileNotFoundError("Mutilate binary is missing or not executable")

    def _run_checked(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:500]
            raise RuntimeError(f"command exited {result.returncode}: {detail or 'no diagnostic output'}")
        return result

    def _load_data(self, mutilate_bin: str) -> None:
        command = [
            mutilate_bin,
            "-s",
            str(self.config["target"]),
            "--loadonly",
            "--records=10000",
        ]
        logger.info("Loading 10,000 records into %s", self.config["target"])
        self._run_checked(command, timeout=30)
        logger.info("Mutilate dataset loaded")

    def _epoch_command(self, mutilate_bin: str) -> list[str]:
        command = [
            mutilate_bin,
            "-s",
            str(self.config["target"]),
            "--noload",
            "-T",
            str(self.config["threads"]),
            "-c",
            str(self.config["clients"]),
            "-q",
            str(self.config["qps"]),
            "-d",
            str(self.config["depth"]),
            "-t",
            "1",
            "-W",
            "0",
        ]
        iadist = self.config.get("iadist")
        if iadist:
            command.extend(["--iadist", str(iadist)])
        return command

    def _run_stage(self, stage_number: int, mutilate_bin: str) -> None:
        assert self.sock is not None and self.channel is not None
        samples: list[dict[str, float]] = []
        failures = 0
        self.channel.send(MSG_ACK, {"stage_num": stage_number})
        logger.info("Starting measurement stage %d", stage_number)

        while True:
            readable, _, _ = select.select([self.sock], [], [], 0.05)
            if readable:
                message = self.channel.receive()
                if message["type"] != MSG_STAGE_END:
                    raise RuntimeError(f"expected {MSG_STAGE_END}, got {message['type']}")
                if int(message["data"].get("stage_num", stage_number)) != stage_number:
                    raise RuntimeError("stage-end number does not match active stage")
                break

            try:
                result = self._run_checked(self._epoch_command(mutilate_bin), timeout=5)
                sample = parse_mutilate_output(result.stdout)
                samples.append(sample)
                if len(samples) <= 3 or len(samples) % 5 == 0:
                    logger.info(
                        "Stage %d sample %d: p99=%.1fus throughput=%.1f ops/s",
                        stage_number,
                        len(samples),
                        sample["p99_us"],
                        sample["throughput"],
                    )
            except (OSError, RuntimeError, subprocess.TimeoutExpired, MutilateOutputError) as exc:
                failures += 1
                logger.warning("Stage %d sample failed: %s", stage_number, exc)

        if not samples:
            self.channel.send(
                MSG_ERROR,
                {
                    "stage_num": stage_number,
                    "message": f"no valid Mutilate samples ({failures} failed epochs)",
                },
            )
            raise MutilateOutputError(f"stage {stage_number} produced no valid samples")
        self.channel.send(
            MSG_PERF_DATA,
            {"stage_num": stage_number, "samples": samples, "failed_samples": failures},
        )
        logger.info("Stage %d returned %d valid sample(s), %d failed", stage_number, len(samples), failures)

    def _run_experiment(self) -> None:
        assert self.channel is not None
        advertised_bin = self._resolve_mutilate_bin()
        self.channel.send(
            MSG_READY,
            {"hostname": socket.gethostname(), "mutilate_bin": advertised_bin},
        )
        acknowledgement = self.channel.receive()
        if acknowledgement["type"] != MSG_ACK:
            raise RuntimeError(f"expected configuration ACK, got {acknowledgement['type']}")
        self.config = acknowledgement["data"]
        required = ("target", "threads", "clients", "qps", "depth")
        missing = [name for name in required if name not in self.config]
        if missing:
            raise RuntimeError(f"coordinator configuration is missing: {', '.join(missing)}")
        mutilate_bin = self._resolve_mutilate_bin(str(self.config.get("mutilate_bin") or ""))
        logger.info("Received benchmark configuration: target=%s qps=%s", self.config["target"], self.config["qps"])
        self._load_data(mutilate_bin)

        while True:
            message = self.channel.receive()
            if message["type"] == MSG_ALL_DONE:
                logger.info("Experiment complete; returning to reconnect loop")
                return
            if message["type"] != MSG_STAGE_START:
                raise RuntimeError(f"unexpected coordinator message: {message['type']}")
            stage_number = int(message["data"].get("stage_num", 0))
            self._run_stage(stage_number, mutilate_bin)

    def run_forever(self) -> None:
        while True:
            try:
                self.connect()
                self._run_experiment()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # The systemd service must survive failed experiments.
                logger.warning("Mutilate client experiment ended: %s", exc, exc_info=True)
            finally:
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                self.sock = None
                self.channel = None
            time.sleep(self.retry_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-host", required=True, help="coordinator/server IPv4 address")
    parser.add_argument("--client-host", help="local source IPv4 address for the control connection")
    parser.add_argument("--control-port", type=int, default=SYNC_PORT)
    parser.add_argument("--mutilate-bin", help="local pinned Mutilate executable")
    parser.add_argument("--retry-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.control_port <= 65535:
        parser.error("--control-port must be between 1 and 65535")
    if args.retry_seconds <= 0:
        parser.error("--retry-seconds must be positive")
    return args


def main() -> int:
    args = parse_args()
    client = MutilateClient(
        args.server_host,
        client_host=args.client_host,
        control_port=args.control_port,
        mutilate_bin=args.mutilate_bin,
        retry_seconds=args.retry_seconds,
    )
    try:
        client.run_forever()
    except KeyboardInterrupt:
        logger.info("Mutilate client interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
