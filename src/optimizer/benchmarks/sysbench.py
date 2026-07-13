#!/usr/bin/env python3
"""
Unified sysbench benchmark implementation supporting all sysbench test types.

This module implements a unified BenchmarkInterface that handles:
- OLTP benchmarks (require setup/cleanup)
- FileIO benchmarks (no setup/cleanup)
- CPU benchmarks (no setup/cleanup)
- Memory benchmarks (no setup/cleanup)
- Threads benchmarks (no setup/cleanup)
"""

import os
import subprocess
import json
import re
import time
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime
import pprint

from ..benchmark import BenchmarkInterface, BenchmarkMetrics
from .benchmark_registry import BenchmarkType, BenchmarkInfo

logger = logging.getLogger(__name__)


class SysbenchBenchmark(BenchmarkInterface):
    """Unified sysbench benchmark supporting all test types."""
    
    def __init__(self, config):
        """Initialize unified sysbench benchmark."""
        super().__init__(config)
        
        # Get benchmark info from registry
        self.benchmark_info: BenchmarkInfo = BenchmarkType.from_string(config.benchmark).value
        self.script = self.benchmark_info.script
        self.requires_setup = self.benchmark_info.requires_setup
        self.requires_cleanup = self.benchmark_info.requires_cleanup
        self.default_options = self.benchmark_info.default_options.copy()
        
        # OLTP-specific settings (only used if requires_setup is True)
        # Site-specific database settings may be supplied at runtime. In
        # particular, keeping the password out of config prevents it from being
        # copied into optimization histories saved for artifact evaluation.
        self.host = os.getenv(
            "SEMATUNE_SYSBENCH_HOST",
            str(getattr(config, 'sysbench_host', '127.0.0.1')),
        )
        self.port = int(os.getenv(
            "SEMATUNE_SYSBENCH_PORT",
            str(getattr(config, 'sysbench_port', 5432)),
        ))
        self.user = os.getenv(
            "SEMATUNE_SYSBENCH_USER",
            str(getattr(config, 'sysbench_user', 'admin')),
        )
        self.password = os.getenv(
            "SEMATUNE_SYSBENCH_PASSWORD",
            str(getattr(config, 'sysbench_password', '')),
        )
        self.db = os.getenv(
            "SEMATUNE_SYSBENCH_DB",
            str(getattr(config, 'sysbench_db', 'benchdb')),
        )
        self.tables = getattr(config, 'sysbench_tables', 4)
        self.table_size = getattr(config, 'sysbench_table_size', 100000)
        
        # Common settings
        self.threads = getattr(config, 'sysbench_threads', 16)
        self.rate = getattr(config, 'sysbench_rate', None)
        
        # CPU-specific settings
        if "cpu" in self.benchmark_info.name:
            cpu_max_prime = getattr(config, 'sysbench_cpu_max_prime', None)
            if cpu_max_prime is not None:
                self.default_options['cpu_max_prime'] = cpu_max_prime
        
        # Setup state
        self.database_setup_done = False
        
        # Create window-specific output directory
        self.window_output_dir = os.path.join(self.results_dir, f"{self.benchmark_info.name}_windows")
        os.makedirs(self.window_output_dir, exist_ok=True)
    
    def _wrap_with_taskset(self, cmd: list) -> list:
        """Wrap command with taskset.
        
        For OLTP benchmarks (those requiring setup), always use cores 10-19.
        For other benchmarks, use pin_to_cores from config if set.
        
        Args:
            cmd: Original command as list of strings
            
        Returns:
            Command wrapped with taskset if applicable
        """
        if self.requires_setup:
            # OLTP benchmarks always use cores 10-19
            return ["taskset", "-c", "10-19"] + cmd
        else:
            # Other benchmarks use pin_to_cores from config
            if self.pin_to_cores:
                return ["taskset", "-c", self.pin_to_cores] + cmd
            return cmd
    
    def pre_execute(self) -> bool:
        """Run pre-execution setup if required.
        
        Returns:
            True if setup succeeded or not needed, False otherwise
        """
        if not self.requires_setup:
            logger.info(f"{self.benchmark_info.name} does not require setup")
            return True
        
        if self.database_setup_done:
            return True
        
        logger.info(f"Running sysbench pre-execution setup for {self.benchmark_info.name}...")
        
        # Set environment
        env = os.environ.copy()
        if self.password:
            env["PGPASSWORD"] = self.password
        
        if self.requires_cleanup:
            cleanup_cmd = [
                "sysbench", self.script,
                "--db-driver=pgsql",
                f"--pgsql-host={self.host}",
                f"--pgsql-port={self.port}",
                f"--pgsql-user={self.user}",
                f"--pgsql-db={self.db}",
                f"--tables={self.tables}",
                f"--table-size={self.table_size}",
                "cleanup"
            ]
            subprocess.run(self._wrap_with_taskset(cleanup_cmd), env=env, check=False)
        
        prepare_cmd = [
            "sysbench", self.script,
            "--db-driver=pgsql",
            f"--pgsql-host={self.host}",
            f"--pgsql-port={self.port}",
            f"--pgsql-user={self.user}",
            f"--pgsql-db={self.db}",
            f"--tables={self.tables}",
            f"--table-size={self.table_size}",
            "prepare"
        ]
        
        subprocess.run(self._wrap_with_taskset(prepare_cmd), env=env, check=True)
        self.database_setup_done = True
        return True

    def cleanup(self) -> None:
        """Clean up benchmark resources.
        
        Stops any running sysbench processes and performs database cleanup if required.
        """
        # Kill any running sysbench processes started by this benchmark
        # We use pkill with -P to only kill children of this process if possible,
        # but sysbench might be detached. A safer bet for now is broadly killing sysbench
        # if we are sure we own it, or just relying on `process.terminate()` in execute_window
        # which we already do.
        
        # However, for `requires_cleanup` benchmarks (OLTP), we might want to run the cleanup command.
        if self.requires_cleanup and self.database_setup_done:
            logger.info(f"Running sysbench cleanup for {self.benchmark_info.name}...")
            
            env = os.environ.copy()
            if self.password:
                env["PGPASSWORD"] = self.password
                
            cleanup_cmd = [
                "sysbench", self.script,
                "--db-driver=pgsql",
                f"--pgsql-host={self.host}",
                f"--pgsql-port={self.port}",
                f"--pgsql-user={self.user}",
                f"--pgsql-db={self.db}",
                f"--tables={self.tables}",
                f"--table-size={self.table_size}",
                "cleanup"
            ]
            try:
                subprocess.run(self._wrap_with_taskset(cleanup_cmd), env=env, check=False)
            except Exception as e:
                logger.warning(f"Sysbench cleanup failed: {e}")
        
        # Ensure no lingering sysbench processes
        # specific to this user/environment if possible
        pass
    
    def _build_sysbench_command(self, duration: int, checkpoint_list: Optional[str] = None) -> list:
        """Build sysbench command based on benchmark type.
        
        Args:
            duration: Duration in seconds
            checkpoint_list: Optional checkpoint list string
            
        Returns:
            Command as list of strings
        """
        cmd = ["sysbench", self.script]
        
        # Add benchmark-specific options
        if self.requires_setup:
            # OLTP benchmark
            cmd.extend([
                "--db-driver=pgsql",
                f"--pgsql-host={self.host}",
                f"--pgsql-port={self.port}",
                f"--pgsql-user={self.user}",
                f"--pgsql-db={self.db}",
                f"--tables={self.tables}",
                f"--table-size={self.table_size}",
            ])
        elif "fileio" in self.benchmark_info.name:
            # FileIO benchmark
            for key, value in self.default_options.items():
                if key.startswith("file_"):
                    option_name = f"--{key.replace('_', '-')}"
                    cmd.append(f"{option_name}={value}")
        elif "memory" in self.benchmark_info.name:
            # Memory benchmark
            for key, value in self.default_options.items():
                if key.startswith("memory_"):
                    option_name = f"--{key.replace('_', '-')}"
                    cmd.append(f"{option_name}={value}")
        elif "cpu" in self.benchmark_info.name:
            # CPU benchmark
            for key, value in self.default_options.items():
                if key.startswith("cpu_"):
                    option_name = f"--{key.replace('_', '-')}"
                    cmd.append(f"{option_name}={value}")
        # Threads benchmark has no special options
        
        # Common options
        cmd.extend([
            f"--threads={self.threads}",
            f"--time={duration}",
            "--percentile=99",  # Report only p99 latency
        ])
        
        if checkpoint_list:
            cmd.append(f"--report-checkpoints={checkpoint_list}")
        
        if self.rate is not None:
            cmd.append(f"--rate={self.rate}")
        
        cmd.append("run")
        
        return cmd
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a measurement window.
        
        Args:
            window_number: Current iteration/window number
            duration: Duration of the measurement window in seconds
            use_perf: Whether to collect perf stat metrics
            
        Returns:
            Parsed metrics from the window execution
        """
        # Calculate actual benchmark runtime to align with metrics collection window
        # For long windows (> 3s): 1s delay + 1s buffer
        # For short windows (<= 3s): 0.2s delay + 0s buffer
        if duration <= 3:
            initial_delay = 0.2
            end_buffer = 0.0
        else:
            initial_delay = 1.0
            end_buffer = 1.0
        
        # Benchmark runs for: window_duration - initial_delay - end_buffer
        benchmark_duration = max(1, int(duration - initial_delay - end_buffer))
        
        logger.info(f"Executing {self.benchmark_info.name} window {window_number} "
                   f"(window: {duration}s, benchmark: {benchmark_duration}s, "
                   f"delay: {initial_delay}s, buffer: {end_buffer}s)")
        
        # Create window-specific output directory
        window_dir = os.path.join(self.window_output_dir, f"window_{window_number}")
        os.makedirs(window_dir, exist_ok=True)
        
        # Build command with adjusted duration to match metrics collection window
        cmd = self._build_sysbench_command(benchmark_duration, checkpoint_list=None)
        
        # Set environment
        env = os.environ.copy()
        if self.password and self.requires_setup:
            env["PGPASSWORD"] = self.password
        
        # Output files
        log_file = os.path.join(window_dir, "sysbench.log")
        
        # Start system metrics collection (handles its own timing)
        window_start_time = self.start_system_measurement(window_number, duration)
        
        # Wait for initial delay before starting benchmark
        # This aligns benchmark start with metrics collection start
        time.sleep(initial_delay)
        
        final_cmd = self._wrap_with_taskset(cmd)
        
        # Run in new process group so Ctrl+C (SIGINT) only goes to the optimizer, not to
        # this sysbench child. Otherwise the child gets -2 (SIGINT) and exits before
        # printing final metrics, so we get "no metrics reported".
        process = subprocess.Popen(
            final_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            # The Functional wrapper owns one killable process group and must
            # be able to terminate the benchmark before restoring host state.
            # Normal standalone runs retain the legacy isolated-child behavior.
            start_new_session=os.getenv("SEMATUNE_FUNCTIONAL_PROCESS_GROUP") != "1",
        )
        
        print(f"Running sysbench command: {' '.join(final_cmd)}")
        
        self.collect_perf_metrics(window_number, duration)
        
        # Capture all output to log file
        stderr_content = []
        with open(log_file, 'w') as log_f:
            # Read stdout and stderr in separate threads to avoid blocking
            import threading
            
            def read_stdout():
                for line in process.stdout:
                    log_f.write(line)
                    log_f.flush()
            
            def read_stderr():
                for line in process.stderr:
                    stderr_content.append(line)
                    log_f.write(line)
                    log_f.flush()
            
            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            
            stdout_thread.start()
            stderr_thread.start()
            
            stdout_thread.join()
            process.wait()
            stderr_thread.join()
        
        window_end_time = time.time()
        
        if process.returncode != 0:
            stderr_output = ''.join(stderr_content) if stderr_content else "No stderr output"
            # Also check log file for any error messages
            log_content = ""
            try:
                with open(log_file, 'r') as f:
                    log_content = f.read()
            except:
                pass
            
            error_msg = (
                f"{self.benchmark_info.name} window {window_number} failed with return code {process.returncode}\n"
                f"Command: {' '.join(final_cmd)}\n"
                f"Stderr: {stderr_output}\n"
            )
            if log_content and len(log_content) < 10000:  # Include log if reasonable size
                error_msg += f"Log file content:\n{log_content}"
            
            raise RuntimeError(error_msg)
        
        # Wait for end buffer to complete the full window duration
        if end_buffer > 0:
            logger.debug(f"Waiting {end_buffer}s for end buffer...")
            time.sleep(end_buffer)
        
        # Wait a bit more for system metrics collection thread to finish writing
        time.sleep(0.5)
        
        # Parse final summary from log file
        metrics = self.parse_results(window_dir)
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        logger.info(f"Final {self.benchmark_info.name} metrics (with system metrics):\n{pprint.pformat(metrics.__dict__, indent=2)}")
        return metrics
    
    def _parse_checkpoint_line(self, line: str, current_checkpoint: Optional[dict]) -> Optional[dict]:
        """Parse a checkpoint line from sysbench output.
        
        Returns:
            Updated checkpoint dictionary or None
        """
        # Checkpoint header: "[ 5s ] Checkpoint report:"
        cp_match = re.search(r'\[\s*(\d+)s\s*\]\s+Checkpoint report:', line)
        if cp_match:
            current_checkpoint = {"elapsed_s": int(cp_match.group(1))}
            return current_checkpoint
        
        if not current_checkpoint:
            return None
        
        # Parse based on benchmark type
        if self.requires_setup:
            # OLTP benchmarks - handle variable spacing
            # Format: "transactions:                        924    (462.05 per sec.)"
            tx_match = re.search(r'transactions:\s+(\d+)\s+\(([\d.]+)\s+per sec', line, re.I)
            if tx_match:
                current_checkpoint["tx_total"] = int(tx_match.group(1))
                current_checkpoint["tps"] = float(tx_match.group(2))
            
            # Format: "queries:                             18480  (9240.99 per sec.)"
            q_match = re.search(r'queries:\s+(\d+)\s+\(([\d.]+)\s+per sec', line, re.I)
            if q_match:
                current_checkpoint["queries_total"] = int(q_match.group(1))
                current_checkpoint["qps"] = float(q_match.group(2))
        elif "cpu" in self.benchmark_info.name:
            # CPU benchmarks
            events_match = re.search(r'events per second:\s*([\d.]+)', line, re.I)
            if events_match:
                current_checkpoint["events_per_second"] = float(events_match.group(1))
            
            events_total_match = re.search(r'total number of events:\s*(\d+)', line, re.I)
            if events_total_match:
                current_checkpoint["events_total"] = int(events_total_match.group(1))
        elif "memory" in self.benchmark_info.name:
            # Memory benchmarks
            ops_match = re.search(r'Operations performed:\s*(\d+)', line, re.I)
            if ops_match:
                current_checkpoint["operations_total"] = int(ops_match.group(1))
            
            ops_per_sec_match = re.search(r'([\d.]+)\s+ops/sec', line, re.I)
            if ops_per_sec_match:
                current_checkpoint["operations_per_second"] = float(ops_per_sec_match.group(1))
        elif "fileio" in self.benchmark_info.name:
            # FileIO benchmarks
            ops_match = re.search(r'Operations performed:\s*(\d+)', line, re.I)
            if ops_match:
                current_checkpoint["operations_total"] = int(ops_match.group(1))
            
            ops_per_sec_match = re.search(r'([\d.]+)\s+ops/sec', line, re.I)
            if ops_per_sec_match:
                current_checkpoint["operations_per_second"] = float(ops_per_sec_match.group(1))
        elif "threads" in self.benchmark_info.name:
            # Threads benchmarks
            ops_match = re.search(r'Operations performed:\s*(\d+)', line, re.I)
            if ops_match:
                current_checkpoint["operations_total"] = int(ops_match.group(1))
            
            ops_per_sec_match = re.search(r'([\d.]+)\s+ops/sec', line, re.I)
            if ops_per_sec_match:
                current_checkpoint["operations_per_second"] = float(ops_per_sec_match.group(1))
        
        # Common latency parsing
        lat_avg_match = re.search(r'avg:\s*([\d.]+)', line, re.I)
        if lat_avg_match:
            current_checkpoint["lat_avg_ms"] = float(lat_avg_match.group(1))
        
        lat_p95_match = re.search(r'95th percentile:\s*([\d.]+)', line, re.I)
        if lat_p95_match:
            current_checkpoint["lat_p95_ms"] = float(lat_p95_match.group(1))
        
        lat_p99_match = re.search(r'99th percentile:\s*([\d.]+)', line, re.I)
        if lat_p99_match:
            current_checkpoint["lat_p99_ms"] = float(lat_p99_match.group(1))
        
        return current_checkpoint
    
    def _is_checkpoint_complete(self, checkpoint: dict) -> bool:
        """Check if a checkpoint has all required fields.
        
        Args:
            checkpoint: Checkpoint dictionary to check
            
        Returns:
            True if checkpoint appears complete, False otherwise
        """
        if not checkpoint or "elapsed_s" not in checkpoint:
            return False
        
        if self.requires_setup:
            # OLTP benchmarks need: tx_total, queries_total (or qps/tps), and latency
            has_tx = "tx_total" in checkpoint or "tps" in checkpoint
            has_q = "queries_total" in checkpoint or "qps" in checkpoint
            has_latency = "lat_avg_ms" in checkpoint
            return has_tx and has_q and has_latency
        elif "cpu" in self.benchmark_info.name:
            # CPU benchmarks need: events_per_second and latency
            has_events = "events_per_second" in checkpoint
            has_latency = "lat_avg_ms" in checkpoint
            return has_events and has_latency
        else:
            # Other benchmarks need: operations_per_second and latency
            has_ops = "operations_per_second" in checkpoint
            has_latency = "lat_avg_ms" in checkpoint
            return has_ops and has_latency
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse results from final sysbench summary output.
        
        Parses the final summary section from the sysbench log file.
        
        Args:
            output_dir: Directory containing benchmark output files
            
        Returns:
            Parsed metrics from the results
        """
        log_file = os.path.join(output_dir, "sysbench.log")
        
        if not os.path.exists(log_file):
            raise FileNotFoundError(f"No sysbench log file found in {output_dir}")
        
        # Read the log file and parse the final summary
        with open(log_file, 'r') as f:
            log_content = f.read()
        
        # Parse the final summary section (last occurrence in the file)
        metrics = self._parse_final_summary(log_content)
        
        logger.debug(f"Parsed {self.benchmark_info.name} final summary metrics:\n{pprint.pformat(metrics.__dict__, indent=2)}")
        return metrics
    
    def _parse_final_summary(self, log_content: str) -> BenchmarkMetrics:
        """Parse the final summary section from sysbench output.
        
        Args:
            log_content: Full sysbench log content
            
        Returns:
            BenchmarkMetrics object with parsed values
        """
        lines = log_content.split('\n')
        
        # Find the summary section based on benchmark type. Keep these branches
        # separate: OLTP must start at "SQL statistics" so transaction/query
        # rates above "General statistics" are not discarded.
        summary_start_idx = -1
        is_cpu_benchmark = (
            self.benchmark_info.name == "sysbench_cpu"
            or self.benchmark_info.name.startswith("sysbench_cpu_")
        )
        if self.requires_setup:
            for i in range(len(lines) - 1, -1, -1):
                if "SQL statistics:" in lines[i]:
                    summary_start_idx = i
                    break
        elif is_cpu_benchmark:
            # For CPU benchmarks, find "CPU speed:" section
            for i in range(len(lines) - 1, -1, -1):
                if "CPU speed:" in lines[i]:
                    summary_start_idx = i
                    break
        else:
            for i in range(len(lines) - 1, -1, -1):
                if "General statistics:" in lines[i]:
                    summary_start_idx = i
                    break
        
        if summary_start_idx == -1:
            raise ValueError(f"Could not find summary section in sysbench output for {self.benchmark_info.name}")
        
        # Parse from the summary start to the end
        summary_lines = lines[summary_start_idx:]
        
        # Initialize parsed values
        tx_total = 0
        tps = 0.0
        queries_total = 0
        qps = 0.0
        events_total = 0
        events_per_second = 0.0
        operations_total = 0
        operations_per_second = 0.0
        latency_avg = 0.0
        latency_p95 = 0.0
        latency_p99 = 0.0
        total_time = 0.0
        
        in_latency_section = False
        
        for line in summary_lines:
            line_stripped = line.strip()
            
            # Parse transactions (OLTP benchmarks)
            if self.requires_setup:
                tx_match = re.search(r'transactions:\s+(\d+)\s+\(([\d.]+)\s+per sec\.?\)', line, re.I)
                if tx_match:
                    tx_total = int(tx_match.group(1))
                    tps = float(tx_match.group(2))
                
                q_match = re.search(r'queries:\s+(\d+)\s+\(([\d.]+)\s+per sec\.?\)', line, re.I)
                if q_match:
                    queries_total = int(q_match.group(1))
                    qps = float(q_match.group(2))
            
            # Parse events (CPU benchmarks only, not sysbench_oltp which contains "cpu")
            elif is_cpu_benchmark:
                events_match = re.search(r'events per second:\s*([\d.]+)', line, re.I)
                if events_match:
                    events_per_second = float(events_match.group(1))
                
                events_total_match = re.search(r'total number of events:\s*(\d+)', line, re.I)
                if events_total_match:
                    events_total = int(events_total_match.group(1))
            
            # Parse operations (Memory, FileIO, Threads benchmarks)
            else:
                ops_match = re.search(r'Operations performed:\s*(\d+)', line, re.I)
                if ops_match:
                    operations_total = int(ops_match.group(1))
                
                ops_per_sec_match = re.search(r'([\d.]+)\s+ops/sec', line, re.I)
                if ops_per_sec_match:
                    operations_per_second = float(ops_per_sec_match.group(1))
            
            # Parse latency section
            if "Latency (ms):" in line:
                in_latency_section = True
                continue
            
            if in_latency_section:
                # Match "avg:" with leading whitespace (indented line)
                avg_match = re.search(r'^\s+avg:\s+([\d.]+)', line)
                if avg_match:
                    latency_avg = float(avg_match.group(1))
                
                # Match "95th percentile:" with leading whitespace (indented line)
                p95_match = re.search(r'^\s+95th percentile:\s+([\d.]+)', line)
                if p95_match:
                    latency_p95 = float(p95_match.group(1))
                
                # Match "99th percentile:" with leading whitespace (indented line)
                p99_match = re.search(r'^\s+99th percentile:\s+([\d.]+)', line)
                if p99_match:
                    latency_p99 = float(p99_match.group(1))
                    # We typically find p99 after p95, so we can stop after this if we want
                    # but let's just let it parse the whole section
            
            # Parse total time
            time_match = re.search(r'total time:\s+([\d.]+)s', line, re.I)
            if time_match:
                total_time = float(time_match.group(1))
        
        # Build metrics based on benchmark type
        if self.requires_setup:
            return BenchmarkMetrics(
                throughput=qps,
                goodput=tps,
                latency_avg=latency_avg,
                latency_p95=latency_p95,
                extra_metrics={
                    "tx_total": tx_total,
                    "queries_total": queries_total,
                    "total_time_s": total_time,
                    "latency_p99": latency_p99
                }
            )
        elif "cpu" in self.benchmark_info.name:
            return BenchmarkMetrics(
                throughput=events_per_second,
                goodput=events_per_second,
                latency_avg=latency_avg,
                latency_p95=latency_p95,
                extra_metrics={
                    "events_total": events_total,
                    "total_time_s": total_time,
                    "latency_p99": latency_p99
                }
            )
        else:
            return BenchmarkMetrics(
                throughput=operations_per_second,
                goodput=operations_per_second,
                latency_avg=latency_avg,
                latency_p95=latency_p95,
                extra_metrics={
                    "operations_total": operations_total,
                    "total_time_s": total_time,
                    "latency_p99": latency_p99
                }
            )


