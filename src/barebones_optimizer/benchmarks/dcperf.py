#!/usr/bin/env python3
"""
DCPerf benchmark implementation for the OS parameter tuner.

This module implements BenchmarkInterface for DCPerf benchmarks (SparkBench, etc.).
DCPerf benchmarks are end-to-end: they run to completion rather than for a fixed
time window. The execute_window method waits for the benchmark process to terminate
and then parses the results from stdout.

Designed for extensibility — new DCPerf benchmarks can subclass DCPerfBenchmark
and override _get_run_command() and _parse_stdout().
"""

import os
import re
import subprocess
import time
import threading
import logging
from typing import Optional, Dict, Any, List

from ..benchmark import BenchmarkInterface, BenchmarkMetrics, get_repo_root

logger = logging.getLogger(__name__)


class DCPerfBenchmark(BenchmarkInterface):
    """Base class for DCPerf benchmarks.
    
    DCPerf benchmarks run to completion (no time-based windows). The optimizer
    calls execute_window() which:
    1. Runs the benchpress_cli.py command
    2. Waits for the process to exit
    3. Parses stdout for metrics
    
    Config fields:
        dcperf_path: Path to DCPerf directory (default: deps/DCPerf)
        dcperf_worker_cores: Number of Spark worker cores (default: auto-detect)
        pin_to_cores: Core pinning spec for taskset (e.g., "0-9")
        dcperf_benchmark_name: Which benchpress benchmark to run (e.g., "spark_standalone_local")
        dcperf_max_runtime: Maximum runtime in seconds before killing (default: 3600)
    """
    
    # Subclasses should set this
    BENCHMARK_NAME = None  # e.g., "spark_standalone_local"
    
    def __init__(self, config):
        super().__init__(config)
        
        self.repo_root = get_repo_root()
        
        # DCPerf installation path
        self.dcperf_path = getattr(config, 'dcperf_path', None)
        if not self.dcperf_path:
            self.dcperf_path = os.path.join(self.repo_root, 'deps', 'DCPerf')
        elif not os.path.isabs(self.dcperf_path):
            self.dcperf_path = os.path.join(self.repo_root, self.dcperf_path)
        
        # Worker cores (for Spark-like benchmarks; None = auto-detect)
        self.worker_cores = getattr(config, 'dcperf_worker_cores', None)
        
        # Maximum runtime before we kill the process (safety net)
        self.max_runtime = getattr(config, 'dcperf_max_runtime', 3600)
        
        # Benchmark name (from config or class default)
        self.benchmark_name = getattr(config, 'dcperf_benchmark_name', None) or self.BENCHMARK_NAME
        if not self.benchmark_name:
            raise ValueError("dcperf_benchmark_name must be set in config or BENCHMARK_NAME in subclass")
        
        # Estimated duration for system metrics collection (set high; daemon thread stops on its own)
        self._estimated_duration = 600  # 10 minutes default, will be overridden per run
        
        logger.info(f"DCPerf benchmark initialized: {self.benchmark_name}")
        logger.info(f"  DCPerf path: {self.dcperf_path}")
        logger.info(f"  Worker cores: {self.worker_cores or 'auto-detect'}")
        logger.info(f"  Pin to cores: {self.pin_to_cores or 'none'}")
        logger.info(f"  Max runtime: {self.max_runtime}s")
    
    def pre_execute(self) -> bool:
        """Verify DCPerf is installed. Setup assumed done per walkthrough."""
        if not os.path.isdir(self.dcperf_path):
            logger.error(f"DCPerf directory not found: {self.dcperf_path}")
            return False
        
        # Check for benchpress_cli.py or benchpress module
        benchpress_cli = os.path.join(self.dcperf_path, 'benchpress_cli.py')
        benchpress_module = os.path.join(self.dcperf_path, 'benchpress', 'cli', 'main.py')
        
        if os.path.exists(benchpress_cli):
            self._use_cli_script = True
            logger.info(f"DCPerf using benchpress_cli.py at {benchpress_cli}")
        elif os.path.exists(benchpress_module):
            self._use_cli_script = False
            logger.info(f"DCPerf using benchpress.cli.main module at {benchpress_module}")
        else:
            logger.error(f"Neither benchpress_cli.py nor benchpress/cli/main.py found in {self.dcperf_path}")
            return False
        
        # Ensure benchmark_installs.txt marker file exists.
        # Benchpress's verify_install() checks this file for the benchmark's
        # install_script path. If missing, the run command silently skips the job.
        self._ensure_install_marker()
        
        logger.info(f"DCPerf installation verified at {self.dcperf_path}")
        return True
    
    def _ensure_install_marker(self) -> None:
        """Ensure benchmark_installs.txt contains the install script for our benchmark.
        
        Benchpress checks this file before running. It's normally created by
        `benchpress_cli.py install`, but may be missing if the benchmark was
        installed manually or the file was lost.
        """
        # Read install_script from benchmarks.yml
        benchmarks_yml = os.path.join(self.dcperf_path, 'benchpress', 'config', 'benchmarks.yml')
        install_script = None
        
        if os.path.exists(benchmarks_yml):
            try:
                import yaml
                with open(benchmarks_yml, 'r') as f:
                    benchmarks = yaml.safe_load(f)
                
                # Find the benchmark spec that matches our benchmark_name
                # The job references a benchmark name (e.g., "spark_standalone")
                # We need to find the parent benchmark, not the job name
                for bm_name, bm_spec in benchmarks.items():
                    if isinstance(bm_spec, dict) and 'install_script' in bm_spec:
                        install_script_candidate = bm_spec['install_script']
                        # Check if this benchmark's name matches any part of our job name
                        if bm_name in self.benchmark_name or self.benchmark_name.startswith(bm_name):
                            install_script = install_script_candidate
                            break
            except Exception as e:
                logger.warning(f"Could not read benchmarks.yml: {e}")
        
        if not install_script:
            logger.warning("Could not determine install_script from benchmarks.yml, "
                          "benchmark may fail verify_install check")
            return
        
        # Check/create benchmark_installs.txt in DCPerf directory
        marker_path = os.path.join(self.dcperf_path, 'benchmark_installs.txt')
        
        # Check if already present
        if os.path.exists(marker_path):
            with open(marker_path, 'r') as f:
                for line in f:
                    if line.strip() == install_script.strip():
                        logger.info(f"Install marker already present for {install_script}")
                        return
        
        # Append the install script to the marker file
        logger.info(f"Creating install marker for {install_script} in {marker_path}")
        try:
            with open(marker_path, 'a') as f:
                f.write(install_script + '\n')
        except PermissionError:
            import tempfile
            # Handle root-owned directory
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
                # Read existing content if any
                if os.path.exists(marker_path):
                    with open(marker_path, 'r') as existing:
                        tmp.write(existing.read())
                tmp.write(install_script + '\n')
                tmp_path = tmp.name
            try:
                subprocess.run(['sudo', 'cp', tmp_path, marker_path], check=True, timeout=10)
                subprocess.run(['sudo', 'chmod', '644', marker_path], check=True, timeout=5)
            finally:
                os.unlink(tmp_path)
    
    def cleanup(self) -> None:
        """Clean up any remaining benchmark processes."""
        # Kill any lingering benchpress/spark/java processes from previous runs
        try:
            subprocess.run(
                ["pkill", "-f", "benchpress_cli.py"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
    
    def _get_run_command(self) -> List[str]:
        """Build the benchpress run command.
        
        Uses benchpress_cli.py if available, otherwise falls back to
        python3 -c with explicit main() call (the benchpress module has no
        __name__ == '__main__' guard so python3 -m doesn't work).
        
        Returns:
            Command as list of strings
        """
        if getattr(self, '_use_cli_script', True):
            benchpress_cli = os.path.join(self.dcperf_path, 'benchpress_cli.py')
            if os.path.exists(benchpress_cli):
                cmd = [benchpress_cli, "run", self.benchmark_name]
                return cmd
        
        # Fallback: use python3 -c with explicit main() call
        # python3 -m benchpress.cli.main does NOT work because there's no
        # if __name__ == "__main__" block in the module
        python_code = f"from benchpress.cli.main import main; main(['run', '{self.benchmark_name}'])"
        cmd = ["python3", "-c", python_code]
        return cmd
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a DCPerf benchmark run to completion.
        
        The `duration` parameter is IGNORED for DCPerf benchmarks — the benchmark
        runs until the process exits. System metrics are collected for the entire
        runtime of the benchmark.
        
        Args:
            window_number: Current iteration/window number
            duration: Ignored for DCPerf (run-to-completion)
            
        Returns:
            Parsed metrics from the benchmark run
        """
        logger.info(f"=== DCPerf {self.benchmark_name} window {window_number} (run-to-completion) ===")
        
        # Build command
        cmd = self._get_run_command()
        
        # Wrap with taskset if CPU pinning is configured
        final_cmd = self._wrap_with_taskset(cmd)
        
        # DCPerf typically requires sudo
        if os.geteuid() != 0:
            final_cmd = ["sudo"] + final_cmd
        
        logger.info(f"Running: {' '.join(final_cmd)}")
        print(f"Running DCPerf command: {' '.join(final_cmd)}")
        
        # Record start time
        window_start_time = time.time()
        
        # Start system metrics collection with estimated duration
        # (daemon thread will run until process exits or estimated duration elapsed)
        actual_window_start = self.start_system_measurement(window_number, self.max_runtime)
        
        # Start perf stat with estimated duration
        perf_info = self.collect_perf_metrics(window_number, self.max_runtime)
        
        # Start the benchmark process
        process = subprocess.Popen(
            final_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Combine stderr into stdout
            text=True,
            cwd=self.dcperf_path,
            bufsize=1,
            universal_newlines=True
        )
        
        # Stream and capture output
        output_lines = []
        def stream_output():
            try:
                for line in process.stdout:
                    print(f"[DCPerf] {line}", end='', flush=True)
                    output_lines.append(line)
            except Exception:
                pass
        
        output_thread = threading.Thread(target=stream_output, daemon=True)
        output_thread.start()
        
        # Wait for process to complete (with max_runtime safety net)
        start_time = time.time()
        while True:
            if process.poll() is not None:
                break
            
            elapsed = time.time() - start_time
            if elapsed > self.max_runtime:
                logger.warning(f"DCPerf benchmark exceeded max_runtime ({self.max_runtime}s), killing...")
                process.terminate()
                time.sleep(5)
                if process.poll() is None:
                    process.kill()
                break
            
            time.sleep(1.0)
        
        # Wait for output thread to finish collecting remaining output
        output_thread.join(timeout=5)
        
        window_end_time = time.time()
        run_duration = window_end_time - window_start_time
        logger.info(f"DCPerf benchmark completed in {run_duration:.1f}s (exit code: {process.returncode})")
        
        # Stop perf stat if still running
        if perf_info and 'perf_process' in perf_info:
            perf_process = perf_info['perf_process']
            perf_output_handle = perf_info.get('perf_output_handle')
            try:
                perf_process.terminate()
                perf_process.wait(timeout=5)
            except Exception:
                try:
                    perf_process.kill()
                    perf_process.wait(timeout=2)
                except Exception:
                    pass
            if perf_output_handle:
                try:
                    perf_output_handle.close()
                except Exception:
                    pass
        
        if process.returncode != 0:
            logger.error(f"DCPerf benchmark failed with exit code {process.returncode}")
            # Still try to parse any output we got
        
        # Parse output
        stdout_text = ''.join(output_lines)
        metrics = self._parse_stdout(stdout_text, run_duration)
        
        # Populate system metrics (power, C-state, CPU utilization, perf)
        self._populate_system_metrics(metrics, window_number, actual_window_start, window_end_time)
        
        return metrics
    
    def _parse_stdout(self, stdout: str, run_duration: float) -> BenchmarkMetrics:
        """Parse benchmark output from stdout.
        
        Subclasses should override this to extract benchmark-specific metrics.
        
        Args:
            stdout: Complete stdout from the benchmark process
            run_duration: How long the benchmark took in seconds
            
        Returns:
            BenchmarkMetrics with parsed values
        """
        raise NotImplementedError("Subclasses must implement _parse_stdout")
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse results from output directory.
        
        For DCPerf, results are parsed from stdout in execute_window, not from files.
        This method is kept for interface compatibility.
        """
        return BenchmarkMetrics()


class DCPerfSparkBenchmark(DCPerfBenchmark):
    """DCPerf SparkBench benchmark implementation.
    
    Runs SparkBench (spark_standalone_local) and parses stdout for:
    - queries_per_hour: Primary throughput metric (higher is better)
    - execution_time_test_93586: Query execution time (lower is better) 
    - score: Normalized performance score
    - worker_cores: Number of Spark worker cores used
    """
    
    BENCHMARK_NAME = "spark_standalone_local"
    
    def __init__(self, config):
        super().__init__(config)
        
        # If worker cores are specified, we need to modify the Spark runner
        if self.worker_cores:
            self._configure_worker_cores(self.worker_cores)
    
    def _configure_worker_cores(self, worker_cores: int) -> None:
        """Configure SparkBench to use a specific number of worker cores.
        
        Modifies the runner.py template to add --worker-cores argument.
        """
        runner_path = os.path.join(
            self.dcperf_path,
            'packages', 'spark_standalone', 'templates', 'runner.py'
        )
        
        if not os.path.exists(runner_path):
            logger.warning(f"SparkBench runner.py not found at {runner_path}, "
                          f"cannot configure worker cores")
            return
        
        # Read the runner.py
        with open(runner_path, 'r') as f:
            content = f.read()
        
        # Check if --worker-cores is already configured
        worker_cores_line = f'    cmd_list.append("--worker-cores")\n    cmd_list.append("{worker_cores}")\n'
        marker = '# OS_PARAM_TUNING_WORKER_CORES'
        
        if marker in content:
            # Already configured — update the value
            # Remove old marker block and re-add
            lines = content.split('\n')
            new_lines = []
            skip_next = 0
            for i, line in enumerate(lines):
                if skip_next > 0:
                    skip_next -= 1
                    continue
                if marker in line:
                    # Replace the next 2 lines (append key, append value)
                    new_lines.append(f'    {marker}')
                    new_lines.append(f'    cmd_list.append("--worker-cores")')
                    new_lines.append(f'    cmd_list.append("{worker_cores}")')
                    skip_next = 2
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
        else:
            # Find the run_test function's cmd_list and add --worker-cores before --real
            # Look for cmd_list.append("--real") in run_test
            content = content.replace(
                '    cmd_list.append("--real")\n',
                f'    {marker}\n'
                f'    cmd_list.append("--worker-cores")\n'
                f'    cmd_list.append("{worker_cores}")\n'
                f'    cmd_list.append("--real")\n',
                1  # Only replace first occurrence (in run_test function)
            )
        
        # Write the modified runner.py (handle root-owned files from sudo install)
        try:
            with open(runner_path, 'w') as f:
                f.write(content)
        except PermissionError:
            # DCPerf was installed with sudo — write via temp file + sudo cp
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                subprocess.run(['sudo', 'cp', tmp_path, runner_path], check=True, timeout=10)
                subprocess.run(['sudo', 'chmod', '755', runner_path], check=True, timeout=5)
            finally:
                os.unlink(tmp_path)
        
        logger.info(f"Configured SparkBench worker cores: {worker_cores}")
    
    def _parse_stdout(self, stdout: str, run_duration: float) -> BenchmarkMetrics:
        """Parse SparkBench output from stdout.
        
        Output format (from results.txt, printed to stdout):
            queries-per-hour : 245.484
            test-release_test_93586 : 14.7
            worker-cores : 24
            worker-memory : 39GB
        """
        metrics = BenchmarkMetrics()
        extra = {}
        
        queries_per_hour = None
        execution_time = None
        
        for line in stdout.split('\n'):
            line = line.strip()
            
            # Parse key-value pairs separated by ":"
            if ':' not in line:
                continue
            
            parts = line.split(':', 1)
            if len(parts) != 2:
                continue
            
            key = parts[0].strip()
            value = parts[1].strip()
            
            try:
                if key == 'queries-per-hour':
                    queries_per_hour = float(value)
                    extra['queries_per_hour'] = queries_per_hour
                elif key.startswith('test-release_test'):
                    test_name = key.replace('test-release_', '')
                    execution_time = float(value)
                    extra[f'execution_time_{test_name}'] = execution_time
                elif key == 'worker-cores':
                    extra['worker_cores'] = int(value)
                elif key == 'worker-memory':
                    extra['worker_memory'] = value
                elif key == 'total_iops_read':
                    extra['total_iops_read'] = int(value)
                elif key == 'total_iops_write':
                    extra['total_iops_write'] = int(value)
            except (ValueError, IndexError) as e:
                logger.debug(f"Could not parse line: {line} ({e})")
                continue
        
        # Calculate score (normalized against baseline)
        # DCPerf baseline for sparkbench is 4.0 queries_per_hour
        SPARK_BASELINE = 4.0
        if queries_per_hour is not None:
            extra['score'] = queries_per_hour / SPARK_BASELINE
        
        # Map to BenchmarkMetrics
        if queries_per_hour is not None:
            metrics.throughput = queries_per_hour
        
        if execution_time is not None:
            metrics.latency_avg = execution_time
        
        # Store run duration
        extra['run_duration_seconds'] = run_duration
        metrics.extra_metrics = extra
        
        if queries_per_hour is None:
            logger.warning("No queries_per_hour found in DCPerf SparkBench output — "
                          "benchmark may have failed or output format changed")
        else:
            logger.info(f"SparkBench results: queries_per_hour={queries_per_hour}, "
                       f"execution_time={execution_time}, duration={run_duration:.1f}s")
        
        return metrics


class DCPerfMediawikiBenchmark(DCPerfBenchmark):
    """DCPerf MediaWiki (oss_performance) benchmark implementation.
    
    Runs the oss_performance_mediawiki benchmark (HHVM + nginx + MariaDB + wrk)
    and parses JSON stdout for:
    - wrk_rps: Wrk RPS from "Combined" section (primary throughput metric)
    - score: Normalized score (RPS / 1280.0 baseline)
    
    The benchmark exercises a full web stack serving MediaWiki pages under load.
    
    Output format: JSON blob in stdout with structure like:
        {"Combined": {"Wrk RPS": 1234.5, ...}, "WrkResults": [...], ...}
    """
    
    BENCHMARK_NAME = "oss_performance_mediawiki"
    
    # Baseline RPS from benchpress (used to compute normalized score)
    MEDIAWIKI_BASELINE_RPS = 1280.0
    
    def __init__(self, config):
        super().__init__(config)
        # Override estimated duration — mediawiki default is 10 minutes
        self._estimated_duration = 900  # 15 min to be safe
    
    def _extract_mediawiki_metrics(self, data: dict, extra: dict):
        """Extract RPS metrics from parsed JSON data.
        
        Returns:
            Tuple of (wrk_rps, extra_dict)
        """
        wrk_rps = None
        combined = data.get('Combined', {})
        
        if 'Wrk RPS' in combined:
            wrk_rps = float(combined['Wrk RPS'])
            logger.info(f"MediaWiki: Wrk RPS = {wrk_rps}")
        elif 'Siege RPS' in combined:
            wrk_rps = float(combined['Siege RPS'])
            logger.info(f"MediaWiki: Siege RPS = {wrk_rps}")
        
        # Store all Combined metrics as extra
        for k, v in combined.items():
            extra[f"mediawiki_{k.lower().replace(' ', '_')}"] = v
        
        return wrk_rps, extra
    
    def _parse_stdout(self, stdout: str, run_duration: float) -> BenchmarkMetrics:
        """Parse MediaWiki benchmark output from stdout.
        
        The output is a JSON blob containing nested results. We extract:
        - Combined.Wrk RPS (or Combined.Siege RPS) → wrk_rps metric
        - score = RPS / baseline (1280.0)
        
        Args:
            stdout: Complete stdout from the benchmark process
            run_duration: How long the benchmark took in seconds
            
        Returns:
            BenchmarkMetrics with parsed values
        """
        import json as json_module
        
        metrics = BenchmarkMetrics()
        extra = {}
        
        wrk_rps = None
        score = None
        
        # Extract JSON from stdout — try multiple strategies
        # Strategy 1: Try each line as JSON directly
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith('{'):
                continue
            try:
                data = json_module.loads(line)
                if isinstance(data, dict) and 'Combined' in data:
                    wrk_rps, extra = self._extract_mediawiki_metrics(data, extra)
                    break
            except (json_module.JSONDecodeError, ValueError):
                continue
        
        # Strategy 2: Scan full text for JSON objects starting with '{'
        if wrk_rps is None:
            full_text = ' '.join(stdout.splitlines())
            for i, ch in enumerate(full_text):
                if ch == '{':
                    # Try parsing from this position to various end positions
                    # (find matching closing brace using json decoder)
                    try:
                        data, end_idx = json_module.JSONDecoder().raw_decode(full_text, i)
                        if isinstance(data, dict) and 'Combined' in data:
                            wrk_rps, extra = self._extract_mediawiki_metrics(data, extra)
                            break
                    except (json_module.JSONDecodeError, ValueError):
                        continue
        
        # Calculate score
        if wrk_rps is not None:
            score = wrk_rps / self.MEDIAWIKI_BASELINE_RPS
        
        # Populate metrics
        metrics.throughput = wrk_rps  # Primary metric (wrk_rps / siege_rps)
        extra['wrk_rps'] = wrk_rps
        extra['score'] = score
        extra['run_duration_seconds'] = run_duration
        metrics.extra_metrics = extra
        
        if wrk_rps is None:
            logger.warning("No Wrk/Siege RPS found in DCPerf MediaWiki output — "
                          "benchmark may have failed or output format changed")
        else:
            logger.info(f"MediaWiki results: wrk_rps={wrk_rps}, score={score:.4f}, "
                       f"duration={run_duration:.1f}s")
        
        return metrics


class DCPerfDjangoBenchmark(DCPerfBenchmark):
    """DCPerf DjangoBench (django_workload) benchmark implementation.
    
    Runs the django_workload_default benchmark in standalone mode
    (Django + Cassandra + uWSGI + Siege on one machine) and parses
    stdout for:
    - transaction_rate: Trans/sec from Siege output (primary throughput metric)
    - response_time: Average response time in seconds
    - p50/p90/p95/p99: Latency percentiles
    - score: Normalized score (trans/sec / 958.0 baseline)
    
    Output format (text lines):
        Transaction rate:    217.518 trans/sec ---- RSD 0.223
        Response time:       0.646 secs ---- RSD 0.203
        P50:                 0.304 secs ---- RSD 0.057
        ...
    """
    
    BENCHMARK_NAME = "django_workload_default"
    
    # Baseline trans/sec from benchpress (used to compute normalized score)
    DJANGO_BASELINE_TPS = 958.0
    
    def __init__(self, config):
        super().__init__(config)
        
        # CPU pinning: backend (Cassandra + Django/uWSGI) and client (Siege)
        # These are read from config or default to sensible values
        self.backend_cores = getattr(config, 'django_backend_cores', '0-9')
        self.client_cores = getattr(config, 'django_client_cores', '10-19')
        
        # Shorter default: 3 iterations × 2 min = ~6 min + startup (~10 min total)
        self._estimated_duration = 900  # 15 min
        
        logger.info(f"  Backend (Cassandra+Django) cores: {self.backend_cores}")
        logger.info(f"  Client (Siege) cores: {self.client_cores}")
    
    def _get_run_command(self) -> List[str]:
        """Build benchpress run command with standalone role.
        
        Django workload needs '-r standalone' to run all components
        (Django, Cassandra, Siege) on one machine.
        """
        if getattr(self, '_use_cli_script', True):
            benchpress_cli = os.path.join(self.dcperf_path, 'benchpress_cli.py')
            if os.path.exists(benchpress_cli):
                cmd = [benchpress_cli, "run", self.benchmark_name,
                       "-r", "standalone"]
                return cmd
        
        # Fallback: python3 -c with main() call
        python_code = (
            f"from benchpress.cli.main import main; "
            f"main(['run', '{self.benchmark_name}', '-r', 'standalone'])"
        )
        cmd = ["python3", "-c", python_code]
        return cmd
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute DjangoBench with component-level CPU pinning.
        
        Overrides the base class to set environment variables for pinning
        Cassandra, Django/uWSGI, and Siege to separate core sets instead
        of wrapping the entire process with a single taskset.
        """
        logger.info(f"=== DCPerf {self.benchmark_name} window {window_number} (run-to-completion) ===")
        
        # Build command — do NOT wrap with taskset (components handle their own pinning)
        cmd = self._get_run_command()
        
        # DCPerf typically requires sudo — use sudo -E to preserve env vars
        if os.geteuid() != 0:
            cmd = ["sudo", "-E"] + cmd
        
        logger.info(f"Running: {' '.join(cmd)}")
        logger.info(f"  Backend cores (Cassandra+Django): {self.backend_cores}")
        logger.info(f"  Client cores (Siege): {self.client_cores}")
        print(f"Running DCPerf command: {' '.join(cmd)}")
        
        # Set environment variables for per-component CPU pinning
        env = os.environ.copy()
        env["CASSANDRA_TASKSET_CPUS"] = self.backend_cores
        env["DJANGO_TASKSET_CPUS"] = self.backend_cores
        env["SIEGE_TASKSET_CPUS"] = self.client_cores
        
        # Record start time
        window_start_time = time.time()
        
        # Start system metrics collection
        actual_window_start = self.start_system_measurement(window_number, self.max_runtime)
        
        # Start perf stat
        perf_info = self.collect_perf_metrics(window_number, self.max_runtime)
        
        # Start the benchmark process with env vars
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.dcperf_path,
            env=env,
            bufsize=1,
            universal_newlines=True
        )
        
        # Stream and capture output
        output_lines = []
        def stream_output():
            try:
                for line in process.stdout:
                    print(f"[DCPerf] {line}", end='', flush=True)
                    output_lines.append(line)
            except Exception:
                pass
        
        output_thread = threading.Thread(target=stream_output, daemon=True)
        output_thread.start()
        
        # Wait for process to complete (with max_runtime safety net)
        start_time = time.time()
        while True:
            if process.poll() is not None:
                break
            
            elapsed = time.time() - start_time
            if elapsed > self.max_runtime:
                logger.warning(f"DCPerf benchmark exceeded max_runtime ({self.max_runtime}s), killing...")
                process.terminate()
                time.sleep(5)
                if process.poll() is None:
                    process.kill()
                break
            
            time.sleep(1.0)
        
        # Wait for output thread to finish
        output_thread.join(timeout=5)
        
        window_end_time = time.time()
        run_duration = window_end_time - window_start_time
        logger.info(f"DCPerf benchmark completed in {run_duration:.1f}s (exit code: {process.returncode})")
        
        # Stop perf stat if still running
        if perf_info and 'perf_process' in perf_info:
            perf_process = perf_info['perf_process']
            perf_output_handle = perf_info.get('perf_output_handle')
            try:
                perf_process.terminate()
                perf_process.wait(timeout=5)
            except Exception:
                try:
                    perf_process.kill()
                    perf_process.wait(timeout=2)
                except Exception:
                    pass
            if perf_output_handle:
                try:
                    perf_output_handle.close()
                except Exception:
                    pass
        
        if process.returncode != 0:
            logger.error(f"DCPerf benchmark failed with exit code {process.returncode}")
        
        # Parse output
        stdout_text = ''.join(output_lines)
        metrics = self._parse_stdout(stdout_text, run_duration)
        
        # Populate system metrics
        self._populate_system_metrics(metrics, window_number, actual_window_start, window_end_time)
        
        return metrics
    
    def pre_execute(self) -> bool:
        """Verify Django benchmark is installed and fix permissions."""
        result = super().pre_execute()
        if not result:
            return False
        
        # Fix Cassandra data directory permissions (common issue per setup doc)
        cassandra_dirs = [
            "/data/cassandra/data",
            "/data/cassandra/commitlog",
            "/data/cassandra/saved_caches",
            "/data/cassandra/hints",
        ]
        for d in cassandra_dirs:
            if os.path.exists(d):
                try:
                    subprocess.run(
                        ["chmod", "-R", "777", d],
                        capture_output=True, timeout=10
                    )
                except Exception:
                    pass
        
        # Fix django_workload benchmark directory permissions
        django_dir = os.path.join(self.dcperf_path, "benchmarks", "django_workload")
        if os.path.exists(django_dir):
            try:
                import getpass
                user = getpass.getuser()
                subprocess.run(
                    ["chown", "-R", f"{user}:{user}", django_dir],
                    capture_output=True, timeout=30
                )
            except Exception:
                pass
        
        return True
    
    def _parse_stdout(self, stdout: str, run_duration: float) -> BenchmarkMetrics:
        """Parse DjangoBench output from stdout.
        
        Output format (Siege summary, averaged across iterations):
            Transactions:       26059.8 hits ---- RSD 0.223
            Availability:       99.83 % ---- RSD 0.000
            Elapsed time:       119.804 secs ---- RSD 0.000
            Transaction rate:   217.518 trans/sec ---- RSD 0.223
            Response time:      0.646 secs ---- RSD 0.203
            Throughput:         0.428 MB/sec ---- RSD 0.220
            Concurrency:        133.842  ---- RSD 0.067
            P50:                0.304 secs ---- RSD 0.057
            P90:                0.832 secs ---- RSD 0.404
            P95:                1.478 secs ---- RSD 0.283
            P99:                7.074 secs ---- RSD 0.428
        
        Args:
            stdout: Complete stdout from the benchmark process
            run_duration: How long the benchmark took in seconds
            
        Returns:
            BenchmarkMetrics with parsed values
        """
        metrics = BenchmarkMetrics()
        extra = {}
        
        transaction_rate = None
        response_time = None
        
        # Parse key-value metrics from stdout
        # Lines after "URL hit percentages:" contain the summary data
        in_results = False
        
        for line in stdout.splitlines():
            line = line.strip()
            
            # Start parsing after "URL hit percentages" section
            if "URL hit percentage" in line:
                in_results = True
                continue
            
            if not in_results or ':' not in line:
                continue
            
            # Skip URL hit lines (start with /)
            if line.startswith('/'):
                continue
            
            try:
                # Split on first colon
                parts = line.split(':', 1)
                if len(parts) != 2:
                    continue
                
                key = parts[0].strip()
                value_part = parts[1].strip()
                
                # Remove RSD part if present
                if '----' in value_part:
                    value_part = value_part.split('----')[0].strip()
                
                # Extract numeric value and unit
                tokens = value_part.split()
                if not tokens:
                    continue
                
                try:
                    val = float(tokens[0])
                except ValueError:
                    continue
                
                unit = tokens[1] if len(tokens) > 1 else ""
                metric_key = key.lower().replace(' ', '_')
                if unit:
                    metric_key += f"_{unit.replace('/', '_per_')}"
                
                extra[f"django_{metric_key}"] = val
                
                # Extract key metrics
                if key == "Transaction rate":
                    transaction_rate = val
                elif key == "Response time":
                    response_time = val
                elif key == "P50":
                    metrics.latency_p50 = val
                elif key == "P90":
                    metrics.latency_p90 = val
                elif key == "P95":
                    metrics.latency_p95 = val
                elif key == "P99":
                    metrics.latency_p99 = val
                elif key == "Concurrency":
                    pass  # stored in extra already
                    
            except Exception as e:
                logger.debug(f"Failed to parse Django metric line: {line}: {e}")
                continue
        
        # Calculate score
        score = None
        if transaction_rate is not None:
            score = transaction_rate / self.DJANGO_BASELINE_TPS
        
        # Populate metrics
        metrics.throughput = transaction_rate  # Primary metric
        if response_time is not None:
            metrics.latency_avg = response_time
        extra['transaction_rate'] = transaction_rate
        extra['score'] = score
        extra['run_duration_seconds'] = run_duration
        metrics.extra_metrics = extra
        
        if transaction_rate is None:
            logger.warning("No Transaction rate found in DCPerf DjangoBench output — "
                          "benchmark may have failed or output format changed")
        else:
            logger.info(f"DjangoBench results: transaction_rate={transaction_rate} trans/sec, "
                       f"score={score:.4f}, response_time={response_time}s, "
                       f"duration={run_duration:.1f}s")
        
        return metrics


