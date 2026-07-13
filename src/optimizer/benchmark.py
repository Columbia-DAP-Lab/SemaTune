#!/usr/bin/env python3
"""
Benchmark interface for OS parameter tuning.

This module provides an abstract base class that all benchmarks must implement,
ensuring a consistent interface for the optimizer.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Set
from pathlib import Path
import subprocess
import time
import os
import logging
import threading
import json

logger = logging.getLogger(__name__)


def get_repo_root() -> str:
    """Get the repository root directory using git.
    
    First checks for OS_PARAM_TUNING_ROOT environment variable.
    If not set, uses git to determine the repository root.
    
    Returns:
        Absolute path to repository root
        
    Raises:
        RuntimeError: If not in a git repository or git command fails
    """
    # Check for environment variable first (useful when running with sudo)
    repo_root = os.environ.get("OS_PARAM_TUNING_ROOT")
    if repo_root and os.path.isdir(repo_root):
        return os.path.abspath(repo_root)
    
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise RuntimeError(
            "Could not determine repository root. "
            "Make sure you're running from within the os-param-tuning git repository."
        ) from e


@dataclass
class BenchmarkMetrics:
    """Standardized metrics dataclass for all benchmarks."""
    throughput: float = 0.0  # Requests/transactions per second
    goodput: float = 0.0     # Successful requests/transactions per second
    latency_avg: float = 0.0  # Average latency in milliseconds
    latency_p95: float = 0.0 # 95th percentile latency in milliseconds
    # Additional benchmark-specific metrics
    extra_metrics: Dict[str, Any] = field(default_factory=dict)
    
    # Power and CPU metrics (always collected)
    power_socket0_watts: Optional[float] = None  # Average CPU power (socket 0) in Watts
    power_ram_watts: Optional[float] = None  # Average RAM power (socket 0) in Watts
    cstate_poll_pct: Optional[float] = None  # C-state POLL residency percentage (focused cores)
    cstate_c1_pct: Optional[float] = None  # C-state C1 residency percentage (focused cores)
    cstate_c1e_pct: Optional[float] = None  # C-state C1E residency percentage (focused cores)
    cstate_c6_pct: Optional[float] = None  # C-state C6 residency percentage (focused cores)
    cpu_load_cores_pct: Optional[float] = None  # CPU load percentage (focused cores)
    cpu_load_socket0_pct: Optional[float] = None  # CPU load percentage (socket 0, all cores)
    
    def get_metric(self, name: str) -> float:
        """Get a metric value by name, with fallback to extra_metrics."""
        if hasattr(self, name):
            return getattr(self, name)
        return self.extra_metrics.get(name, 0.0)


class BenchmarkInterface(ABC):
    """Abstract base class for all benchmark implementations."""
    
    def __init__(self, config: Any):
        """Initialize benchmark with configuration.
        
        Args:
            config: Configuration object containing benchmark-specific settings
        """
        self.config = config
        
        # Get repo root and resolve results directory relative to it
        self.repo_root = get_repo_root()
        results_dir = getattr(config, 'results_dir', 'results')
        
        # If results_dir is relative, make it relative to repo root
        if not os.path.isabs(results_dir):
            self.results_dir = os.path.join(self.repo_root, results_dir)
        else:
            self.results_dir = results_dir
        
        self.pin_to_cores = getattr(config, 'pin_to_cores', None)
        os.makedirs(self.results_dir, exist_ok=True)
    
    def _wrap_with_taskset(self, cmd: List[str]) -> List[str]:
        """Wrap command with taskset if CPU pinning is enabled.
        
        Args:
            cmd: Original command as list of strings
            
        Returns:
            Command wrapped with taskset if pinning enabled, otherwise original command
        """
        if self.pin_to_cores:
            return ["taskset", "-c", self.pin_to_cores] + cmd
        return cmd
    
    @abstractmethod
    def cleanup(self) -> None:
        """Clean up benchmark resources."""
        pass

    def update_workload(self, iteration: int) -> None:
        """Update workload parameters based on iteration number.
        
        Args:
            iteration: Current iteration number
        """
        pass
    
    @abstractmethod
    def pre_execute(self) -> bool:
        """Run pre-execution setup (e.g., database load).
        
        Returns:
            True if setup succeeded, False otherwise
        """
        pass
    
    @abstractmethod
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Execute a measurement window.
        
        In case of continuous benchmarks, the window duration this method only parses the results for the current window.
        
        For repetead benchmarks, this method should execute the benchmark for the duration of the window and return the metrics for the entire duration.
        
        The method should ALWAYS call parse_results.
        
        Note: Perf stat and power/CPU metrics are ALWAYS collected automatically.
        
        Args:
            window_number: Current iteration/window number
            duration: Duration of the measurement window in seconds
            
        Returns:
            Parsed metrics from the window execution (includes perf, power, and CPU metrics)
        """
        pass
    
    @abstractmethod
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse results from output directory.
        
        Args:
            output_dir: Directory containing benchmark output files
            
        Returns:
            Parsed metrics from the results
        """
        pass
    
    def supports_perf_stat(self) -> bool:
        """Whether this benchmark supports perf stat integration.
        
        Returns:
            True if perf stat is supported, False otherwise
        """
        return True
    
    def collect_perf_metrics(self, window_number: int, duration: int) -> Dict[str, Any]:
        """Collect perf stat metrics for a window.
        
        This method handles the entire perf stat lifecycle:
        1. Starts perf stat in the background to monitor cores globally
        2. Waits for it to complete
        3. Closes file handles
        4. Parses the output
        
        It uses perf stat's --timeout parameter (in milliseconds) to control the duration.
        The measurement starts 1 second after window start and ends 1 second before window end.
        
        Args:
            window_number: Current window number
            duration: Window duration in seconds
            
        Returns:
            Dictionary with:
            - perf_metrics: Parsed perf metrics dictionary
            - perf_start_time: When perf stat started (1s after window start)
            - perf_end_time: When perf stat ended
            - perf_output_file: Path to file where perf stat output was saved
        """
        # Perf stat duration: window duration - 2 seconds (1s delay + 1s buffer)
        perf_duration_seconds = max(1, duration - 2)
        perf_timeout_ms = int(perf_duration_seconds * 1000)  # Convert to milliseconds
        
        # Create output file path
        perf_output_file = os.path.join(self.results_dir, f"window_{window_number}_perf_stat.txt")
        
        # Record perf stat start time (1 second after window start)
        perf_start_time = time.time() + 1.0
        perf_end_time = perf_start_time + perf_duration_seconds
        
        # Build perf stat command with --timeout
        # perf stat with --timeout runs system-wide and doesn't need a command
        perf_cmd = ["perf", "stat", "--timeout", str(perf_timeout_ms)]
        if self.pin_to_cores:
            # Restrict perf stat to the same cores as the benchmark
            perf_cmd.extend(["--cpu", self.pin_to_cores])
        else:
            # Monitor all CPUs
            perf_cmd.append("-a")
        
        # Create a wrapper script that waits 1s, then runs perf stat
        # perf stat monitors all activity on the specified cores globally
        import shlex
        perf_cmd_str = ' '.join(shlex.quote(str(arg)) for arg in perf_cmd)
        perf_wrapper = f"""
