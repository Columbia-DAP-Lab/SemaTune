#!/usr/bin/env python3
"""
Mutilate benchmark implementation for the simplified OS tuner.

This module implements the BenchmarkInterface for distributed mutilate benchmarks
with memcached server and remote client coordination.
"""

import os
import subprocess
import json
import time
import logging
import socket
from typing import Dict, List, Optional, Any

from ..benchmark import BenchmarkInterface, BenchmarkMetrics

logger = logging.getLogger(__name__)

# Communication protocol (must match client)
SYNC_PORT = 19876
MSG_READY = "CLIENT_READY"
MSG_STAGE_PREPARE = "STAGE_PREPARE"
MSG_STAGE_START = "STAGE_START"
MSG_STAGE_END = "STAGE_END"
MSG_PERF_DATA = "PERF_DATA"
MSG_ALL_DONE = "ALL_DONE"
MSG_ACK = "ACK"


class ServerCoordinator:
    """Coordinates communication with remote mutilate client."""
    
    def __init__(self, client_host: str, port: int = SYNC_PORT):
        self.client_host = client_host
        self.port = port
        self.sock = None
        self.client_conn = None
        
    def start_server(self, client_config: Dict[str, Any]) -> Dict[str, Any]:
        """Start TCP server and wait for client connection.
        
        Args:
            client_config: Configuration to send to client
            
        Returns:
            Client configuration (may be empty if client sends its own)
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', self.port))
        self.sock.listen(1)
        self.sock.settimeout(300)  # 5 minute timeout
        
        logger.info(f"Waiting for client connection on port {self.port}...")
        self.client_conn, addr = self.sock.accept()
        self.client_conn.settimeout(60)
        logger.info(f"Client connected from {addr}")
        
        # Wait for client ready signal (includes configuration)
        msg = self._recv_msg()
        if msg['type'] != MSG_READY:
            raise ValueError(f"Expected READY, got {msg['type']}")
        received_config = msg.get('data', {})
        logger.info("Client ready!")
        
        # Send our configuration to client via ACK with config data
        if client_config:
            self._send_msg(MSG_ACK, client_config)
        else:
            self._send_msg(MSG_ACK, {})
        
        return received_config
        
    def _send_msg(self, msg_type: str, data: dict = None):
        """Send JSON message to client."""
        msg = {'type': msg_type, 'data': data or {}}
        msg_bytes = json.dumps(msg).encode() + b'\n'
        self.client_conn.sendall(msg_bytes)
        
    def _recv_msg(self) -> dict:
        """Receive JSON message from client."""
        buffer = b''
        while b'\n' not in buffer:
            chunk = self.client_conn.recv(4096)
            if not chunk:
                raise ConnectionError("Client disconnected")
            buffer += chunk
        msg_bytes, _ = buffer.split(b'\n', 1)
        return json.loads(msg_bytes.decode())
    
    def start_stage(self, stage_num: int):
        """Signal client to start measurements."""
        self._send_msg(MSG_STAGE_START, {'stage_num': stage_num})
        ack = self._recv_msg()
        if ack['type'] != MSG_ACK:
            raise ValueError(f"Expected ACK, got {ack['type']}")
    
    def end_stage(self, stage_num: int) -> List[dict]:
        """Signal client stage end and receive performance data."""
        self._send_msg(MSG_STAGE_END, {'stage_num': stage_num})
        
        # Receive performance data
        msg = self._recv_msg()
        if msg['type'] != MSG_PERF_DATA:
            raise ValueError(f"Expected PERF_DATA, got {msg['type']}")
        
        return msg['data']['samples']
    
    def signal_all_done(self):
        """Signal client that all stages complete."""
        self._send_msg(MSG_ALL_DONE, {})
    
    def close(self):
        """Close connections."""
        if self.client_conn:
            self.client_conn.close()
        if self.sock:
            self.sock.close()


class MutilateBenchmark(BenchmarkInterface):
    """Mutilate benchmark implementation for distributed load generation."""
    
    def __init__(self, config):
        super().__init__(config)
        
        # Validate mutilate configuration
        if not config.mutilate_client_host:
            raise ValueError("mutilate_client_host must be specified in config")
        
        self.client_host = config.mutilate_client_host
        self.target = config.mutilate_target
        self.memcached_bin = config.mutilate_memcached_bin
        
        # Client configuration (will be sent to client)
        self.client_config = {
            'threads': config.mutilate_threads,
            'clients': config.mutilate_clients,
            'qps': config.mutilate_qps,
            'iadist': config.mutilate_iadist,
            'depth': config.mutilate_depth,
            'mutilate_bin': config.mutilate_bin_path,
            'target': self.target
        }
        
        # State
        self.memcached_proc = None
        self.coordinator = None
        self.setup_done = False
        
        # Parse cores for memcached pinning
        self.memcached_cores = None
        if self.pin_to_cores:
            self.memcached_cores = self._parse_cores_spec(self.pin_to_cores)
    
    def _parse_cores_spec(self, cores_spec: str) -> Optional[set]:
        """Parse core specification like '10-13', '0,1,2' into set of integers."""
        if not cores_spec or cores_spec.strip() == "":
            return None
        cores = set()
        for part in cores_spec.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                cores.update(range(int(start), int(end) + 1))
            else:
                cores.add(int(part))
        return cores
    
    def pre_execute(self) -> bool:
        """Start memcached and connect to remote client."""
        if self.setup_done:
            return True
        
        logger.info("Starting mutilate benchmark setup...")
        
        # Start memcached
        logger.info("Starting memcached...")
        
        # Check if we're running as root (to avoid unnecessary sudo)
        is_root = os.geteuid() == 0
        sudo_prefix = [] if is_root else ["sudo"]
        
        # Stop any existing memcached processes
        try:
            # Try to stop system service first
            for stop_cmd in [["systemctl", "stop", "memcached"], ["service", "memcached", "stop"]]:
                try:
                    subprocess.run(
                        sudo_prefix + stop_cmd,
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            
            # Kill any remaining memcached processes
            subprocess.run(
                sudo_prefix + ["pkill", "-9", "memcached"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            
            # Check if port is still in use
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('127.0.0.1', 11211))
            sock.close()
            if result == 0:
                logger.warning("Port 11211 is still in use after cleanup attempt")
                # Try one more aggressive kill
                subprocess.run(
                    sudo_prefix + ["fuser", "-k", "11211/tcp"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                time.sleep(1)
        except Exception as e:
            logger.warning(f"Error during memcached cleanup: {e}")
        
        # Determine memcached thread count
        # User requested to use "mutilate_threads" config for memcached threads
        memcached_threads = str(self.config.mutilate_threads)
        logger.info(f"Using {memcached_threads} memcached threads (from mutilate_threads config)")
        
        # Build memcached command
        cmd = [
            self.memcached_bin,
            "-u", "root",
            "-t", memcached_threads,
            "-m", "1024",
            "-l", "0.0.0.0",
            "-p", "11211"
        ]
        
        # Capture stderr for better error messages
        stderr_file = None
        try:
            import tempfile
            stderr_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.log')
            stderr_path = stderr_file.name
            stderr_file.close()
        except Exception:
            stderr_path = subprocess.DEVNULL
        
        if self.memcached_cores is not None:
            # Use taskset to pin to cores
            taskset_cmd = ["taskset", "-c", self.pin_to_cores] + cmd
            logger.info(f"Pinning memcached to cores {self.pin_to_cores}")
            logger.info(f"Running: {' '.join(taskset_cmd)}")
            self.memcached_proc = subprocess.Popen(
                taskset_cmd,
                stdout=subprocess.DEVNULL,
                stderr=open(stderr_path, 'w') if stderr_path != subprocess.DEVNULL else subprocess.DEVNULL
            )
        else:
            logger.info(f"Running: {' '.join(cmd)}")
            self.memcached_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=open(stderr_path, 'w') if stderr_path != subprocess.DEVNULL else subprocess.DEVNULL
            )
        
        # Wait for memcached to start
        time.sleep(2)
        
        if self.memcached_proc.poll() is not None:
            error_msg = "Memcached failed to start"
            if stderr_path != subprocess.DEVNULL and os.path.exists(stderr_path):
                try:
                    with open(stderr_path, 'r') as f:
                        stderr_content = f.read().strip()
                        if stderr_content:
                            error_msg += f": {stderr_content}"
                    os.unlink(stderr_path)
                except Exception:
                    pass
            raise RuntimeError(error_msg)
        
        # Warm up period
        logger.info("Memcached started, warming up for 3 seconds...")
        time.sleep(3)
        logger.info("Memcached ready!")
        
        # Pin NIC IRQs to socket 0 cores (if possible)
        socket0_cpulist_path = "/sys/devices/system/node/node0/cpulist"
        if os.path.exists(socket0_cpulist_path):
            with open(socket0_cpulist_path, "r") as f:
                socket0_cpulist = f.read().strip()
            import sys
            orchestrator_path = os.path.join(self.repo_root, "mutilate", "orchestrator.py")
            if os.path.exists(orchestrator_path):
                sys.path.insert(0, os.path.dirname(orchestrator_path))
                from orchestrator import bind_nic_irqs_to_cores
                bound = bind_nic_irqs_to_cores(socket0_cpulist)
                if bound > 0:
                    logger.info(f"Pinned {bound} NIC IRQ(s) to socket 0 cores: {socket0_cpulist}")
        
        self.coordinator = ServerCoordinator(self.client_host)
        client_received_config = self.coordinator.start_server(self.client_config)
        logger.info(f"Client configuration received: {client_received_config}")
        self.setup_done = True
        return True
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a measurement window.
        
        Args:
            window_number: Current iteration/window number
            duration: Duration of the measurement window in seconds
            
        Returns:
            Parsed metrics from the window execution
        """
        logger.info(f"Executing mutilate window {window_number} for {duration}s")
        
        if not self.coordinator:
            raise RuntimeError("Client not connected. Call pre_execute() first.")
        
        window_start_time = self.start_system_measurement(window_number, duration)
        self.collect_perf_metrics(window_number, duration)
        
        logger.info(f"Signaling client to start window {window_number}...")
        self.coordinator.start_stage(window_number)
        
        time.sleep(duration)
        window_end_time = time.time()
        
        logger.info(f"Signaling client to end window {window_number}...")
        perf_samples = self.coordinator.end_stage(window_number)
        logger.info(f"Received {len(perf_samples)} performance samples from client")
        
        time.sleep(2.0)
        
        metrics = self._aggregate_perf_samples(perf_samples)
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        return metrics
    
    def _aggregate_perf_samples(self, perf_samples: List[Dict[str, Any]]) -> BenchmarkMetrics:
        """Aggregate performance samples from client into BenchmarkMetrics.
        
        Args:
            perf_samples: List of performance sample dicts from client
            
        Returns:
            BenchmarkMetrics with aggregated values
        """
        if not perf_samples:
            raise ValueError("No performance samples received from client")
        
        # Aggregate metrics
        avg_us_list = []
        p95_us_list = []
        p99_us_list = []
        throughput_list = []
        
        for sample in perf_samples:
            if sample.get('avg_us') is not None:
                avg_us_list.append(sample['avg_us'])
            if sample.get('p95_us') is not None:
                p95_us_list.append(sample['p95_us'])
            if sample.get('p99_us') is not None:
                p99_us_list.append(sample['p99_us'])
            if sample.get('throughput') is not None:
                throughput_list.append(sample['throughput'])
        
        # Calculate averages
        latency_avg_ms = sum(avg_us_list) / len(avg_us_list) / 1000.0 if avg_us_list else 0.0
        latency_p95_ms = sum(p95_us_list) / len(p95_us_list) / 1000.0 if p95_us_list else 0.0
        latency_p99_ms = sum(p99_us_list) / len(p99_us_list) / 1000.0 if p99_us_list else 0.0
        throughput = sum(throughput_list) / len(throughput_list) if throughput_list else 0.0
        goodput = throughput  # Assume all requests are successful
        
        # Create BenchmarkMetrics
        metrics = BenchmarkMetrics(
            throughput=throughput,
            goodput=goodput,
            latency_avg=latency_avg_ms,
            latency_p95=latency_p95_ms,
            extra_metrics={
                "latency_p99": latency_p99_ms,
                "num_samples": len(perf_samples),
                "avg_us": sum(avg_us_list) / len(avg_us_list) if avg_us_list else None,
                "p95_us": sum(p95_us_list) / len(p95_us_list) if p95_us_list else None,
                "p99_us": sum(p99_us_list) / len(p99_us_list) if p99_us_list else None,
            }
        )
        
        logger.info(f"Aggregated mutilate metrics: throughput={throughput:.2f}, "
                   f"latency_avg={latency_avg_ms:.2f}ms, latency_p95={latency_p95_ms:.2f}ms")
        
        return metrics
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse results from output directory.
        
        Note: For mutilate, results come from client via network, not from files.
        This method is kept for interface compliance but returns empty metrics.
        
        Args:
            output_dir: Directory containing benchmark output files (not used)
            
        Returns:
            Empty BenchmarkMetrics (actual parsing happens in execute_window)
        """
        # Results are parsed directly from client network messages
        return BenchmarkMetrics()
    
    def cleanup(self) -> None:
        """Cleanup: stop memcached and disconnect from client."""
        logger.info("Cleaning up mutilate benchmark...")
        
        # Signal client all done
        if self.coordinator:
            try:
                self.coordinator.signal_all_done()
                self.coordinator.close()
            except Exception as e:
                logger.warning(f"Error closing client connection: {e}")
            self.coordinator = None
        
        # Stop memcached
        if self.memcached_proc:
            logger.info("Stopping memcached...")
            try:
                self.memcached_proc.terminate()
                self.memcached_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.memcached_proc.kill()
                self.memcached_proc.wait()
            except Exception as e:
                logger.warning(f"Error stopping memcached: {e}")
            
            # Also kill any remaining memcached processes
            try:
                subprocess.run(
                    ["pkill", "-9", "memcached"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
            logger.info("Memcached stopped")
            self.memcached_proc = None
        
        self.setup_done = False
        logger.info("Mutilate benchmark cleanup complete")

