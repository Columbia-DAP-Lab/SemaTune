#!/usr/bin/env python3
"""
Tailbench benchmark implementation for the OS tuner.

Tailbench is a benchmark suite for latency-critical applications.
Supports all 8 applications: img-dnn, masstree, moses, shore, silo, specjbb, sphinx, xapian.

This implementation runs benchmarks continuously and collects interval-based metrics.
"""

import os
import subprocess
import time
import logging
import csv
import glob
import signal
import datetime
import pprint
import tempfile
from dateutil import parser as date_parser
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from ..benchmark import BenchmarkInterface, BenchmarkMetrics

logger = logging.getLogger(__name__)


@dataclass
class TailbenchApp:
    """Configuration for a Tailbench application."""
    name: str
    binary_name: str
    workdir: str
    requires_dataset: bool
    default_qps: int
    default_threads: int
    default_warmupreqs: int
    env_vars: Dict[str, str]  # Additional environment variables
    args: List[str]  # Command-line arguments


# Registry of all Tailbench applications
TAILBENCH_APPS = {
    "img-dnn": TailbenchApp(
        name="img-dnn",
        binary_name="img-dnn_integrated",
        workdir="img-dnn",
        requires_dataset=True,
        default_qps=500,
        default_threads=40,
        default_warmupreqs=5000,
        env_vars={
            "TBENCH_MNIST_DIR": "${DATA_ROOT}/img-dnn/mnist"
        },
        args=["-r", "40", "-f", "${DATA_ROOT}/img-dnn/models/model.xml", "-n", "100000000"]
    ),
    "masstree": TailbenchApp(
        name="masstree",
        binary_name="mttest_integrated",
        workdir="masstree",
        requires_dataset=False,
        default_qps=2000,
        default_threads=32,
        default_warmupreqs=40000,
        env_vars={},
        args=["-j32", "mycsba", "masstree"]
    ),
    "moses": TailbenchApp(
        name="moses",
        binary_name="bin/moses_integrated",
        workdir="moses",
        requires_dataset=True,
        default_qps=100,
        default_threads=1,
        default_warmupreqs=1000,
        env_vars={},
        args=["-f", "${DATA_ROOT}/moses/moses.ini"]
    ),
    "shore": TailbenchApp(
        name="shore",
        binary_name="shore-kits/shore_kits_server_integrated",
        workdir="shore",
        requires_dataset=True,
        default_qps=2000,
        default_threads=32,
        default_warmupreqs=10000,
        env_vars={
            "TBENCH_SHORE_CONF": "${DATA_ROOT}/shore/shore.conf"
        },
        args=[]
    ),
    "silo": TailbenchApp(
        name="silo",
        binary_name="out-perf.masstree/benchmarks/dbtest_integrated",
        workdir="silo",
        requires_dataset=False,
        default_qps=1000,
        default_threads=32,
        default_warmupreqs=20000,
        env_vars={
            "LD_LIBRARY_PATH": "${WORKDIR}/third-party/lz4:${LD_LIBRARY_PATH}"
        },
        args=["--verbose", "--bench", "tpcc", "--num-threads", "32", 
              "--scale-factor", "4", "--retry-aborted-transactions", 
              "--ops-per-worker", "1000000000"]
    ),
    "specjbb": TailbenchApp(
        name="specjbb",
        binary_name="java",  # Special case: uses Java
        workdir="specjbb",
        requires_dataset=False,
        default_qps=5000,
        default_threads=32,
        default_warmupreqs=25000,
        env_vars={
            "CLASSPATH": "./build/dist/jbb.jar:./build/dist/check.jar:../harness/tbench.jar",
            "LD_LIBRARY_PATH": "../harness:${LD_LIBRARY_PATH}"
        },
        args=["-Djava.library.path=.", "-XX:ParallelGCThreads=32",
              "-XX:+UseSerialGC", "-XX:NewRatio=1", "-XX:NewSize=7000m",
              "-Xms10000m", "-Xmx10000m", "-Xrs", "spec.jbb.JBBmain",
              "-propfile", "SPECjbb_mt.props"]
    ),
    "sphinx": TailbenchApp(
        name="sphinx",
        binary_name="decoder_integrated",
        workdir="sphinx",
        requires_dataset=True,
        default_qps=1,
        default_threads=40,
        default_warmupreqs=10,
        env_vars={
            "LD_LIBRARY_PATH": "./sphinx-install/lib:${LD_LIBRARY_PATH}",
            "TBENCH_AN4_CORPUS": "${DATA_ROOT}/sphinx",
            "TBENCH_AUDIO_SAMPLES": "audio_samples"
        },
        args=["-t", "40"]
    ),
    "xapian": TailbenchApp(
        name="xapian",
        binary_name="xapian_integrated",
        workdir="xapian",
        requires_dataset=True,
        default_qps=50,
        default_threads=32,
        default_warmupreqs=2500,
        env_vars={
            "LD_LIBRARY_PATH": "${WORKDIR}/xapian-core-1.2.13/install/lib:${LD_LIBRARY_PATH}",
            "TBENCH_TERMS_FILE": "${DATA_ROOT}/xapian/terms.in"
        },
        args=["-n", "32", "-d", "/mydata/tailbench.inputs/xapian/wiki", "-r", "1000000000"]
    ),
}