sleep 1 && 
{perf_cmd_str} 2>&1
"""
        
        wrapper_cmd = ["sh", "-c", perf_wrapper]
        
        # Start perf stat in background and capture stdout to file
        perf_output_handle = open(perf_output_file, 'w')
        
        perf_process = subprocess.Popen(
            wrapper_cmd,
            stdout=perf_output_handle,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout to capture all output
            text=True
        )
        
        logger.info(f"Started perf stat measurement for window {window_number} "
                   f"(timeout={perf_timeout_ms}ms after 1s delay, CPU={self.pin_to_cores or 'all'})")
        
        # Calculate perf duration for later use in finalization
        perf_duration = max(1, duration - 2)
        
        # Parse the output after process completes (will be done by caller)
        # Return process info for later waiting and parsing
        return {
            "perf_process": perf_process,
            "perf_output_handle": perf_output_handle,
            "perf_output_file": perf_output_file,
            "perf_start_time": perf_start_time,
            "perf_end_time": perf_end_time,
            "perf_duration": perf_duration
        }
    
    def finalize_perf_metrics(self, window_number: int, perf_info: Dict[str, Any]) -> None:
        """Finalize perf metrics collection after window completes.
        
        This method:
        1. Waits for perf process to complete
        2. Closes file handles
        3. Parses the output
        4. Saves results to JSON file
        
        Args:
            window_number: Current window number
            perf_info: Dictionary returned from collect_perf_metrics containing process handle
        """
        perf_process = perf_info.get("perf_process")
        perf_output_handle = perf_info.get("perf_output_handle")
        perf_output_file = perf_info.get("perf_output_file")
        perf_start_time = perf_info.get("perf_start_time")
        perf_end_time = perf_info.get("perf_end_time")
        perf_duration = perf_info.get("perf_duration", 8)
        
        # Wait for perf stat to finish
        if perf_process:
            try:
                perf_process.wait(timeout=perf_duration + 5)
            except subprocess.TimeoutExpired:
                logger.warning("Perf stat process did not finish in time")
        
        # Close file handle
        if perf_output_handle and not perf_output_handle.closed:
            perf_output_handle.close()
        
        # Parse the output
        perf_metrics = self._parse_perf_stat_output(perf_output_file)
        
        # Save perf info (including parsed metrics) to a JSON file for later retrieval
        perf_info_file = os.path.join(self.results_dir, f"window_{window_number}_perf_info.json")
        perf_info_data = {
            "perf_start_time": perf_start_time,
            "perf_end_time": perf_end_time,
            "perf_output_file": perf_output_file,
            "perf_metrics": perf_metrics
        }
        try:
            with open(perf_info_file, 'w') as f:
                json.dump(perf_info_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save perf info to {perf_info_file}: {e}")
    
    def _run_with_perf_stat(self, cmd: list, duration: int, 
                           output_file: Optional[str] = None,
                           cwd: Optional[str] = None) -> subprocess.Popen:
        """Run a command and start perf stat measurement in parallel.
        
        This is a convenience method that starts both the benchmark and perf stat.
        The benchmark runs for the full duration, while perf stat monitors for (duration-2) seconds.
        
        Note: The cmd should already be wrapped with taskset if CPU pinning is enabled.
        
        Args:
            cmd: Command to run (as list of strings, may already include taskset)
            duration: Total window duration in seconds
            output_file: Optional file to write perf output to (deprecated, use start_perf_measurement instead)
            cwd: Working directory for the command
            
        Returns:
            Process handle for the benchmark command (with perf stat info attached)
        """
        # Start the benchmark process
        benchmark_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd
        )
        
        # Start perf stat measurement (will be handled by benchmark's execute_window)
        # This method is kept for backward compatibility but perf stat should be started separately
        logger.warning("_run_with_perf_stat is deprecated. Use start_perf_measurement() instead.")
        
        return benchmark_process
    
    def _parse_perf_stat_output(self, perf_output_file: str) -> Dict[str, float]:
        """Parse perf stat output from stdout.
        
        Perf stat stdout format (when monitoring system-wide with --cpu= or -a):
        
         Performance counter stats for 'system wide':
        
                 19,468.06 msec cpu-clock                 #   39.723 CPUs utilized          
                   387      context-switches          #   19.879 /sec                   
                    53      cpu-migrations            #    2.722 /sec                   
                   567      page-faults               #   29.125 /sec                   
            57,855,174      cycles                    #    0.003 GHz                    
            29,783,029      instructions              #    0.51  insn per cycle         
             5,777,023      branches                  #  296.744 K/sec                  
               319,430      branch-misses             #    5.53% of all branches        
        
           0.490091285 seconds time elapsed
        
        Or simpler format:
        
         Performance counter stats for 'system wide':
        
              127,779,260      cycles                                                      
        80,652,652      instructions              #    0.63  insn per cycle         
         4,266,083      cache-references                                            
           357,819      cache-misses              #    8.388 % of all cache refs    
        
           3.009424379 seconds time elapsed
        
        Args:
            perf_output_file: Path to perf stat output file (captured from stdout)
            
        Returns:
            Dictionary of metric names to values
        """
        perf_metrics = {}
        
        if not os.path.exists(perf_output_file):
            logger.warning(f"Perf output file not found: {perf_output_file}")
            return perf_metrics
        
        # Check if file is empty
        file_size = os.path.getsize(perf_output_file)
        if file_size == 0:
            logger.warning(f"Perf output file is empty: {perf_output_file}")
            return perf_metrics
        
        try:
            with open(perf_output_file, 'r') as f:
                content = f.read()
            
            if not content.strip():
                logger.warning(f"Perf output file contains only whitespace: {perf_output_file}")
                return perf_metrics
            
            # Perf stat stdout format
            # Format: "value metric_name" or "value metric_name # comment"
            # Skip empty lines, comment lines, and header lines
            for line in content.split('\n'):
                original_line = line
                line = line.strip()
                # Skip empty lines
                if not line:
                    continue
                # Skip comment lines that start with #
                if line.startswith('#'):
                    continue
                # Skip "Performance counter stats" header line
                if 'Performance counter stats' in line:
                    continue
                # Parse "seconds time elapsed" line
                if 'seconds' in line.lower() and 'elapsed' in line.lower():
                    try:
                        # Format: "1.236200708 seconds time elapsed"
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part.lower() == 'seconds' and i > 0:
                                time_value = parts[i-1].replace(',', '')
                                perf_metrics['time_elapsed_seconds'] = float(time_value)
                                break
                    except (ValueError, IndexError):
                        pass
                    continue
                # Skip "seconds user/sys" lines (but not elapsed)
                if 'seconds' in line.lower() and ('user' in line.lower() or 'sys' in line.lower()):
                    continue
                # Skip lines with "Terminated" or other error messages
                if line.lower().startswith('failed') or 'terminated' in line.lower():
                    continue
                
                # Parse the main metric and any metrics in comments
                # Format: "value metric_name # comment_with_metrics"
                main_line = line
                comment_line = None
                if '#' in line:
                    # Split on # - keep both parts
                    parts_split = line.split('#', 1)
                    main_line = parts_split[0].strip()
                    comment_line = parts_split[1].strip()
                
                # Parse main metric
                parts = main_line.split()
                main_metric_name = None
                if len(parts) >= 2:
                    try:
                        # First part is the value (could be a number with commas or decimals)
                        value_str = parts[0].replace(',', '')
                        value = float(value_str)
                        
                        # Metric name is everything after the value
                        # Handle cases like "19,468.06 msec cpu-clock" -> "msec_cpu_clock"
                        # Or "258,778,271 cycles" -> "cycles"
                        metric_name_parts = parts[1:]
                        metric_name = '_'.join(metric_name_parts)
                        
                        # Normalize metric names (remove special chars, use underscores)
                        metric_name = metric_name.replace('/', '_').replace('-', '_')
                        main_metric_name = metric_name
                        
                        # Store the metric
                        perf_metrics[metric_name] = value
                        
                        # Store simplified names for common metrics
                        if 'cycles' in metric_name.lower():
                            perf_metrics['cycles'] = value
                        elif 'instructions' in metric_name.lower():
                            perf_metrics['instructions'] = value
                        elif 'branch_misses' in metric_name or 'branch-misses' in line:
                            perf_metrics['branch_misses'] = value
                        elif 'page_faults' in metric_name or 'page-faults' in line:
                            perf_metrics['page_faults'] = value
                    except (ValueError, IndexError):
                        pass
                
                # Parse metrics from comments
                # Format: "#   0.65  insn per cycle" or "#    3.49% of all branches"
                if comment_line:
                    comment_parts = comment_line.split()
                    if len(comment_parts) >= 2:
                        try:
                            # Try to parse first part as a number
                            comment_value_str = comment_parts[0].replace(',', '').replace('%', '')
                            comment_value = float(comment_value_str)
                            
                            # Check what type of metric it is based on keywords
                            comment_lower = comment_line.lower()
                            
                            if 'insn per cycle' in comment_lower or 'instructions per cycle' in comment_lower:
                                perf_metrics['instructions_per_cycle'] = comment_value
                            elif 'branch' in comment_lower and ('miss' in comment_lower or '%' in comment_line):
                                # Branch miss rate (percentage)
                                perf_metrics['branch_miss_rate_pct'] = comment_value
                            elif 'cpus utilized' in comment_lower:
                                perf_metrics['cpus_utilized'] = comment_value
                            elif '/sec' in comment_line:
                                # Rate metric - extract the metric name from the main line
                                # Format: "1,102 context-switches # 22.278 /sec"
                                # We'll use the main metric name to create a rate metric
                                if main_metric_name:
                                    perf_metrics[f"{main_metric_name}_per_sec"] = comment_value
                            elif 'ghz' in comment_lower:
                                perf_metrics['ghz'] = comment_value
                        except (ValueError, IndexError):
                            pass
        except Exception as e:
            logger.warning(f"Error parsing perf stat output: {e}")
        
        logger.debug(f"Parsed {len(perf_metrics)} perf metrics from {perf_output_file}")
        if perf_metrics:
            logger.debug(f"Sample metrics: {list(perf_metrics.keys())[:5]}")
        return perf_metrics
    
    def _populate_system_metrics(self, metrics: BenchmarkMetrics, window_number: int,
                                 actual_window_start: float, window_end_time: float) -> None:
        """Populate system metrics (power, C-state, CPU utilization, perf) into metrics object.
        
        This is a helper method to reduce code duplication across benchmark implementations.
        It:
        1. Parses power and CPU metrics from saved file
        2. Populates the BenchmarkMetrics object with these values
        3. Creates a system_metrics dictionary for history
        4. Reads perf metrics from saved JSON file and adds them to system_metrics
        
        Args:
            metrics: BenchmarkMetrics object to populate
            window_number: Current window number
            actual_window_start: Window start timestamp
            window_end_time: Window end timestamp
        """
        # Parse power and CPU metrics from saved file
        power_cpu_metrics = self.parse_system_metrics(window_number)
        if power_cpu_metrics:
            metrics.power_socket0_watts = power_cpu_metrics.get("power_socket0_watts")
            metrics.power_ram_watts = power_cpu_metrics.get("power_ram_watts")
            metrics.cstate_poll_pct = power_cpu_metrics.get("cstate_poll_pct")
            metrics.cstate_c1_pct = power_cpu_metrics.get("cstate_c1_pct")
            metrics.cstate_c1e_pct = power_cpu_metrics.get("cstate_c1e_pct")
            metrics.cstate_c6_pct = power_cpu_metrics.get("cstate_c6_pct")
            metrics.cpu_load_cores_pct = power_cpu_metrics.get("cpu_load_cores_pct")
            metrics.cpu_load_socket0_pct = power_cpu_metrics.get("cpu_load_socket0_pct")
        
        # Read perf info from saved JSON file
        perf_info_file = os.path.join(self.results_dir, f"window_{window_number}_perf_info.json")
        perf_start_time = None
        perf_end_time = None
        perf_metrics = {}
        
        if os.path.exists(perf_info_file):
            try:
                with open(perf_info_file, 'r') as f:
                    perf_info = json.load(f)
                perf_start_time = perf_info.get("perf_start_time")
                perf_end_time = perf_info.get("perf_end_time")
                perf_metrics = perf_info.get("perf_metrics", {})
            except Exception as e:
                logger.warning(f"Failed to read perf info from {perf_info_file}: {e}")
        
        # Store system metrics timestamps and data in extra_metrics for history
        system_metrics_dict = {
            "window_start_time": actual_window_start,
            "window_end_time": window_end_time,
            "perf_start_time": perf_start_time,
            "perf_end_time": perf_end_time,
            "system_metrics_start_time": power_cpu_metrics.get("collection_start_time") if power_cpu_metrics else None,
            "system_metrics_end_time": power_cpu_metrics.get("collection_end_time") if power_cpu_metrics else None,
            "power_socket0_watts": power_cpu_metrics.get("power_socket0_watts") if power_cpu_metrics else None,
            "power_ram_watts": power_cpu_metrics.get("power_ram_watts") if power_cpu_metrics else None,
            "cstate_poll_pct": power_cpu_metrics.get("cstate_poll_pct") if power_cpu_metrics else None,
            "cstate_c1_pct": power_cpu_metrics.get("cstate_c1_pct") if power_cpu_metrics else None,
            "cstate_c1e_pct": power_cpu_metrics.get("cstate_c1e_pct") if power_cpu_metrics else None,
            "cstate_c6_pct": power_cpu_metrics.get("cstate_c6_pct") if power_cpu_metrics else None,
            "cpu_load_cores_pct": power_cpu_metrics.get("cpu_load_cores_pct") if power_cpu_metrics else None,
            "cpu_load_socket0_pct": power_cpu_metrics.get("cpu_load_socket0_pct") if power_cpu_metrics else None,
        }
        
        metrics.extra_metrics["system_metrics"] = system_metrics_dict
        
        # Add perf metrics to extra_metrics and system_metrics
        if perf_metrics:
            metrics.extra_metrics.update(perf_metrics)
            system_metrics_dict["perf_metrics"] = perf_metrics
    
    def _parse_cores_spec(self, cores_spec: Optional[str]) -> Optional[Set[int]]:
        """Parse core specification string into set of integers.
        
        Args:
            cores_spec: Core specification like "0-3", "0,1,2", "all", or None
            
        Returns:
            Set of core numbers, or None if "all" or None (meaning all cores)
        """
        if cores_spec is None or cores_spec.lower() == "all":
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
    
    def _read_rapl_socket_energy(self, socket: int = 0) -> Optional[int]:
        """Read RAPL energy for a specific socket (package).
        
        Args:
            socket: Socket number (default 0)
            
        Returns:
            Energy in microjoules, or None if unavailable
        """
        try:
            rapl_base = Path("/sys/class/powercap/intel-rapl")
            if not rapl_base.exists():
                return None
            
            for domain_dir in rapl_base.glob("intel-rapl:*"):
                name_file = domain_dir / "name"
                if name_file.exists():
                    name = name_file.read_text().strip()
                    if name == f"package-{socket}":
                        energy_file = domain_dir / "energy_uj"
                        if energy_file.exists():
                            energy_uj = int(energy_file.read_text().strip())
                            return energy_uj
            return None
        except Exception as e:
            logger.debug(f"RAPL socket energy read failed: {e}")
            return None
    
    def _read_rapl_dram_energy(self, socket: int = 0) -> Optional[int]:
        """Read RAPL DRAM energy for a specific socket.
        
        Args:
            socket: Socket number (default 0)
            
        Returns:
            Energy in microjoules, or None if unavailable
        """
        try:
            rapl_base = Path("/sys/class/powercap/intel-rapl")
            if not rapl_base.exists():
                return None
            
            # Find package-N domain
            package_dir = None
            for domain_dir in rapl_base.glob("intel-rapl:*"):
                name_file = domain_dir / "name"
                if name_file.exists():
                    name = name_file.read_text().strip()
                    if name == f"package-{socket}":
                        package_dir = domain_dir
                        break
            
            if package_dir is None:
                return None
            
            # Find DRAM subdomain
            for subdomain_dir in package_dir.glob("intel-rapl:*"):
                name_file = subdomain_dir / "name"
                if name_file.exists():
                    name = name_file.read_text().strip()
                    if name == "dram":
                        energy_file = subdomain_dir / "energy_uj"
                        if energy_file.exists():
                            energy_uj = int(energy_file.read_text().strip())
                            return energy_uj
            return None
        except Exception as e:
            logger.debug(f"RAPL DRAM energy read failed: {e}")
            return None
    
    def _read_cstate_residency(self, cores_filter: Optional[Set[int]] = None) -> Dict[str, int]:
        """Read C-state residency times for specified cores.
        
        Args:
            cores_filter: Set of CPU core numbers to include, or None for all cores
            
        Returns:
            Dict with C-state names as keys and total time in microseconds
        """
        cstate_times = {}
        root = Path("/sys/devices/system/cpu")
        
        try:
            for cpu_dir in root.glob("cpu[0-9]*"):
                if not cpu_dir.is_dir():
                    continue
                
                try:
                    cpu_num = int(cpu_dir.name[3:])
                except ValueError:
                    continue
                
                # Skip if not in filter
                if cores_filter is not None and cpu_num not in cores_filter:
                    continue
                
                cpuidle_dir = cpu_dir / "cpuidle"
                if not cpuidle_dir.exists():
                    continue
                
                for state_dir in sorted(cpuidle_dir.glob("state*")):
                    name_file = state_dir / "name"
                    time_file = state_dir / "time"
                    
                    if not name_file.exists() or not time_file.exists():
                        continue
                    
                    try:
                        cstate_name = name_file.read_text().strip()
                        time_us = int(time_file.read_text().strip())
                        
                        if cstate_name not in cstate_times:
                            cstate_times[cstate_name] = 0
                        cstate_times[cstate_name] += time_us
                    except (ValueError, IOError):
                        continue
        except Exception as e:
            logger.debug(f"C-state residency read failed: {e}")
        
        return cstate_times
    
    def _read_cpu_stats(self) -> Dict[int, Dict[str, int]]:
        """Read CPU statistics from /proc/stat.
        
        Returns:
            Dict mapping CPU core number to stats dict.
            Keys: 'user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq'
        """
        stats = {}
        try:
            with open("/proc/stat", "r") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        continue
                    
                    parts = line.split()
                    if not parts:
                        continue
                    
                    cpu_name = parts[0]
                    if cpu_name == "cpu":
                        continue  # Skip overall CPU stats
                    
                    try:
                        cpu_num = int(cpu_name[3:])
                    except ValueError:
                        continue
                    
                    if len(parts) >= 5:
                        stats[cpu_num] = {
                            'user': int(parts[1]),
                            'nice': int(parts[2]),
                            'system': int(parts[3]),
                            'idle': int(parts[4]),
                            'iowait': int(parts[5]) if len(parts) > 5 else 0,
                            'irq': int(parts[6]) if len(parts) > 6 else 0,
                            'softirq': int(parts[7]) if len(parts) > 7 else 0,
                        }
        except Exception as e:
            logger.debug(f"CPU stats read failed: {e}")
        
        return stats
    
    def _calculate_cpu_utilization(self, stats1: Dict[int, Dict[str, int]], 
                                   stats2: Dict[int, Dict[str, int]],
                                   cores_filter: Optional[Set[int]] = None,
                                   socket_filter: Optional[int] = None) -> Optional[float]:
        """Calculate average CPU utilization percentage between two stat readings.
        
        Args:
            stats1: First CPU stats reading
            stats2: Second CPU stats reading
            cores_filter: Set of CPU cores to average, or None for all
            socket_filter: Socket number to filter (0 or 1), or None for all
        
        Returns:
            Average CPU utilization percentage (0-100), or None if calculation fails
        """
        if not stats1 or not stats2:
            return None
        
        utilizations = []
        
        for cpu_num in set(stats1.keys()) & set(stats2.keys()):
            if cores_filter is not None and cpu_num not in cores_filter:
                continue
            
            # Filter by socket
            if socket_filter is not None:
                try:
                    cpu_socket = int(Path(f"/sys/devices/system/cpu/cpu{cpu_num}/topology/physical_package_id").read_text().strip())
                    if cpu_socket != socket_filter:
                        continue
                except Exception:
                    continue
            
            s1 = stats1[cpu_num]
            s2 = stats2[cpu_num]
            
            total1 = s1['user'] + s1['nice'] + s1['system'] + s1['idle'] + s1['iowait'] + s1['irq'] + s1['softirq']
            total2 = s2['user'] + s2['nice'] + s2['system'] + s2['idle'] + s2['iowait'] + s2['irq'] + s2['softirq']
            
            idle1 = s1['idle']
            idle2 = s2['idle']
            
            total_diff = total2 - total1
            idle_diff = idle2 - idle1
            
            if total_diff > 0:
                utilization = 100.0 * (1.0 - (idle_diff / total_diff))
                utilizations.append(utilization)
        
        if not utilizations:
            return None
        
        return sum(utilizations) / len(utilizations)
    
    def _collect_system_metrics_thread(self, window_number: int, duration: int, metrics_file: str, window_start_time: float):
        """Background thread that collects power and CPU metrics for a specific window.
        
        This thread:
        - For long windows (> 3s): Starts 1s after window start, ends 1s before window end
        - For short windows (<= 3s): Minimal delay, collects for the full window
        - Collects samples every 1 second during this period
        - Saves all samples to a JSON file
        
        CPU metrics are collected by:
        1. Reading /proc/stat counters (cumulative since boot)
        2. Waiting 1 second
        3. Reading counters again
        4. Calculating: utilization = (total_diff - idle_diff) / total_diff * 100
        5. This gives CPU load percentage over that 1-second period
        
        Power metrics are collected by:
        1. Reading RAPL energy counters (cumulative)
        2. Waiting 1 second
        3. Reading counters again
        4. Calculating: power = (energy_diff) / time / 1e6 (Watts)
        
        C-state metrics are collected by:
        1. Reading C-state residency times (cumulative)
        2. Waiting 1 second
        3. Reading times again
        4. Calculating percentages of time spent in each C-state
        
        All samples are averaged at the end to get window-wide metrics.
        
        Args:
            window_number: Window number for this collection
            duration: Total window duration in seconds
            metrics_file: Path to file where metrics will be saved
            window_start_time: Absolute time when the window started
        """
        # Parse cores filter from pin_to_cores
        cores_filter = self._parse_cores_spec(self.pin_to_cores)
        
        # Adjust timing based on window duration
        # For short windows (< 4s), reduce/skip delays to still get metrics
        if duration <= 3:
            # Short window: minimal 0.2s delay, no buffer, 1 sample covering the window
            initial_delay = 0.2
            end_buffer = 0.0  # No buffer zone for short windows
            logger.debug(f"Short window ({duration}s): using minimal delay ({initial_delay}s), no buffer")
        else:
            # Normal window: 1s delay, 1s buffer
            initial_delay = 1.0
            end_buffer = 1.0
        
        # Wait initial delay
        time.sleep(initial_delay)
        
        # Collection duration: window duration - delay - buffer
        collection_duration = max(1, int(duration - initial_delay - end_buffer))
        num_samples = collection_duration
        
        collection_start_time = time.time()
        samples = []
        
        # Collect samples every 1 second
        # Each sample represents metrics over a 1-second period
        for i in range(num_samples):
            sample_start_time = time.time()
            
            # Check if we should stop (only for normal windows with buffer)
            if end_buffer > 0:
                elapsed_from_start = sample_start_time - window_start_time
                if elapsed_from_start >= (duration - end_buffer):
                    # We've reached the buffer zone, stop collecting
                    break
            
            # Read initial values (cumulative counters)
            socket0_e1 = self._read_rapl_socket_energy(0)
            dram_e1 = self._read_rapl_dram_energy(0)
            cstate_t1 = self._read_cstate_residency(cores_filter=cores_filter)
            cpu_stats1 = self._read_cpu_stats()
            
            # Log warnings for unavailable metrics (only on first sample)
            if i == 0:
                if socket0_e1 is None:
                    logger.warning("RAPL socket power measurement unavailable (no intel-rapl sysfs or no permission)")
                if dram_e1 is None:
                    logger.debug("RAPL DRAM power measurement unavailable")
                if not cstate_t1:
                    logger.warning("C-state residency measurement unavailable (no cpuidle sysfs)")
                if not cpu_stats1:
                    logger.warning("CPU stats unavailable (/proc/stat read failed)")
            
            # Wait 1 second
            time.sleep(1.0)
            
            # Read final values (cumulative counters)
            socket0_e2 = self._read_rapl_socket_energy(0)
            dram_e2 = self._read_rapl_dram_energy(0)
            cstate_t2 = self._read_cstate_residency(cores_filter=cores_filter)
            cpu_stats2 = self._read_cpu_stats()
            
            # Calculate power (Watts) - difference over 1 second
            # RAPL energy counters are cumulative, so we take the difference
            watts = None
            if socket0_e1 is not None and socket0_e2 is not None:
                if socket0_e2 >= socket0_e1:
                    watts = (socket0_e2 - socket0_e1) / 1.0 / 1e6
                else:
                    watts = socket0_e2 / 1.0 / 1e6  # Wrapped (counter overflow)
            
            ram_watts = None
            if dram_e1 is not None and dram_e2 is not None:
                if dram_e2 >= dram_e1:
                    ram_watts = (dram_e2 - dram_e1) / 1.0 / 1e6
                else:
                    ram_watts = dram_e2 / 1.0 / 1e6  # Wrapped
            
            # Calculate C-state percentages (difference over 1 second)
            # C-state residency counters are cumulative, so we take the difference
            cstate_pcts = {}
            if cstate_t1 and cstate_t2:
                time_diffs = {}
                for cstate in set(cstate_t1.keys()) | set(cstate_t2.keys()):
                    t1_val = cstate_t1.get(cstate, 0)
                    t2_val = cstate_t2.get(cstate, 0)
                    if t2_val >= t1_val:
                        time_diffs[cstate] = t2_val - t1_val
                    else:
                        time_diffs[cstate] = t2_val  # Wrapped (counter overflow)
                
                total_us = sum(time_diffs.values())
                if total_us > 0:
                    for cstate, time_us in time_diffs.items():
                        cstate_pcts[cstate] = (time_us / total_us) * 100.0
            
            # Calculate CPU load (difference over 1 second)
            # /proc/stat counters are cumulative, so we take the difference
            # Utilization = (non-idle time) / total time * 100
            cpu_load_cores = None
            cpu_load_socket0 = None
            
            if cpu_stats1 and cpu_stats2:
                if cores_filter is not None:
                    cpu_load_cores = self._calculate_cpu_utilization(
                        cpu_stats1, cpu_stats2, cores_filter=cores_filter
                    )
                cpu_load_socket0 = self._calculate_cpu_utilization(
                    cpu_stats1, cpu_stats2, socket_filter=0
                )
            
            # Store sample (each sample is a 1-second measurement)
            sample = {
                "power_socket0_watts": watts,
                "power_ram_watts": ram_watts,
                "cstate_poll_pct": cstate_pcts.get("POLL"),
                "cstate_c1_pct": cstate_pcts.get("C1"),
                "cstate_c1e_pct": cstate_pcts.get("C1E"),
                "cstate_c6_pct": cstate_pcts.get("C6"),
                "cpu_load_cores_pct": cpu_load_cores,
                "cpu_load_socket0_pct": cpu_load_socket0,
                "timestamp": sample_start_time,
                "sample_index": i
            }
            samples.append(sample)
            
            # Write samples to file incrementally (for safety and real-time monitoring)
            try:
                with open(metrics_file, 'w') as f:
                    json.dump({
                        "window_number": window_number,
                        "window_start_time": window_start_time,
                        "window_duration": duration,
                        "initial_delay": initial_delay,
                        "end_buffer": end_buffer,
                        "collection_start_time": collection_start_time,
                        "collection_end_time": window_start_time + duration - end_buffer,
                        "samples": samples
                    }, f, indent=2)
            except Exception as e:
                logger.warning(f"Error writing metrics file: {e}")
        
        if len(samples) == 0:
            logger.warning(f"No system metrics samples collected for window {window_number} (duration={duration}s)")
        else:
            logger.debug(f"Completed system metrics collection for window {window_number}: {len(samples)} samples")
    
    def start_system_measurement(self, window_number: int, duration: int) -> float:
        """Start system metrics collection for a window.
        
        Spawns a background thread that collects power and CPU metrics.
        The thread:
        - Starts 1 second after window start (delayed to avoid startup overhead)
        - Ends 1 second before window end (buffer to avoid teardown overhead)
        - Collects samples every 1 second during this period
        - Saves all samples to a JSON file for later parsing
        
        The measurement window overlaps with the benchmark execution:
        - Benchmark runs for the full duration
        - Metrics are collected from 1s to (duration-1s)
        - This ensures we measure the steady-state workload
        
        Args:
            window_number: Current window number
            duration: Window duration in seconds
            
        Returns:
            Window start timestamp (absolute time)
        """
        metrics_file = os.path.join(self.results_dir, f"window_{window_number}_metrics.json")
        window_start_time = time.time()
        
        # Start collection thread (one thread per window)
        thread = threading.Thread(
            target=self._collect_system_metrics_thread,
            args=(window_number, duration, metrics_file, window_start_time),
            daemon=True
        )
        thread.start()
        
        # Log appropriate collection timing based on window duration
        if duration <= 3:
            logger.info(f"Started system metrics collection for window {window_number} (duration: {duration}s, "
                       f"short window mode: minimal delay, 1 sample)")
        else:
            logger.info(f"Started system metrics collection for window {window_number} (duration: {duration}s, "
                       f"collection period: 1s to {duration-1}s)")
        
        return window_start_time
    
    def parse_system_metrics(self, window_number: int) -> Dict[str, Any]:
        """Parse power and CPU metrics for a specific window from the saved file.
        
        This method reads the metrics file saved by the collection thread and
        calculates averages across all samples collected during the measurement window.
        
        The measurement window overlaps with the benchmark execution:
        - Starts 1 second after window start (to avoid startup overhead)
        - Ends 1 second before window end (to avoid teardown overhead)
        - Samples are collected every 1 second during this period
        
        Args:
            window_number: Window number to parse metrics for
            
        Returns:
            Dictionary with averaged power and CPU metrics, plus timestamps
        """
        metrics_file = os.path.join(self.results_dir, f"window_{window_number}_metrics.json")
        
        if not os.path.exists(metrics_file):
            logger.debug(f"Metrics file not found for window {window_number}: {metrics_file}")
            return {}
        
        try:
            with open(metrics_file, 'r') as f:
                data = json.load(f)
            
            samples = data.get('samples', [])
            if not samples:
                logger.debug(f"No samples found in metrics file for window {window_number}")
                return {}
            
            # Calculate averages across all samples
            # Each sample represents a 1-second measurement period
            def avg(field_name: str) -> Optional[float]:
                values = [s.get(field_name) for s in samples if s.get(field_name) is not None]
                return sum(values) / len(values) if values else None
            
            metrics = {
                "power_socket0_watts": avg("power_socket0_watts"),
                "power_ram_watts": avg("power_ram_watts"),
                "cstate_poll_pct": avg("cstate_poll_pct"),
                "cstate_c1_pct": avg("cstate_c1_pct"),
                "cstate_c1e_pct": avg("cstate_c1e_pct"),
                "cstate_c6_pct": avg("cstate_c6_pct"),
                "cpu_load_cores_pct": avg("cpu_load_cores_pct"),
                "cpu_load_socket0_pct": avg("cpu_load_socket0_pct"),
                # Include timestamps from the metrics file
                "window_start_time": data.get("window_start_time"),
                "window_duration": data.get("window_duration"),
                "collection_start_time": data.get("collection_start_time"),  # After 1s delay
                "collection_end_time": data.get("collection_end_time"),  # 1s before end
            }
            
            logger.debug(
                f"Parsed {len(samples)} samples for window {window_number}: "
                f"power={metrics.get('power_socket0_watts')}W, "
                f"cpu_load={metrics.get('cpu_load_cores_pct')}%"
            )
            
            return metrics
        except Exception as e:
            logger.warning(f"Error parsing metrics file for window {window_number}: {e}")
            return {}