class SysbenchContinuousBenchmark(SysbenchBenchmark):
    """Continuous sysbench benchmark that runs without restarting between windows."""
    
    def __init__(self, config):
        """Initialize continuous sysbench benchmark."""
        super().__init__(config)
        self.continuous_process = None
    
    def pre_execute(self) -> bool:
        """Start continuous sysbench process."""
        # Run database setup first
        if not super().pre_execute():
            return False
        
        logger.info(f"Starting continuous {self.benchmark_info.name} benchmark...")
        
        # Build command with very long duration and checkpoints
        duration = 3600  # 1 hour (will be stopped manually)
        checkpoint_every = 2  # Checkpoint every 2 seconds
        checkpoint_list = ",".join([str(i) for i in range(checkpoint_every, duration + 1, checkpoint_every)])
        
        cmd = self._build_sysbench_command(duration, checkpoint_list)
        
        # Set environment
        env = os.environ.copy()
        if self.password and self.requires_setup:
            env["PGPASSWORD"] = self.password
        
        # Start continuous process
        log_file = os.path.join(self.window_output_dir, "sysbench_continuous.log")
        self.continuous_process = subprocess.Popen(
            self._wrap_with_taskset(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1
        )
        
        logger.info(f"Continuous sysbench started with PID {self.continuous_process.pid}")
        return True
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a measurement window by parsing checkpoints.
        
        The benchmark is already running, so we just parse results for this window.
        """
        if not self.continuous_process:
            raise RuntimeError("Continuous process not started. Call pre_execute() first.")
        
        logger.info(f"Executing {self.benchmark_info.name} window {window_number} for {duration}s (continuous mode)")
        
        # Create window-specific output directory
        window_dir = os.path.join(self.window_output_dir, f"window_{window_number}")
        os.makedirs(window_dir, exist_ok=True)
        
        checkpoint_json = os.path.join(window_dir, "latest_checkpoint.json")
        
        window_start_time = self.start_system_measurement(window_number, duration)
        self.collect_perf_metrics(window_number, duration)
        
        # Wait for window duration
        time.sleep(duration)
        
        # Parse the latest checkpoint from the continuous log
        # The checkpoint should have been written at the end of the window
        metrics = self.parse_results(window_dir, window_number)
        
        self._populate_system_metrics(metrics, window_number, window_start_time, duration)
        
        return metrics
    
    def cleanup(self) -> None:
        """Stop the continuous benchmark process."""
        if self.continuous_process:
            logger.info("Stopping continuous sysbench process...")
            self.continuous_process.terminate()
            try:
                self.continuous_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Process didn't terminate, killing...")
                self.continuous_process.kill()
                self.continuous_process.wait()
            self.continuous_process = None
            logger.info("Continuous sysbench stopped")
