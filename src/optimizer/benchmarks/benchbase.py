#!/usr/bin/env python3
"""
BenchBase benchmark implementation for the simplified OS tuner.

This module implements the BenchmarkInterface for BenchBase benchmarks (TPCC, YCSB, etc.).
"""

import os
import subprocess
import json
import glob
import time
import logging
import xml.etree.ElementTree as ET

from ..benchmark import BenchmarkInterface, BenchmarkMetrics

logger = logging.getLogger(__name__)


class BenchBaseBenchmark(BenchmarkInterface):
    """BenchBase benchmark implementation for TPCC, YCSB, and other BenchBase workloads."""
    
    def __init__(self, config):
        super().__init__(config)
        
        # Resolve jar_path relative to repo root if not absolute
        jar_path = config.benchbase_jar_path
        if not os.path.isabs(jar_path):
            jar_path = os.path.join(self.repo_root, jar_path)
        self.jar_path = os.path.abspath(jar_path)
        
        # Resolve config_file relative to repo root if not absolute
        config_file = config.benchbase_config_file
        if not os.path.isabs(config_file):
            config_file = os.path.join(self.repo_root, config_file)
        self.config_file = os.path.abspath(config_file)
        
        # Detect benchmark type from config, default to TPCC
        self.benchmark_type = getattr(config, 'benchmark', 'tpcc').lower()
        logger.info(f"Initializing BenchBase benchmark with type: {self.benchmark_type}")
        
        self.benchbase_dir = os.path.dirname(self.jar_path)
        self.database_setup_done = False
        
        # Verify files exist
        if not os.path.exists(self.jar_path):
            raise FileNotFoundError(f"BenchBase JAR not found: {self.jar_path}")
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"BenchBase config file not found: {self.config_file}")
        
        self.temp_config_dir = os.path.join(self.results_dir, "temp_configs")
        os.makedirs(self.temp_config_dir, exist_ok=True)
        
        # Create window-specific output directory
        self.window_output_dir = os.path.join(self.results_dir, "benchbase_windows")
        os.makedirs(self.window_output_dir, exist_ok=True)
    
    def _wrap_with_taskset(self, cmd: list) -> list:
        """Wrap command with taskset, always using cores 10-19 for BenchBase benchmarks.
        
        Args:
            cmd: Original command as list of strings
            
        Returns:
            Command wrapped with taskset on cores 10-19
        """
        return ["taskset", "-c", "10-19"] + cmd
    
    def cleanup(self) -> None:
        """Clean up benchmark resources."""
        # Remove manual temp config directory if it exists
        if os.path.exists(self.temp_config_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_config_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp config dir: {e}")
        
    def pre_execute(self) -> bool:
        if self.database_setup_done:
            return True

        # A short multi-method Functional suite may prepare one identical
        # dataset once, then launch each optimizer in a separate process. This
        # environment-only switch is intentionally opt-in; ordinary paper
        # configs retain their create/load isolation.
        if os.environ.get("SEMATUNE_BENCHBASE_REUSE_LOADED_DB") == "1":
            logger.info("Reusing the BenchBase database prepared by the first suite method")
            self.database_setup_done = True
            return True
        
        logger.info("Running BenchBase pre-execution setup (create + load)...")
        
        setup_config = self._create_temp_config(0)
        setup_output_dir = os.path.abspath(os.path.join(self.results_dir, "database_setup"))
        os.makedirs(setup_output_dir, exist_ok=True)
        
        # Use just the JAR basename - must run from benchbase_dir to find lib/ dependencies
        jar_basename = os.path.basename(self.jar_path)
        
        cmd = [
            "java", "-jar", jar_basename,
            "-b", self.benchmark_type,
            "--config", setup_config,
            "--create=true",
            "--load=true",
            "-d", setup_output_dir,
        ]
        
        logger.info(f"Running BenchBase setup from {self.benchbase_dir}: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=self.benchbase_dir, check=True)
        self.database_setup_done = True
        os.unlink(setup_config)
        return True
    
    def _create_temp_config(self, window_number: int, duration: int = None) -> str:
        """Create a temporary config file for window execution.
        
        Args:
            window_number: Window number for config file naming
            duration: Duration in seconds to set in config (uses config.window_duration if None)
            
            
        Returns:
            Absolute path to temporary config file
        """
        tree = ET.parse(self.config_file)
        root = tree.getroot()

        # Site-specific database credentials are supplied only through the
        # caller's environment. They are written to the short-lived per-window
        # XML and never serialized in an optimizer history or command line.
        db_host = os.environ.get("SEMATUNE_BENCHBASE_HOST") or os.environ.get("SEMATUNE_SYSBENCH_HOST")
        db_port = os.environ.get("SEMATUNE_BENCHBASE_PORT") or os.environ.get("SEMATUNE_SYSBENCH_PORT")
        db_name = os.environ.get("SEMATUNE_BENCHBASE_DB") or os.environ.get("SEMATUNE_SYSBENCH_DB")
        db_user = os.environ.get("SEMATUNE_BENCHBASE_USER") or os.environ.get("SEMATUNE_SYSBENCH_USER")
        db_password = os.environ.get("SEMATUNE_BENCHBASE_PASSWORD") or os.environ.get("SEMATUNE_SYSBENCH_PASSWORD")
        if db_host and db_port and db_name:
            url_elem = root.find(".//url")
            if url_elem is not None:
                url_elem.text = (
                    f"jdbc:postgresql://{db_host}:{db_port}/{db_name}"
                    "?sslmode=disable&ApplicationName=tpcc&reWriteBatchedInserts=true"
                )
        if db_user:
            user_elem = root.find(".//username")
            if user_elem is not None:
                user_elem.text = db_user
        if db_password:
            password_elem = root.find(".//password")
            if password_elem is not None:
                password_elem.text = db_password
        
        # Update the time element to use window duration
        time_elem = root.find('.//work/time')
        if time_elem is not None:
            actual_duration = duration if duration is not None else self.config.window_duration
            time_elem.text = str(actual_duration)
        else:
            raise ValueError("Could not find time element in config")
        
        if self.config.workload_change_type == "cyclic_halving":
            # Determine phase: 0=Normal, 1=Halved
            interval = self.config.workload_change_interval or 5
            phase = ((window_number - 1) // interval) % 2
            
            if phase == 1:
                # Find rate element and halve it
                rate_elem = root.find('.//work/rate')
                if rate_elem is not None:
                    try:
                        original_rate = int(rate_elem.text)
                        new_rate = max(1, original_rate // 2)
                        logger.info(f"Workload Change: Halving rate from {original_rate} to {new_rate} (Window {window_number})")
                        rate_elem.text = str(new_rate)
                    except ValueError:
                        logger.warning(f"Could not parse rate '{rate_elem.text}' as integer")
                else:
                    logger.warning("Workload Change: <rate> element not found in config")
        
        # Create unique temp config file (already absolute from __init__)
        temp_config_path = os.path.join(
            self.temp_config_dir,
            f"config_window_{window_number}.xml"
        )
        
        tree.write(temp_config_path, xml_declaration=True, encoding='utf-8')
        return os.path.abspath(temp_config_path)

    def update_workload(self, iteration: int) -> None:
        """Update workload parameters based on iteration number.
        
        This method is called by the optimizer before executing the window.
        For BenchBase, the actual config modification happens in _create_temp_config
        since we generate a new config for each window anyway.
        """
        # We don't need to store state here because _create_temp_config takes window_number
        pass
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        logger.info(f"Executing BenchBase window {window_number} for {duration}s")
        
        # Create window-specific output directory
        window_dir = os.path.join(self.window_output_dir, f"window_{window_number}")
        os.makedirs(window_dir, exist_ok=True)
        
        # Create temp config for this window with the actual duration
        temp_config = self._create_temp_config(window_number, duration)
        
        # Build execute command
        # Use absolute path for window_dir so BenchBase writes results to the correct location
        abs_window_dir = os.path.abspath(window_dir)
        os.makedirs(abs_window_dir, exist_ok=True)
        
        # Use just the JAR basename - must run from benchbase_dir to find lib/ dependencies
        jar_basename = os.path.basename(self.jar_path)
        
        cmd = [
            "java", "-jar", jar_basename,
            "-b", self.benchmark_type,
            "--config", temp_config,
            "--execute=true",
            "-d", abs_window_dir,
        ]
        
        # Wrap with taskset if pinning enabled
        final_cmd = self._wrap_with_taskset(cmd)
        
        logger.info(f"Running BenchBase execute: {' '.join(final_cmd)}")
        # Slightly larger timeout than before and one retry on timeout.
        timeout_buffer_seconds = max(
            0, int(getattr(self.config, "benchbase_timeout_buffer_seconds", 40))
        )
        max_timeout_retries = max(
            0, int(getattr(self.config, "benchbase_timeout_retries", 1))
        )
        timeout_seconds = duration + timeout_buffer_seconds

        import threading
        actual_window_start = None
        window_end_time = None

        try:
            for attempt in range(1, max_timeout_retries + 2):
                if attempt > 1:
                    logger.warning(
                        "Retrying BenchBase window %s after a transient process failure (%s/%s)",
                        window_number,
                        attempt,
                        max_timeout_retries + 1,
                    )

                # Start system metrics collection for this attempt
                actual_window_start = self.start_system_measurement(window_number, duration)

                process = subprocess.Popen(
                    final_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Combine stderr into stdout
                    text=True,
                    cwd=self.benchbase_dir,
                    bufsize=1,
                    universal_newlines=True
                )

                print(f"Running command: {' '.join(final_cmd)}")

                # Stream output in a thread (daemon so it doesn't block)
                output_lines = []

                def stream_output():
                    try:
                        for line in process.stdout:
                            print(f"[BenchBase] {line}", end='', flush=True)
                            output_lines.append(line)
                    except Exception:
                        pass

                threading.Thread(target=stream_output, daemon=True).start()

                # Wait 2 seconds before starting perf (avoid startup noise)
                time.sleep(2)

                # Start perf stat (returns dict with process handle)
                perf_info = self.collect_perf_metrics(window_number, max(1, duration - 2))

                # Wait for BenchBase process to finish with timeout using poll()
                # poll() is safer than wait() when we have threads reading from pipes
                start_time = time.time()
                timed_out = False

                while True:
                    if process.poll() is not None:
                        # Process has finished
                        break

                    if time.time() - start_time > timeout_seconds:
                        # Timeout - kill the process
                        timed_out = True
                        logger.warning(
                            "BenchBase window %s timed out after %ss on attempt %s/%s; terminating...",
                            window_number,
                            timeout_seconds,
                            attempt,
                            max_timeout_retries + 1,
                        )
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        break

                    time.sleep(0.5)

                # Make sure process has exited before continuing
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

                window_end_time = time.time()

                # Wait, parse, and persist perf metrics for _populate_system_metrics.
                self.finalize_perf_metrics(window_number, perf_info)

                if process.returncode == 0:
                    break

                # A fresh BenchBase JVM can also fail transiently while
                # PostgreSQL invalidates catalog entries after a preceding
                # create/load. Retry any non-zero process exit within the same
                # strict budget; persistent configuration errors still fail on
                # the next attempt.
                if attempt <= max_timeout_retries:
                    logger.warning(
                        "BenchBase window %s exited %s%s; retrying",
                        window_number,
                        process.returncode,
                        " after timeout" if timed_out else "",
                    )
                    continue

                raise RuntimeError(
                    f"BenchBase window {window_number} failed with exit code {process.returncode}"
                )
            else:
                raise RuntimeError(
                    f"BenchBase window {window_number} failed after {max_timeout_retries + 1} attempts"
                )

            # Wait for BenchBase to write the summary.json file
            summary_pattern = os.path.join(abs_window_dir, "*.summary.json")
            max_wait_time = 10.0
            wait_start = time.time()
            summary_files = []
            while not summary_files and (time.time() - wait_start) < max_wait_time:
                summary_files = glob.glob(summary_pattern)
                if not summary_files:
                    time.sleep(0.5)

            if not summary_files:
                logger.warning(f"Summary file not found after waiting {max_wait_time}s in {abs_window_dir}")

            metrics = self.parse_results(abs_window_dir)
            self._populate_system_metrics(
                metrics,
                window_number,
                actual_window_start or time.time(),
                window_end_time or time.time(),
            )
            return metrics
        finally:
            if os.path.exists(temp_config):
                try:
                    os.unlink(temp_config)
                except OSError as e:
                    logger.warning(f"Failed to remove temp config {temp_config}: {e}")
            
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse results from output directory.
        
        Args:
            output_dir: Directory containing benchmark output files
            
        Returns:
            Parsed metrics from the results
        """
        summary_files = glob.glob(os.path.join(output_dir, "*.summary.json"))
        if not summary_files:
            raise FileNotFoundError(f"No summary.json files found in {output_dir}")
        
        latest_file = max(summary_files, key=os.path.getctime)
        
        with open(latest_file, 'r') as f:
            metrics_data = json.load(f)
        
        # Log available keys for debugging
        logger.debug(f"BenchBase summary.json keys: {list(metrics_data.keys())}")
        if "Latency Distribution" in metrics_data:
            logger.debug(f"Latency Distribution keys: {list(metrics_data['Latency Distribution'].keys())}")
        
        # Process metrics
        processed = self._process_metrics(metrics_data)
        
        # Log processed metrics for debugging
        logger.debug(f"Processed metrics keys: {list(processed.keys())}")
        logger.debug(f"Processed latency_avg: {processed.get('latency_avg')}, p_95_latency: {processed.get('p_95_latency')}")
        
        # Convert to BenchmarkMetrics
        latency_avg_us = processed.get("latency_avg", 0.0)
        latency_p95_us = processed.get("p_95_latency", 0.0)
        latency_p99_us = processed.get("p_99_latency", 0.0)
        
        # Convert microseconds to milliseconds (only if we have valid values)
        latency_avg_ms = latency_avg_us / 1000.0 if latency_avg_us > 0 else 0.0
        latency_p95_ms = latency_p95_us / 1000.0 if latency_p95_us > 0 else 0.0
        latency_p99_ms = latency_p99_us / 1000.0 if latency_p99_us > 0 else 0.0
        
        metrics = BenchmarkMetrics(
            throughput=processed.get("throughput", 0.0),
            goodput=processed.get("goodput", 0.0),
            latency_avg=latency_avg_ms,
            latency_p95=latency_p95_ms,
            extra_metrics={
                "measured_requests": processed.get("measured_requests", 0),
                "duration_seconds": processed.get("duration_seconds", 0.0),
                "p_99_latency": latency_p99_us, # Keep original us value in extra_metrics
                "latency_p99": latency_p99_ms,  # Add ms value for convenience
                **{k: v for k, v in processed.items() if k not in ["throughput", "goodput", "latency_avg", "p_95_latency", "p_99_latency"]}
            }
        )
        
        logger.info(f"Parsed BenchBase metrics: goodput={metrics.goodput:.2f}, throughput={metrics.throughput:.2f}, "
                   f"latency_avg={metrics.latency_avg:.2f}ms, latency_p95={metrics.latency_p95:.2f}ms, latency_p99={latency_p99_ms:.2f}ms")
        return metrics

    def _process_metrics(self, metrics: dict) -> dict:
        processed = {}
        
        # Map common metrics from summary.json format
        key_mappings = {
            "Throughput (requests/second)": "throughput",
            "Goodput (requests/second)": "goodput",
            "Measured Requests": "measured_requests",
            "Elapsed Time (nanoseconds)": "elapsed_time_ns",
        }
        
        for source_key, target_key in key_mappings.items():
            if source_key in metrics:
                processed[target_key] = metrics[source_key]
        
        # Process latency distribution if available
        if "Latency Distribution" in metrics:
            latency_dist = metrics["Latency Distribution"]
            
            # Try multiple possible key formats (BenchBase might use different formats)
            latency_mappings = [
                # Standard format
                ("Average Latency (microseconds)", "latency_avg"),
                ("95th Percentile Latency (microseconds)", "p_95_latency"),
                # Alternative formats (in case keys are slightly different)
                ("Average Latency (us)", "latency_avg"),
                ("95th Percentile Latency (us)", "p_95_latency"),
                ("Average", "latency_avg"),
                ("95th Percentile", "p_95_latency"),
                # Additional percentiles
                ("Median Latency (microseconds)", "latency_median"),
                ("Minimum Latency (microseconds)", "latency_min"),
                ("Maximum Latency (microseconds)", "latency_max"),
                ("25th Percentile Latency (microseconds)", "p_25_latency"),
                ("75th Percentile Latency (microseconds)", "p_75_latency"),
                ("90th Percentile Latency (microseconds)", "p_90_latency"),
                ("99th Percentile Latency (microseconds)", "p_99_latency"),
            ]
            
            for source_key, target_key in latency_mappings:
                if source_key in latency_dist:
                    value = latency_dist[source_key]
                    # Handle both numeric values and dicts (some BenchBase versions return dicts)
                    if isinstance(value, dict):
                        # If it's a dict, try to extract a numeric value
                        if "value" in value:
                            processed[target_key] = float(value["value"])
                        elif "mean" in value:
                            processed[target_key] = float(value["mean"])
                        else:
                            logger.warning(f"Latency value for {source_key} is a dict but no 'value' or 'mean' key found: {list(value.keys())}")
                    else:
                        processed[target_key] = float(value)
                    logger.debug(f"Found {source_key} -> {target_key}: {processed[target_key]}")
            
            # Fallback: if we still don't have latency_avg or p_95_latency, try case-insensitive search
            if "latency_avg" not in processed:
                for key in latency_dist.keys():
                    if "average" in key.lower() and "latency" in key.lower():
                        value = latency_dist[key]
                        if isinstance(value, (int, float)):
                            processed["latency_avg"] = float(value)
                            logger.debug(f"Found latency_avg via fallback from key '{key}': {value}")
                            break
            
            if "p_95_latency" not in processed:
                for key in latency_dist.keys():
                    if "95" in key and "percentile" in key.lower() and "latency" in key.lower():
                        value = latency_dist[key]
                        if isinstance(value, (int, float)):
                            processed["p_95_latency"] = float(value)
                            logger.debug(f"Found p_95_latency via fallback from key '{key}': {value}")
                            break
        
        # Calculate derived metrics
        if "elapsed_time_ns" in processed:
            processed["duration_seconds"] = processed["elapsed_time_ns"] / 1_000_000_000
        
        return processed