class TailbenchBenchmark(BenchmarkInterface):
    """Tailbench continuous benchmark implementation."""
    
    def __init__(self, config):
        """Initialize Tailbench benchmark.
        
        Args:
            config: Configuration object (SimpleConfig)
        """
        super().__init__(config)
        
        # Get Tailbench-specific config
        self.app_name = getattr(config, 'tailbench_app', 'masstree')
        if self.app_name not in TAILBENCH_APPS:
            raise ValueError(f"Unknown Tailbench application: {self.app_name}. "
                           f"Available: {list(TAILBENCH_APPS.keys())}")
        
        self.app_config = TAILBENCH_APPS[self.app_name]
        
        # Override defaults with config values
        self.qps = getattr(config, 'tailbench_qps', self.app_config.default_qps)
        self.threads = getattr(config, 'tailbench_threads', self.app_config.default_threads)
        self.warmupreqs = getattr(config, 'tailbench_warmupreqs', self.app_config.default_warmupreqs)
        self.maxreqs = getattr(config, 'tailbench_maxreqs', 1000000000)  # Very high for continuous run
        self.minsleepns = getattr(config, 'tailbench_minsleepns', 10000)
        self.metrics_interval_sec = getattr(config, 'tailbench_metrics_interval_sec', 1)  # 1 second intervals
        output_dir_raw = getattr(config, 'tailbench_output_dir', None)  # Will be processed after repo_root is set
        
        # Paths
        # Ensure repo_root is absolute and valid
        if not self.repo_root:
            self.repo_root = os.getcwd()
        self.repo_root = os.path.abspath(self.repo_root)
        logger.info(f"Repo root resolved to: {self.repo_root}")
        
        # Now process output_dir with proper repo_root
        if output_dir_raw:
            # Expand ~ and make absolute
            output_dir_expanded = os.path.expanduser(output_dir_raw)
            if not os.path.isabs(output_dir_expanded):
                # If still relative after expansion, make it relative to repo_root
                self.output_dir = os.path.join(self.repo_root, output_dir_expanded)
            else:
                self.output_dir = output_dir_expanded
        else:
            self.output_dir = None
        
        # Handle paths that might be relative in config
        tailbench_root_raw = getattr(config, 'tailbench_root', 'deps/Tailbench/tailbench')
        if not os.path.isabs(tailbench_root_raw):
            self.tailbench_root = os.path.join(self.repo_root, tailbench_root_raw)
        else:
            self.tailbench_root = tailbench_root_raw
            
        data_root_raw = getattr(config, 'tailbench_data_root', 'deps/Tailbench/tailbench.inputs')
        if not os.path.isabs(data_root_raw):
            self.data_root = os.path.join(self.repo_root, data_root_raw)
        else:
            self.data_root = data_root_raw
            
        self.jdk_path = getattr(config, 'tailbench_jdk_path', '/usr/lib/jvm/java-8-openjdk-amd64')
        
        # App-specific parameters
        self.xapian_db_path = getattr(config, 'tailbench_xapian_db_path', None)
        self._resolved_xapian_db_path: Optional[str] = None
        self.silo_scale_factor = getattr(config, 'tailbench_silo_scale_factor', None)
        self.sphinx_audio_samples = getattr(config, 'tailbench_sphinx_audio_samples', None)
        
        # State tracking
        self.benchmark_process: Optional[subprocess.Popen] = None
        self.workdir = os.path.join(self.tailbench_root, self.app_config.workdir)
        self.window_start_timestamp = None
        self.interval_counter = 0
        self.file_timestamp_cache: Dict[str, datetime.datetime] = {}  # Cache for file timestamps
        self.benchmark_start_time: Optional[float] = None  # monotonic time when ROI phase started
        self._last_parsed_interval: int = -1  # Track last interval we consumed from log
        
        # Determine metrics directory (output_dir if set, otherwise workdir)
        self.metrics_dir = self.output_dir if self.output_dir else self.workdir
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
        
        logger.info(f"Initialized Tailbench benchmark: {self.app_name}")
        logger.info(f"  QPS: {self.qps}, Threads: {self.threads}")
        logger.info(f"  Workdir: {self.workdir}")
        logger.info(f"  Metrics interval: {self.metrics_interval_sec}s")
        logger.info(f"  Metrics directory: {self.metrics_dir}")
        logger.info(f"  MAXREQS: {self.maxreqs}, WARMUPREQS: {self.warmupreqs}, MINSLEEPNS: {self.minsleepns}")
    
    def _expand_env_vars(self, value: str) -> str:
        """Expand environment variables in a string."""
        value = value.replace("${DATA_ROOT}", self.data_root)
        value = value.replace("${JDK_PATH}", self.jdk_path)
        value = value.replace("${WORKDIR}", self.workdir)
        value = value.replace("${LD_LIBRARY_PATH}", os.environ.get("LD_LIBRARY_PATH", ""))
        return value
    
    def _build_env(self) -> Dict[str, str]:
        """Build environment variables for the benchmark."""
        env = os.environ.copy()
        
        # Core Tailbench environment variables (TBENCH_MAXREQS must fit 32-bit int)
        env["TBENCH_QPS"] = str(self.qps)
        maxreqs_capped = min(self.maxreqs, 2147483647)
        env["TBENCH_MAXREQS"] = str(maxreqs_capped)
        env["TBENCH_WARMUPREQS"] = str(self.warmupreqs)
        env["TBENCH_MINSLEEPNS"] = str(self.minsleepns)
        env["TBENCH_METRICS_INTERVAL_SEC"] = str(self.metrics_interval_sec)
        
        # Output directory (if specified)
        if self.output_dir:
            env["TBENCH_OUTPUT_DIR"] = self.output_dir
        
        # Application-specific environment variables
        for key, value in self.app_config.env_vars.items():
            # Override with config values if provided
            if key == "TBENCH_AUDIO_SAMPLES" and self.sphinx_audio_samples:
                env[key] = self.sphinx_audio_samples
            else:
                env[key] = self._expand_env_vars(value)
        
        # Special handling for JDK path
        if self.app_name == "specjbb":
            env["PATH"] = f"{self.jdk_path}/bin:{env.get('PATH', '')}"
        
        return env

    def _is_valid_xapian_db_path(self, db_path: str) -> bool:
        """Return True if the path looks like a valid Tailbench Xapian database."""
        marker_file = os.path.join(db_path, "iamchert")
        return os.path.isdir(db_path) and os.path.isfile(marker_file)

    def _resolve_xapian_db_path(self) -> str:
        """Resolve Xapian DB path with fallback to data_root when needed."""
        if self._resolved_xapian_db_path is not None:
            return self._resolved_xapian_db_path

        fallback_path = os.path.abspath(os.path.join(self.data_root, "xapian", "wiki"))
        configured_path = None

        if self.xapian_db_path:
            configured_path = self._expand_env_vars(self.xapian_db_path)
            if not os.path.isabs(configured_path):
                configured_path = os.path.abspath(os.path.join(self.repo_root, configured_path))

        if configured_path:
            if self._is_valid_xapian_db_path(configured_path):
                self._resolved_xapian_db_path = configured_path
            elif self._is_valid_xapian_db_path(fallback_path):
                logger.warning(
                    "Configured xapian DB path is invalid or missing Chert marker: %s. "
                    "Falling back to %s",
                    configured_path,
                    fallback_path,
                )
                self._resolved_xapian_db_path = fallback_path
            else:
                self._resolved_xapian_db_path = configured_path
        else:
            self._resolved_xapian_db_path = fallback_path

        return self._resolved_xapian_db_path
    
    def _build_command(self) -> List[str]:
        """Build the command to run the benchmark."""
        cmd = [f"./{self.app_config.binary_name}"]
        
        args = self.app_config.args
        i = 0
        while i < len(args):
            arg = args[i]
            expanded_arg = self._expand_env_vars(arg)
            
            # Replace thread-specific arguments with configured value
            if self.app_name == "masstree" and expanded_arg.startswith("-j"):
                # Masstree uses -j<threads>
                cmd.append(f"-j{self.threads}")
                i += 1
                continue
                
            elif self.app_name == "xapian" and expanded_arg == "-n":
                # Xapian uses -n <threads>
                cmd.append("-n")
                cmd.append(str(self.threads))
                i += 2  # Skip -n and the original thread count
                continue
                
            elif self.app_name == "sphinx" and expanded_arg == "-t":
                # Sphinx uses -t <threads>
                cmd.append("-t")
                cmd.append(str(self.threads))
                i += 2
                continue
                
            elif self.app_name == "silo" and expanded_arg == "--num-threads":
                # Silo uses --num-threads <N>
                cmd.append("--num-threads")
                cmd.append(str(self.threads))
                i += 2
                continue
                
            elif self.app_name == "silo" and expanded_arg == "--scale-factor":
                # Silo uses --scale-factor <N>
                cmd.append("--scale-factor")
                cmd.append(str(self.silo_scale_factor if self.silo_scale_factor is not None else 4))
                i += 2
                continue
                
            elif self.app_name == "xapian" and expanded_arg == "-d":
                # Xapian uses -d <db_path>; must be absolute (process cwd is workdir)
                cmd.append("-d")
                cmd.append(self._resolve_xapian_db_path())
                i += 2
                continue
                
            elif self.app_name == "img-dnn" and expanded_arg == "-r":
                # img-dnn uses -r <threads>
                cmd.append("-r")
                cmd.append(str(self.threads))
                i += 2
                continue
                
            elif self.app_name == "specjbb" and expanded_arg.startswith("-XX:ParallelGCThreads="):
                # SpecJBB uses -XX:ParallelGCThreads=<threads>
                cmd.append(f"-XX:ParallelGCThreads={self.threads}")
                i += 1
                continue
            
            # Default case: just append the arg
            cmd.append(expanded_arg)
            i += 1
        
        # Wrap with taskset if CPU pinning is enabled
        cmd = self._wrap_with_taskset(cmd)
        
        return cmd
    
    def _cleanup_old_metrics(self):
        """Clean up old interval metrics files."""
        old_files = glob.glob(os.path.join(self.metrics_dir, "lats_interval_*.csv"))
        for f in old_files:
            try:
                os.remove(f)
                logger.debug(f"Removed old metrics file: {f}")
            except Exception as e:
                logger.warning(f"Failed to remove {f}: {e}")
        
        # Also clean up lats.bin
        lats_bin = os.path.join(self.metrics_dir, "lats.bin")
        if os.path.exists(lats_bin):
            try:
                os.remove(lats_bin)
            except Exception:
                pass
    
    def pre_execute(self) -> bool:
        """Start the continuous benchmark.
        
        This starts the benchmark process which will run continuously,
        emitting interval metrics every TBENCH_METRICS_INTERVAL_SEC seconds.
        
        Returns:
            True if setup succeeded, False otherwise
        """
        if self.benchmark_process is not None:
            logger.warning("Benchmark process already running")
            return True
        
        logger.info(f"Starting continuous Tailbench benchmark: {self.app_name}")
        
        # Sleep for 12 seconds to allow system to stabilize
        logger.info("Sleeping for 12 seconds to allow system to stabilize...")
        time.sleep(12)
        logger.info("Sleep complete, proceeding with benchmark startup")
        
        # Clean up old metrics
        self._cleanup_old_metrics()
        
        # Build environment and command
        env = self._build_env()
        cmd = self._build_command()
        
        logger.info(f"Running command: {' '.join(cmd)}")
        logger.info(f"In directory: {self.workdir}")
        
        # Log critical environment variables for debugging
        if self.app_name == 'xapian':
            terms_file = env.get('TBENCH_TERMS_FILE')
            db_path = self._resolve_xapian_db_path()
            logger.info(f"TBENCH_TERMS_FILE: {terms_file}")
            logger.info(f"XAPIAN_DB_PATH: {db_path}")
            logger.info(f"LD_LIBRARY_PATH: {env.get('LD_LIBRARY_PATH')}")
            
            # Verify terms file exists
            if not terms_file or not os.path.isfile(terms_file):
                logger.error(f"Terms file not found at: {terms_file}")
                logger.error(f"Current CWD: {os.getcwd()}")
                logger.error(f"Repo root: {self.repo_root}")
                logger.error(
                    "Fix by setting tailbench_data_root/tailbench_xapian_db_path to a valid "
                    "Tailbench dataset location."
                )
                return False

            if not os.path.isdir(db_path):
                logger.error(f"Xapian DB directory not found: {db_path}")
                logger.error(
                    "Fix by setting tailbench_xapian_db_path to an existing directory, "
                    "or ensure tailbench_data_root contains xapian/wiki."
                )
                return False

            marker_file = os.path.join(db_path, "iamchert")
            if not os.path.isfile(marker_file):
                logger.error(
                    f"Xapian DB directory is not in expected Chert format (missing: {marker_file})"
                )
                logger.error(
                    "This usually means the DB path is wrong or points to a non-Tailbench Xapian index."
                )
                return False
        
        # Start the benchmark process
        # Redirect stdout/stderr to log file to prevent pipe buffer overflow
        # (--verbose mode generates lots of output that can fill 64KB pipe buffer)
        # Use output_dir or temp dir so log is writable (workdir may be root-owned)
        # Wrap with stdbuf -oL to force line-buffered stdout so interval stats
        # are flushed to disk immediately (otherwise C library fully buffers
        # when stdout is a file, and we can't read intervals during a window).
        try:
            log_dir = self.output_dir if self.output_dir else tempfile.gettempdir()
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, f"tailbench_{self.app_name}_{os.getpid()}_output.log")
            self.log_file = open(log_file_path, "w")
            logger.info(f"Redirecting benchmark output to {log_file_path}")
            
            # Wrap command with stdbuf to force line-buffered stdout
            stdbuf_cmd = ["stdbuf", "-oL"] + cmd
            logger.info(f"Using stdbuf for line-buffered output: {' '.join(stdbuf_cmd)}")
            
            self.benchmark_process = subprocess.Popen(
                stdbuf_cmd,
                cwd=self.workdir,
                env=env,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,  # Redirect stderr to stdout (log file)
                text=True,
                preexec_fn=os.setsid  # Create new process group for clean termination
            )
            
            # Wait a bit to ensure it starts successfully
            time.sleep(5)
            
            # Check if process is still running
            if self.benchmark_process.poll() is not None:
                # Process exited early, close log file and read it
                self.log_file.close()
                with open(log_file_path, "r") as f:
                    output = f.read()
                logger.error(f"Benchmark failed to start!")
                logger.error(f"Output:\n{output[-2000:] if len(output) > 2000 else output}")  # Last 2000 chars
                self.benchmark_process = None
                self.log_file = None
                return False
            
            logger.info(f"Benchmark process started successfully (PID: {self.benchmark_process.pid})")
            
            # Wait for warmup to complete
            warmup_duration = self.warmupreqs / self.qps
            logger.info(f"Waiting {warmup_duration:.1f}s for warmup to complete...")
            time.sleep(warmup_duration + 2)  # Add 2s buffer
            
            logger.info("Warmup complete, ROI phase started")
            self.interval_counter = 0
            self.benchmark_start_time = time.time()  # Track when ROI phase starts
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start benchmark: {e}")
            self.benchmark_process = None
            return False
    
    def _read_interval_metrics_file(self, csv_file: str) -> Optional[Dict[str, Any]]:
        """Read metrics from a single interval CSV file.
        
        Args:
            csv_file: Path to the CSV file
            
        Returns:
            Dictionary of metrics, or None if failed/empty
        """
        try:
            with open(csv_file, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
                if not rows:
                    logger.warning(f"Empty CSV file: {csv_file}")
                    return None
                
                # Should only be one data row per file
                row = rows[0]
                
                # Parse timestamp if present
                timestamp = None
                if 'timestamp' in row:
                    try:
                        timestamp = date_parser.parse(row['timestamp'])
                    except Exception as e:
                        logger.debug(f"Failed to parse timestamp '{row['timestamp']}': {e}")
                
                # Convert string values to float
                metrics = {}
                for key, value in row.items():
                    if key in ['interval', 'timestamp', 'elapsed_sec']:
                        continue
                    try:
                        metrics[key] = float(value)
                    except (ValueError, TypeError):
                        metrics[key] = 0.0
                
                if timestamp:
                    metrics['parsed_timestamp'] = timestamp
                
                return metrics
                
        except Exception as e:
            logger.warning(f"Failed to read {csv_file}: {e}")
            return None

    def _parse_log_for_intervals(self) -> List[Dict[str, Any]]:
        """Parse the benchmark log file for interval statistics blocks.
        
        Parses blocks like:
            === Interval 0 Statistics ===
              Elapsed: 1.001s | Requests: 140
              Sojourn (end-to-end): mean=0.550ms p50=0.520ms p95=0.915ms p99=1.035ms max=1.097ms
              Service time: mean=0.409ms p95=0.776ms
              Queue time: mean=0.141ms p95=0.186ms
        
        Returns:
            List of dicts with keys: interval, elapsed_s, count, sojourn_mean_ms,
            sojourn_p50_ms, sojourn_p95_ms, sojourn_p99_ms, sojourn_max_ms
        """
        import re
        
        if not hasattr(self, 'log_file') or self.log_file is None:
            return []
        
        log_path = self.log_file.name
        try:
            # Flush the Python file object and sync to disk so we read the latest content
            self.log_file.flush()
            os.fsync(self.log_file.fileno())
        except Exception:
            pass  # Best effort
        
        try:
            with open(log_path, 'r') as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read benchmark log file {log_path}: {e}")
            return []
        
        intervals = []
        
        # Match each interval block
        interval_pattern = re.compile(
            r'=== Interval (\d+) Statistics ===\s*'
            r'Elapsed:\s*([\d.]+)s\s*\|\s*Requests:\s*(\d+)\s*'
            r'Sojourn \(end-to-end\):\s*mean=([\d.]+)ms\s+p50=([\d.]+)ms\s+p95=([\d.]+)ms\s+p99=([\d.]+)ms\s+max=([\d.]+)ms',
            re.MULTILINE
        )
        
        for m in interval_pattern.finditer(content):
            intervals.append({
                'interval': int(m.group(1)),
                'elapsed_s': float(m.group(2)),
                'count': float(m.group(3)),
                'sojourn_mean_ms': float(m.group(4)),
                'sojourn_p50_ms': float(m.group(5)),
                'sojourn_p95_ms': float(m.group(6)),
                'sojourn_p99_ms': float(m.group(7)),
                'sojourn_max_ms': float(m.group(8)),
            })
        
        logger.debug(f"Parsed {len(intervals)} interval blocks from log file")
        return intervals

    def _collect_metrics_in_window(self, start_dt: datetime.datetime, end_dt: datetime.datetime) -> List[Dict[str, float]]:
        """Collect metrics from files that fall within the time window.
        
        Args:
            start_dt: Window start datetime
            end_dt: Window end datetime
            
        Returns:
            List of metrics dictionaries
        """
        metrics_list = []
        
        # glob all interval files
        pattern = os.path.join(self.metrics_dir, "lats_interval_*.csv")
        files = glob.glob(pattern)
        
        logger.info(f"Scanning {len(files)} metrics files for window [{start_dt} to {end_dt}]")
        
        processed_count = 0
        match_count = 0
        
        for csv_file in files:
            # Check cache first
            file_dt = self.file_timestamp_cache.get(csv_file)
            metrics = None
            
            if file_dt is None:
                # Not in cache, read file
                metrics = self._read_interval_metrics_file(csv_file)
                if metrics and 'parsed_timestamp' in metrics:
                    file_dt = metrics['parsed_timestamp']
                    self.file_timestamp_cache[csv_file] = file_dt
                else:
                    # Fallback: try to infer from file modification time if timestamp missing
                    try:
                        mtime = os.path.getmtime(csv_file)
                        file_dt = datetime.datetime.fromtimestamp(mtime)
                        self.file_timestamp_cache[csv_file] = file_dt
                    except Exception:
                        continue
            
            # Check if file falls in window
            if file_dt and start_dt <= file_dt <= end_dt:
                if metrics is None:
                     # We had it cached, so we need to read the content now
                     metrics = self._read_interval_metrics_file(csv_file)
                
                if metrics:
                    # Remove internal fields before adding
                    if 'parsed_timestamp' in metrics:
                        del metrics['parsed_timestamp']
                    metrics_list.append(metrics)
                    match_count += 1
            
            processed_count += 1
            
        logger.info(f"Found {match_count} files in window out of {processed_count} checked")
        return metrics_list

    def _aggregate_metrics(self, metrics_list: List[Dict[str, float]]) -> Dict[str, float]:
        """Aggregate metrics from multiple intervals.
        
        For latency metrics, we compute weighted averages based on request count.
        
        Args:
            metrics_list: List of metrics dictionaries from individual intervals
        
        Returns:
            Aggregated metrics dictionary
        """
        if not metrics_list:
            logger.warning("No metrics to aggregate")
            return {
                "count": 0.0,
                "sojourn_mean_ms": 0.0,
                "sojourn_p50_ms": 0.0,
                "sojourn_p95_ms": 0.0,
                "sojourn_p99_ms": 0.0,
                "sojourn_max_ms": 0.0,
            }
        
        # Calculate total count and weighted sums
        total_count = sum(m.get('count', 0) for m in metrics_list)
        
        if total_count == 0:
            logger.warning("Total request count is zero")
            return {"count": 0.0, "sojourn_mean_ms": 0.0}
        
        # Compute weighted averages for means
        sojourn_mean = sum(m.get('sojourn_mean_ms', 0) * m.get('count', 0) 
                          for m in metrics_list) / total_count
        
        # For percentiles and max, take the worst (max) across all intervals
        sojourn_p50 = max(m.get('sojourn_p50_ms', 0) for m in metrics_list)
        sojourn_p95 = max(m.get('sojourn_p95_ms', 0) for m in metrics_list)
        sojourn_p99 = max(m.get('sojourn_p99_ms', 0) for m in metrics_list)
        sojourn_max = max(m.get('sojourn_max_ms', 0) for m in metrics_list)
        
        # Calculate throughput (requests per second)
        # If individual intervals have throughput_rps, use the average of those
        # Otherwise, calculate from total count and duration
        if all('throughput_rps' in m for m in metrics_list):
            throughput = sum(m.get('throughput_rps', 0) for m in metrics_list) / len(metrics_list)
        else:
            window_duration = len(metrics_list) * self.metrics_interval_sec
            throughput = total_count / window_duration if window_duration > 0 else 0
        
        aggregated = {
            "count": total_count,
            "throughput": throughput,
            "sojourn_mean_ms": sojourn_mean,
            "sojourn_p50_ms": sojourn_p50,
            "sojourn_p95_ms": sojourn_p95,
            "sojourn_p99_ms": sojourn_p99,
            "sojourn_max_ms": sojourn_max,
            "intervals_aggregated": len(metrics_list),
        }
        
        logger.info(f"Aggregated {len(metrics_list)} intervals: "
                   f"count={total_count:.0f}, throughput={throughput:.1f} req/s, "
                   f"p95={sojourn_p95:.2f}ms, p99={sojourn_p99:.2f}ms")
        
        return aggregated
    
    def execute_window(self, window_number: int, duration: int) -> BenchmarkMetrics:
        """Collect metrics for a measurement window.
        
        The benchmark is already running continuously. This method:
        1. Records start time
        2. Starts system metrics collection
        3. Waits for the window duration
        4. Records end time
        5. Parses all logfiles to find those within the window
        6. Returns aggregated metrics
        
        Args:
            window_number: Current iteration/window number (1-indexed)
            duration: Duration of the measurement window in seconds
            
        Returns:
            BenchmarkMetrics with aggregated metrics from this window
        """
        logger.info(f"Collecting Tailbench metrics for window {window_number} ({duration}s)")
        
        # Check if benchmark is still running
        if self.benchmark_process is None or self.benchmark_process.poll() is not None:
            logger.error("Benchmark process is not running!")
            if self.benchmark_process:
                stdout, stderr = self.benchmark_process.communicate()
                logger.error(f"Process output:\nstdout: {stdout}\nstderr: {stderr}")
            raise RuntimeError("Benchmark process died unexpectedly")
        
        # Record start time for window alignment
        # We use datetime for comparison with parsed log timestamps
        window_start_dt = datetime.datetime.now()
        window_start_time = time.time()
        
        logger.info(f"Window {window_number} started at {window_start_dt}")
        
        # Start system metrics collection (non-blocking - runs in background thread)
        self.start_system_measurement(window_number, duration)
        
        # Start perf metrics collection (now non-blocking - returns immediately)
        perf_info = self.collect_perf_metrics(window_number, duration)
        
        # Wait for the window duration while metrics collect in background
        time.sleep(duration)
        
        window_end_time = time.time()
        window_end_dt = datetime.datetime.now()
        logger.info(f"Window {window_number} ended at {window_end_dt}")
        
        # Finalize perf collection (wait for process and parse output)
        self.finalize_perf_metrics(window_number, perf_info)
        
        # Collect metrics from CSV files falling within the window
        # We add a small buffer to end_dt to account for file writing latency
        metrics_list = self._collect_metrics_in_window(
            window_start_dt, 
            window_end_dt + datetime.timedelta(seconds=1)
        )
        
        # Fallback: if no CSV files found, parse the benchmark log file for interval stats
        if not metrics_list:
            logger.info("No CSV metrics files found, falling back to log file parsing")
            all_intervals = self._parse_log_for_intervals()
            
            if all_intervals and self.benchmark_start_time is not None:
                # Calculate elapsed-time range for this window relative to benchmark ROI start
                window_elapsed_start = window_start_time - self.benchmark_start_time
                window_elapsed_end = window_end_time - self.benchmark_start_time
                
                logger.info(f"Looking for intervals with elapsed_s in [{window_elapsed_start:.1f}, {window_elapsed_end:.1f}]")
                
                for interval in all_intervals:
                    elapsed = interval['elapsed_s']
                    if window_elapsed_start <= elapsed <= window_elapsed_end + 1.0:
                        metrics_list.append(interval)
                
                logger.info(f"Found {len(metrics_list)} intervals from log file for this window "
                           f"(out of {len(all_intervals)} total)")
            elif all_intervals:
                # No benchmark_start_time - just use the most recent intervals
                # matching the window duration
                expected_intervals = max(1, duration)
                metrics_list = all_intervals[-expected_intervals:]
                logger.warning(f"No benchmark_start_time, using last {len(metrics_list)} intervals from log")
        
        # Aggregate the metrics
        aggregated = self._aggregate_metrics(metrics_list)
        
        # Create BenchmarkMetrics object
        metrics = BenchmarkMetrics(
            throughput=aggregated.get("throughput", 0.0),
            goodput=aggregated.get("throughput", 0.0),  # Assume all requests succeed
            latency_avg=aggregated.get("sojourn_mean_ms", 0.0),
            latency_p95=aggregated.get("sojourn_p95_ms", 0.0),
            extra_metrics={
                "request_count": aggregated.get("count", 0.0),
                "intervals_aggregated": aggregated.get("intervals_aggregated", 0),
                "app_name": self.app_name,
                "latency_p50": aggregated.get("sojourn_p50_ms", 0.0),
                "latency_p99": aggregated.get("sojourn_p99_ms", 0.0),
                "latency_max": aggregated.get("sojourn_max_ms", 0.0),
            }
        )
        
        # Populate system metrics (perf, power, C-state, CPU)
        self._populate_system_metrics(metrics, window_number, window_start_time, window_end_time)
        
        logger.info(f"Final {self.app_name} metrics (with system metrics):\n{pprint.pformat(metrics.__dict__, indent=2)}")
        
        return metrics
    
    def parse_results(self, output_dir: str) -> BenchmarkMetrics:
        """Parse benchmark results.
        
        Not used for Tailbench continuous mode since we parse metrics in execute_window.
        This is here to satisfy the interface.
        """
        raise NotImplementedError("Tailbench continuous mode parses metrics in execute_window")
    
    def cleanup(self) -> None:
        """Stop the continuous benchmark and ensure all processes are killed."""
        logger.info("Stopping Tailbench benchmark...")
        
        # Track PIDs to kill
        pids_to_kill = []
        
        if self.benchmark_process is not None:
            main_pid = self.benchmark_process.pid
            pids_to_kill.append(main_pid)
            
            try:
                # First, try to get all child processes
                logger.info(f"Finding all child processes of PID {main_pid}")
                try:
                    import psutil
                    parent = psutil.Process(main_pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        pids_to_kill.append(child.pid)
                    logger.info(f"Found {len(children)} child processes")
                except ImportError:
                    logger.warning("psutil not available, falling back to process group kill")
                    # Fall back to killpg
                    pass
                except psutil.NoSuchProcess:
                    logger.info("Main process already terminated")
                except Exception as e:
                    logger.warning(f"Error finding children with psutil: {e}")
                
                # Send SIGTERM to process group first
                try:
                    logger.info(f"Sending SIGTERM to process group {main_pid}")
                    os.killpg(os.getpgid(main_pid), signal.SIGTERM)
                except ProcessLookupError:
                    logger.info("Process group already terminated")
                except Exception as e:
                    logger.warning(f"Error sending SIGTERM to process group: {e}")
                
                # Wait for graceful termination
                try:
                    self.benchmark_process.wait(timeout=10)
                    logger.info("Benchmark terminated gracefully")
                except subprocess.TimeoutExpired:
                    logger.warning("Benchmark did not terminate gracefully, using SIGKILL")
                    
                    # Send SIGKILL to process group
                    try:
                        os.killpg(os.getpgid(main_pid), signal.SIGKILL)
                    except Exception as e:
                        logger.warning(f"Error sending SIGKILL to process group: {e}")
                    
                    # Also kill individual PIDs we found
                    for pid in pids_to_kill:
                        try:
                            os.kill(pid, signal.SIGKILL)
                            logger.info(f"Sent SIGKILL to PID {pid}")
                        except ProcessLookupError:
                            pass  # Already dead
                        except Exception as e:
                            logger.warning(f"Error killing PID {pid}: {e}")
                    
                    # Final wait
                    try:
                        self.benchmark_process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        logger.error("Process still alive after SIGKILL")
                
            except Exception as e:
                logger.error(f"Error stopping benchmark: {e}")
            
            finally:
                self.benchmark_process = None
        
        # Additional cleanup: Search for any leftover tailbench *binary* processes and kill them.
        # Use binary name (e.g. xapian_integrated) not app name (xapian) so we never match
        # the Python process (e.g. .../xapian_hi_p99/... in argv) and kill ourselves.
        try:
            logger.info("Searching for any leftover tailbench processes...")
            import subprocess as sp
            binary_name = self.app_config.binary_name
            try:
                result = sp.run(
                    ["pgrep", "-f", binary_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    my_pid = os.getpid()
                    leftover_pids = [
                        int(pid.strip()) for pid in result.stdout.strip().split('\n')
                        if pid.strip() and int(pid.strip()) != my_pid
                    ]
                    if leftover_pids:
                        logger.warning(f"Found {len(leftover_pids)} leftover {binary_name} processes: {leftover_pids}")
                        for pid in leftover_pids:
                            try:
                                os.kill(pid, signal.SIGKILL)
                                logger.info(f"Killed leftover process {pid}")
                            except ProcessLookupError:
                                pass
                            except Exception as e:
                                logger.warning(f"Error killing leftover PID {pid}: {e}")
                else:
                    logger.info("No leftover processes found")
            except FileNotFoundError:
                logger.warning("pgrep not available, skipping leftover process check")
            except Exception as e:
                logger.warning(f"Error checking for leftover processes: {e}")
        except Exception as e:
            logger.error(f"Error in leftover process cleanup: {e}")
        
        # Close log file if open
        if hasattr(self, 'log_file') and self.log_file is not None:
            try:
                self.log_file.close()
                logger.info("Closed benchmark output log file")
            except Exception as e:
                logger.warning(f"Error closing log file: {e}")
            finally:
                self.log_file = None
        
        logger.info("Tailbench benchmark cleanup complete")
