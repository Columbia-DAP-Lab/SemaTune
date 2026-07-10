#!/usr/bin/env python3
"""
Simplified configuration system for the OS parameter tuner.

This module provides a clean, dataclass-based configuration system that's
easier to understand and modify than the complex config manager.
"""

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Tuple, Optional, Any, Union, List

from .model_versions import (
    DEFAULT_LLM_TEMPERATURE,
    PRIMARY_ACTOR_MODEL,
    PRIMARY_SPECULATOR_MODEL,
)

logger = logging.getLogger(__name__)

# TEMPORARY: Override window_duration for all experiments (set to None to use config value)
WINDOW_DURATION_OVERRIDE = None

@dataclass
class SimpleConfig:
    """Simplified configuration for OS parameter tuning."""
    
    # Benchmark selection
    benchmark: str = "sysbench_oltp"  # See benchmark_registry.py for all available benchmarks
    
    # CPU pinning (taskset)
    pin_to_cores: Optional[str] = None  # e.g., "0,1,2,3" or "0-7" or None for all cores
    
    # Benchmark-specific settings
    # Sysbench settings
    sysbench_script: str = "/usr/share/sysbench/oltp_read_write.lua"
    sysbench_host: str = "127.0.0.1"
    sysbench_port: int = 5432
    sysbench_user: str = "admin"
    sysbench_password: str = ""
    sysbench_db: str = "benchdb"
    sysbench_tables: int = 4
    sysbench_table_size: int = 100000
    sysbench_threads: int = 16
    sysbench_rate: Optional[int] = None  # Optional rate limiting
    sysbench_cpu_max_prime: Optional[int] = None  # Maximum prime number for CPU benchmark
    
    # BenchBase/TPCC settings
    benchbase_jar_path: str = "deps/benchbase/target/benchbase-postgres/benchbase.jar"
    benchbase_config_file: str = "config/benchbase/tpcc_config_template.xml"
    # BenchBase execution guardrails
    benchbase_timeout_buffer_seconds: int = 40  # Per-window timeout = window_duration + buffer
    benchbase_timeout_retries: int = 1  # Retry count when a window times out
    
    # Mutilate settings
    mutilate_client_host: Optional[str] = None  # IP address of remote client (for server to connect to)
    mutilate_target: str = "127.0.0.1:11211"  # Memcached target (server:port)
    mutilate_threads: int = 8  # Mutilate worker threads
    mutilate_clients: int = 8  # Mutilate client connections
    mutilate_qps: int = 500000  # Target QPS
    mutilate_iadist: str = "fixed:0"  # Request inter-arrival distribution
    mutilate_depth: int = 1  # Pipeline depth
    mutilate_bin_path: str = "~/mutilate/mutilate"  # Path to mutilate binary on client
    mutilate_memcached_bin: str = "memcached"  # Path to memcached binary on server
    
    # Tailbench settings
    tailbench_app: str = "masstree"  # Application: img-dnn, masstree, moses, shore, silo, specjbb, sphinx, xapian
    tailbench_root: str = "deps/Tailbench/tailbench"  # Path to Tailbench root directory
    tailbench_data_root: str = "deps/Tailbench/tailbench.inputs"  # Path to Tailbench input datasets
    tailbench_qps: Optional[int] = None  # Target QPS (uses app default if None)
    tailbench_threads: Optional[int] = None  # Number of threads (uses app default if None)
    tailbench_warmupreqs: Optional[int] = None  # Number of warmup requests (uses app default if None)
    tailbench_maxreqs: int = 1000000000  # Maximum requests (set very high for continuous run)
    tailbench_minsleepns: int = 10000  # Minimum sleep time in nanoseconds
    tailbench_metrics_interval_sec: int = 1  # Interval for metrics collection in seconds
    tailbench_output_dir: Optional[str] = None  # Directory to save output files (lats.bin, lats_interval_*.csv). If None, uses workdir
    tailbench_jdk_path: str = "/usr/lib/jvm/java-8-openjdk-amd64"  # JDK path (for specjbb)
    
    # Tailbench app-specific settings
    tailbench_xapian_db_path: Optional[str] = None  # Xapian database path (default: ${DATA_ROOT}/xapian/wiki)
    tailbench_silo_scale_factor: Optional[int] = None  # Silo scale factor / number of warehouses (default: 4)
    tailbench_sphinx_audio_samples: Optional[str] = None  # Sphinx audio samples file name (default: audio_samples)
    
    # DCPerf settings
    dcperf_path: Optional[str] = None  # Path to DCPerf directory (default: deps/DCPerf)
    dcperf_benchmark_name: Optional[str] = None  # Which benchpress benchmark to run (e.g., "spark_standalone_local")
    dcperf_worker_cores: Optional[int] = None  # Number of worker cores (None = auto-detect)
    dcperf_max_runtime: int = 3600  # Maximum benchmark runtime in seconds (safety kill)
    dcperf_online_buffer_seconds: int = 120  # Extra runtime budget beyond all planned windows
    dcperf_online_target_runtime_seconds: Optional[float] = None  # Explicit online DCPerf main-phase runtime target; overrides window+buffer-derived budget
    dcperf_online_num_iters: Optional[int] = None  # Explicit Spark --num-iters override for online DCPerf mode (highest priority)
    dcperf_online_query_runtime_estimate_seconds: Optional[float] = None  # Override Spark query runtime estimate
    dcperf_online_interval_seconds: int = 0  # Sleep between Spark iterations when using online DCPerf mode
    dcperf_online_start_timeout_seconds: int = 420  # How long to wait for the online benchmark's main phase after launch
    dcperf_online_log_poll_seconds: float = 0.5  # Polling interval when waiting for online benchmark activity
    django_backend_cores: Optional[str] = None  # Cores for Cassandra+Django (e.g., "0-9")
    django_client_cores: Optional[str] = None  # Cores for Siege load gen (e.g., "10-19")
    
    # Tuner settings
    tuner_type: str = "llm"  # "fixed" or "llm"
    
    # Parameter ranges for tunable parameters
    # Can be:
    # - Tuple[int, int]: Continuous/discrete range [min, max]
    # - List[str] or List[int]: Categorical - list of valid values
    # Examples:
    #   "min_granularity_ns": [100000, 50000000]  # Continuous range
    #   "epp": ["performance", "balance_performance", "balance_power", "power"]  # Categorical
    #   "turbo": [True, False]  # Boolean/categorical
    parameter_ranges: Dict[str, Union[Tuple[int, int], List[Union[int, str, bool]]]] = field(default_factory=lambda: {
        "min_granularity_ns": (100000, 50000000)
    })
    
    # Optional: Explicit parameter types for tuners (like MLOS) that support it
    # Valid types: "integer", "float", "categorical", "ordinal"
    # If not specified, types are inferred from parameter_ranges
    parameter_types: Optional[Dict[str, str]] = None
    
    # Parameters to actually tune (subset of parameter_ranges)
    # If None or empty, all parameters in parameter_ranges will be tuned
    # If specified, only these parameters will be exposed to tuners
    parameters_to_tune: Optional[List[str]] = None
    
    # Fixed parameters (won't be tuned)
    fixed_parameters: Dict[str, int] = field(default_factory=lambda: {
        "latency_ns": 24000000,
        "wakeup_granularity_ns": 4000000
    })
    
    # Optimization settings
    optimization_metric: str = "goodput"  # Metric to optimize (goodput, throughput, latency_avg, etc.)
    optimization_goal: str = "maximize"  # "maximize" or "minimize"
    max_iterations: int = 10
    # Extra measurement-only windows after tuning finishes.
    # Tuner requests stop after max_iterations; these windows keep the last applied params fixed.
    post_tuning_windows: int = 20
    window_duration: int = 60  # Window duration in seconds
    # If True, keep window_duration from config and skip global WINDOW_DURATION_OVERRIDE.
    # Default False preserves historical behavior for existing runs.
    respect_config_window_duration: bool = False
    continuous_apply: bool = False  # If True, continuously resubmit tuner requests and apply immediately. If False, send at most one request per window.
    tuning_mode: str = "outside-of-window"  # "outside-of-window" or "in-window" - when to call the tuner
    experiment_profile: Optional[str] = None  # Optional hardcoded experiment profile applied on config load
    
    # Constraint settings
    constraint_metric: Optional[str] = None  # Metric to constrain (e.g., "latency_p95")
    constraint_threshold: Optional[float] = None  # Threshold value
    constraint_direction: str = "less_than"  # "less_than" (metric < threshold) or "greater_than" (metric > threshold)
    constraint_penalty: float = 10.0  # DEPRECATED — kept for backward compat but ignored.
                                      # A fixed 10x multiplicative penalty is always used instead.
    
    # Results directory
    results_dir: str = "results"
    
    # LLM tuner settings (if tuner_type == "llm")
    # Retained only so historical configs deserialize; runtime credentials come
    # exclusively from GEMINI_API_KEY and OPENROUTER_API_KEY.
    llm_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    llm_model_name: str = PRIMARY_ACTOR_MODEL  # Paper's Actor/Reasoning model.
                                             # Gemini: "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
                                             #         "gemini-3-pro-preview", "gemini-3-flash-preview",
                                             #         "gemini-3.1-pro-preview", "gemini-3.1-flash-lite-preview"
                                             # OpenRouter: any valid model ID (e.g. "openai/gpt-4.1", "anthropic/claude-sonnet-4")
    llm_secondary_model: str = PRIMARY_SPECULATOR_MODEL  # Paper's Speculator/Instant model.
    llm_replay_file: Optional[str] = None  # Path to replay history JSON
    
    # Dual-loop explicit model names (preferred over llm_model_name / llm_secondary_model for clarity)
    llm_actor_model: Optional[str] = None      # Model for Actor (Reasoning) agent.  Falls back to llm_model_name.
    llm_speculator_model: Optional[str] = None  # Model for Speculator (Quick) agent.  Falls back to llm_secondary_model.
    
    # LLM thinking configuration
    # Gemini 3: uses thinking_level ("low", "medium", "high").  Default: "high".
    # Gemini 2.5: uses thinking_budget (int token count).  Default: 5000.
    # Set one or leave both None to use model-family defaults.
    llm_thinking_level: Optional[str] = None
    llm_thinking_budget: Optional[int] = None
    
    # Additional LLM settings
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE
    llm_request_max_retries: int = 10
    llm_request_retry_backoff_sec: float = 1.0
    llm_request_timeout_seconds: Optional[float] = None  # Per-attempt timeout for ordinary LLM requests; None uses SDK defaults.
    llm_final_freeze_max_retries: int = 3  # Extra redundancy for the final convergence/freeze prompt.
    llm_final_freeze_timeout_seconds: Optional[float] = 240.0  # Per-attempt timeout for the final convergence/freeze prompt.
    llm_gist_max_retries: int = 3  # Extra redundancy when generating the end-of-run gist.
    llm_gist_timeout_seconds: Optional[float] = 240.0  # Per-attempt timeout for gist generation.
    llm_openrouter_max_tokens: int = 2048
    llm_api_log_enabled: bool = True
    llm_api_log_dir: Optional[str] = None
    workload_description: Optional[str] = None
    llm_additional_metrics: Optional[List[str]] = None  # Extra metrics to show LLM (e.g. ["throughput", "latency_p99", "power_socket0_watts"])
    # Optional per-agent metric views for dual-loop runs. If unset, llm_additional_metrics is used.
    llm_actor_additional_metrics: Optional[List[str]] = None
    llm_speculator_additional_metrics: Optional[List[str]] = None
    # If True, hide the primary optimization metric value but keep the metric name visible.
    llm_hide_primary_metric_value: bool = False
    # If True, hide the primary optimization metric name/value from all LLM agents.
    llm_hide_primary_metric: bool = False
    # If True, the speculator prompt hides the primary optimization metric.
    llm_speculator_hide_primary_metric: bool = False
    # Optional aggregation/sampling interval hint (seconds) for speculator prompt context.
    llm_speculator_aggregation_interval_s: Optional[float] = None
    # If True (dual-loop only), skip Actor requests during windows and make one final Actor call at run end.
    dual_loop_actor_only_final: bool = False
    # If True (dual-loop only), the final tuning-stage Actor request is required before stable windows begin.
    dual_loop_force_final_actor_before_stable: bool = False
    # If True, keep exploring until the final tuning call and only converge on that final call.
    llm_explore_until_last_iteration: bool = False
    # If True (single-loop LLM), after the completed last tuning window issue one
    # refreshed freeze request and wait for it before stable windows begin.
    llm_force_final_freeze_before_stable: bool = False
    # If True, measure one pre-tuning window at OS defaults before iteration 1 and
    # expose it to LLM tuners as "Initial/OS Default config".
    llm_measure_default_before_tuning: bool = True
    
    # Trimming stage settings (optional LLM-based search space narrowing before main tuner)
    trimming_enabled: bool = False  # Enable/disable trimming phase
    trimming_cycles: int = 5       # Number of initial cycles for trimming
    trimming_model_name: Optional[str] = None  # Model to use for trimming (defaults to llm_model_name)
    trimming_strategy: str = "single_loop"  # "single_loop" (Actor trims) or "dual_loop" (Speculator explores, Actor trims)
    trimming_suggest_params: bool = True  # Whether trimming LLM also suggests param values
    trimming_aggressiveness: str = "normal"  # "normal" or "aggressive"
    
    # Perf stat settings
    use_perf_stat: bool = True  # Always collect perf stats and system metrics
    
    # Indirect optimization settings
    use_indirect_optimization: bool = False  # Enable indirect optimization using metric signatures
    llm_indirect_history_show_all_metrics: bool = True  # If True, show additional metrics for all history entries; if False, only for the last one
    # Indirect prompt behavior:
    # - "signature_compare": explicit full-signature comparison across iterations (mode 3)
    # - "all_metrics_plain": all metrics as indirect evidence without explicit signature wording (mode 2)
    llm_indirect_prompt_style: str = "signature_compare"
    # Keeps indirect-all behavior unchanged by default. Set to 2 to omit the explicit
    # pairwise/full-signature comparison sentence (mode 2 behavior).
    omit_explicit_pairwise_comparison_instruction: Union[bool, int] = False
    llm_full_metrics_prompt_mode: bool = False  # If True, prompt explicitly treats additional metrics as first-class diagnostics while keeping the primary objective unchanged
    # Optional full-metrics variant that keeps primary metric visible but adds
    # explicit full-signature comparison guidance (mode-3-like wording).
    llm_full_metrics_explicit_signature_compare: bool = False
    # If True, list additional observability metrics in the prompt but do not
    # add any extra guidance about how to use them.
    llm_additional_metrics_dump_only: bool = False
    llm_minimal_prompt_mode: bool = False  # If True, use a stripped prompt with only goal, ranges, round progress, and JSON output instructions.
    
    # Run context
    previous_run_gist: Optional[str] = None  # Summary/gist from a previous run to inform the current one
    
    # Dynamic workload changes
    workload_change_type: Optional[str] = None  # Type of workload change (e.g., "cyclic_halving")
    workload_change_interval: Optional[int] = None  # Number of iterations between workload changes
    workload_change_param: Optional[str] = None  # Parameter to change (benchmark specific, e.g., "rate")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        result = asdict(self)
        # Convert tuples to lists for JSON serialization (keep lists as lists for categorical)
        if 'parameter_ranges' in result:
            converted = {}
            for k, v in result['parameter_ranges'].items():
                if isinstance(v, tuple):
                    converted[k] = list(v)
                else:
                    converted[k] = v  # Keep lists as lists
            result['parameter_ranges'] = converted
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimpleConfig':
        """Create config from dictionary."""
        # Filter out tuner-specific parameters and other non-dataclass fields
        # These will be stored as attributes on the config object
        tuner_specific_params = [
            'bayesian_n_trials', 'bayesian_seed',
            'dqn_grid_points', 'dqn_learning_rate', 'dqn_epsilon_start', 'dqn_epsilon_end',
            'dqn_epsilon_decay', 'dqn_batch_size', 'dqn_memory_size', 'dqn_target_update_freq',
            'dqn_hidden_size', 'dqn_gamma',
            'qlearning_grid_points', 'qlearning_max_actions', 'qlearning_learning_rate',
            'qlearning_epsilon_start', 'qlearning_epsilon_end', 'qlearning_epsilon_decay',
            'qlearning_gamma',
            'mlos_max_trials', 'mlos_n_random_init', 'mlos_max_ratio', 'mlos_use_default_config',
            'mlos_n_random_probability', 'mlos_seed', 'mlos_run_name', 'mlos_output_directory',
            'mlos_objective_weights',
            'mlos_buffer_time',
            'constraint_metric', 'constraint_threshold', 'constraint_direction', 'constraint_penalty',
            'changes'  # Workload changes field
        ]
        
        # Extract tuner-specific parameters
        tuner_params = {}
        for param in tuner_specific_params:
            if param in data:
                tuner_params[param] = data.pop(param)
        
        # Detect if dual-loop fields were explicitly provided in the JSON
        # (before they get consumed by cls(**data) or popped)
        # Require BOTH fields: only llm_model_name → single loop
        _has_explicit_dual_loop = ('llm_actor_model' in data and 'llm_speculator_model' in data)
        
        # Convert lists to tuples for continuous ranges, keep lists for categorical
        if 'parameter_ranges' in data:
            converted = {}
            for k, v in data['parameter_ranges'].items():
                if isinstance(v, list):
                    # Check if it's a continuous range [min, max] or categorical list
                    # Continuous ranges must have exactly 2 numeric values (not booleans or strings)
                    if len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                        # Continuous/discrete range
                        converted[k] = tuple(v)
                    else:
                        # Categorical - keep as list (can contain strings, booleans, etc.)
                        converted[k] = v
                else:
                    converted[k] = v
            data['parameter_ranges'] = converted
        
        # Create config instance
        config = cls(**data)
        
        # Store tuner-specific parameters as attributes
        for param, value in tuner_params.items():
            setattr(config, param, value)
        
        # Mark whether dual-loop was explicitly requested
        config._explicit_dual_loop = _has_explicit_dual_loop
        
        return config
    
    def save(self, file_path: str) -> None:
        """Save configuration to JSON file."""
        os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else '.', exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Configuration saved to {file_path}")
    
    @classmethod
    def load(cls, file_path: str) -> 'SimpleConfig':
        """Load configuration from JSON file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Configuration file not found: {file_path}")
        
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        config = cls.from_dict(data)
        config._resolve_llm_model_aliases()
        config._apply_experiment_profile()
        if WINDOW_DURATION_OVERRIDE is not None:
            # Skip override for DCPerf benchmarks (they run to completion)
            if (
                not (config.benchmark and config.benchmark.startswith("dcperf"))
                and not getattr(config, 'respect_config_window_duration', False)
            ):
                config.window_duration = WINDOW_DURATION_OVERRIDE
                logger.info(f"Temporary override: window_duration set to {WINDOW_DURATION_OVERRIDE}s")
        return config

    def _apply_experiment_profile(self) -> None:
        """Apply hardcoded experiment profiles for paper runs."""
        profile = getattr(self, "experiment_profile", None)
        if not profile:
            return

        if profile == "tuxbot_app_dual_30_20_final_actor":
            logger.info("Applying experiment profile: %s", profile)
            self.tuning_mode = "in-window"
            self.max_iterations = 30
            self.post_tuning_windows = 20
            self.continuous_apply = False
            self.use_indirect_optimization = False
            self.llm_full_metrics_prompt_mode = True
            self.llm_full_metrics_explicit_signature_compare = False
            self.dual_loop_actor_only_final = False
            self.dual_loop_force_final_actor_before_stable = True
            self.llm_explore_until_last_iteration = True
            self.llm_measure_default_before_tuning = True
        elif profile == "tuxbot_full_mode3_dual_30_20_final_actor":
            logger.info("Applying experiment profile: %s", profile)
            self.tuning_mode = "in-window"
            self.max_iterations = 30
            self.post_tuning_windows = 20
            self.continuous_apply = False
            self.use_indirect_optimization = False
            self.llm_full_metrics_prompt_mode = True
            self.llm_full_metrics_explicit_signature_compare = True
            self.dual_loop_actor_only_final = False
            self.dual_loop_force_final_actor_before_stable = True
            self.llm_explore_until_last_iteration = True
            self.llm_measure_default_before_tuning = True
        elif profile == "tuxbot_indirect_mode3_dual_30_20_final_actor":
            logger.info("Applying experiment profile: %s", profile)
            self.tuning_mode = "in-window"
            self.max_iterations = 30
            self.post_tuning_windows = 20
            self.continuous_apply = False
            self.use_indirect_optimization = True
            self.llm_indirect_prompt_style = "signature_compare"
            self.omit_explicit_pairwise_comparison_instruction = False
            self.dual_loop_actor_only_final = False
            self.dual_loop_force_final_actor_before_stable = True
            self.llm_explore_until_last_iteration = True
            self.llm_measure_default_before_tuning = True
        elif profile == "tuxbot_indirect_mode3_single_30_20_final_actor":
            logger.info("Applying experiment profile: %s", profile)
            self.tuning_mode = "in-window"
            self.max_iterations = 30
            self.post_tuning_windows = 20
            self.continuous_apply = False
            self.use_indirect_optimization = True
            self.llm_indirect_prompt_style = "signature_compare"
            self.omit_explicit_pairwise_comparison_instruction = False
            self.dual_loop_actor_only_final = False
            self.dual_loop_force_final_actor_before_stable = False
            self.llm_explore_until_last_iteration = True
            self.llm_force_final_freeze_before_stable = True
            self.llm_measure_default_before_tuning = True
        elif profile == "tuxbot_app_single_30_20_final_actor":
            logger.info("Applying experiment profile: %s", profile)
            self.tuning_mode = "in-window"
            self.max_iterations = 30
            self.post_tuning_windows = 20
            self.continuous_apply = False
            self.use_indirect_optimization = False
            self.llm_full_metrics_prompt_mode = True
            self.llm_full_metrics_explicit_signature_compare = False
            self.dual_loop_actor_only_final = False
            self.dual_loop_force_final_actor_before_stable = False
            self.llm_explore_until_last_iteration = True
            self.llm_force_final_freeze_before_stable = True
            self.llm_measure_default_before_tuning = True
        elif profile == "mlos_50_tune_only":
            logger.info("Applying experiment profile: %s", profile)
            self.max_iterations = 50
            self.post_tuning_windows = 0
        elif profile == "mlos_ipc_50_tune_only":
            logger.info("Applying experiment profile: %s", profile)
            self.max_iterations = 50
            self.post_tuning_windows = 0
            self.optimization_metric = "instructions_per_cycle"
            self.optimization_goal = "maximize"
        elif profile == "mlos_cache_misses_50_tune_only":
            logger.info("Applying experiment profile: %s", profile)
            self.max_iterations = 50
            self.post_tuning_windows = 0
            self.optimization_metric = "cache_misses"
            self.optimization_goal = "minimize"
        elif profile in {"mlos_100_tune_only", "mlos_120_tune_only"}:
            logger.info("Applying experiment profile: %s", profile)
            self.max_iterations = 100
            self.post_tuning_windows = 20
        elif profile == "mlos_trimming_aggressive_10_40":
            logger.info("Applying experiment profile: %s", profile)
            self.max_iterations = 50
            self.post_tuning_windows = 0
            self.tuning_mode = "in-window"
            self.continuous_apply = False
            self.trimming_enabled = True
            self.trimming_cycles = 10
            self.trimming_strategy = "single_loop"
            self.trimming_model_name = PRIMARY_ACTOR_MODEL
            self.llm_model_name = PRIMARY_ACTOR_MODEL
            self.trimming_aggressiveness = "aggressive"
        else:
            logger.warning("Unknown experiment_profile=%s; leaving config unchanged", profile)
    
    def _resolve_llm_model_aliases(self) -> None:
        """Synchronize legacy and new dual-loop model name fields.

        Priority:
          - ``llm_actor_model`` / ``llm_speculator_model`` (explicit, preferred)
          - ``llm_model_name`` / ``llm_secondary_model`` (legacy, fallback)

        After this call both old and new fields are consistent so that any code
        path can use either name.

        Only backfills actor/speculator fields when dual-loop was explicitly
        requested (both ``llm_actor_model`` and ``llm_speculator_model`` present
        in config JSON).  For single-loop configs the fields stay ``None``.
        """
        is_dual = getattr(self, '_explicit_dual_loop', False)

        # Actor: new name takes priority; if not set, inherit from legacy field
        if self.llm_actor_model:
            self.llm_model_name = self.llm_actor_model
        elif is_dual:
            # Only backfill for dual-loop configs
            self.llm_actor_model = self.llm_model_name

        # Speculator: new name takes priority; if not set, inherit from legacy field
        if self.llm_speculator_model:
            self.llm_secondary_model = self.llm_speculator_model
        elif is_dual:
            # Only backfill for dual-loop configs
            self.llm_speculator_model = self.llm_secondary_model

    def validate(self) -> bool:
        """Validate configuration settings.
        
        Returns:
            True if valid, raises ValueError if invalid
        """
        # Validate benchmark using registry
        try:
            from .benchmarks.benchmark_registry import BenchmarkType
            BenchmarkType.from_string(self.benchmark)
        except (ImportError, ValueError) as e:
            available = BenchmarkType.list_all() if 'BenchmarkType' in locals() else ["sysbench_oltp", "tpcc"]
            raise ValueError(
                f"Invalid benchmark: {self.benchmark}. "
                f"Available benchmarks: {available}"
            ) from e
        
        if self.tuner_type not in ["fixed", "llm", "human", "qlearning", "bayesian", "dqn", "mlos", "simple_smac", "llm_command"]:
            raise ValueError(f"Invalid tuner_type: {self.tuner_type}. Must be one of: 'fixed', 'llm', 'human', 'qlearning', 'bayesian', 'dqn', 'mlos', 'simple_smac', 'llm_command'")
        
        if self.optimization_goal not in ["maximize", "minimize"]:
            raise ValueError(f"Invalid optimization_goal: {self.optimization_goal}. Must be 'maximize' or 'minimize'")
        
        if self.tuning_mode not in ["outside-of-window", "in-window"]:
            raise ValueError(f"Invalid tuning_mode: {self.tuning_mode}. Must be 'outside-of-window' or 'in-window'")

        if self.benchbase_timeout_buffer_seconds < 0:
            raise ValueError("benchbase_timeout_buffer_seconds must be >= 0")
        if self.benchbase_timeout_retries < 0:
            raise ValueError("benchbase_timeout_retries must be >= 0")
        if self.dcperf_online_buffer_seconds < 0:
            raise ValueError("dcperf_online_buffer_seconds must be >= 0")
        if (
            self.dcperf_online_target_runtime_seconds is not None
            and self.dcperf_online_target_runtime_seconds <= 0
        ):
            raise ValueError("dcperf_online_target_runtime_seconds must be > 0 when set")
        if self.dcperf_online_num_iters is not None and self.dcperf_online_num_iters <= 0:
            raise ValueError("dcperf_online_num_iters must be > 0 when set")
        if self.dcperf_online_interval_seconds < 0:
            raise ValueError("dcperf_online_interval_seconds must be >= 0")
        if self.dcperf_online_start_timeout_seconds <= 0:
            raise ValueError("dcperf_online_start_timeout_seconds must be > 0")
        if self.dcperf_online_log_poll_seconds <= 0:
            raise ValueError("dcperf_online_log_poll_seconds must be > 0")
        
        if self.tuner_type in ["llm", "llm_command"]:
            # Validate Gemini model names (non-Gemini models are OpenRouter and skip validation)
            valid_gemini_models = [
                "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
                "gemini-3-pro-preview", "gemini-3-flash-preview",
                "gemini-3.1-pro-preview", "gemini-3.1-flash-lite-preview",
            ]
            is_gemini_model = self.llm_model_name.startswith("gemini-")
            
            if is_gemini_model:
                if self.llm_model_name not in valid_gemini_models:
                    raise ValueError(
                        f"Invalid Gemini llm_model_name: {self.llm_model_name}. "
                        f"Must be one of: {valid_gemini_models}"
                    )
                if self.llm_secondary_model and self.llm_secondary_model.startswith("gemini-") and self.llm_secondary_model not in valid_gemini_models:
                    raise ValueError(
                        f"Invalid Gemini llm_secondary_model: {self.llm_secondary_model}. "
                        f"Must be one of: {valid_gemini_models}"
                    )
                if not self.llm_replay_file and not os.getenv("GEMINI_API_KEY"):
                    raise ValueError("GEMINI_API_KEY must be set for Gemini models (or use replay_file)")
            else:
                # OpenRouter model — require openrouter_api_key
                if not self.llm_replay_file and not os.getenv("OPENROUTER_API_KEY"):
                    raise ValueError(
                        f"OPENROUTER_API_KEY must be set for non-Gemini model '{self.llm_model_name}' "
                        f"(or use replay_file)."
                    )
            
            # Validate thinking_level if provided
            if self.llm_thinking_level is not None:
                gemini3_flash_levels = ["minimal", "low", "medium", "high"]
                gemini3_pro_levels = ["low", "high"]
                all_valid_levels = gemini3_flash_levels  # superset
                if self.llm_thinking_level not in all_valid_levels:
                    raise ValueError(
                        f"Invalid llm_thinking_level: {self.llm_thinking_level}. "
                        f"Must be one of: {all_valid_levels}"
                    )

            valid_indirect_styles = ["signature_compare", "all_metrics_plain"]
            if self.llm_indirect_prompt_style not in valid_indirect_styles:
                raise ValueError(
                    f"Invalid llm_indirect_prompt_style: {self.llm_indirect_prompt_style}. "
                    f"Must be one of: {valid_indirect_styles}"
                )
            omit_pairwise_mode = getattr(self, 'omit_explicit_pairwise_comparison_instruction', False)
            try:
                omit_pairwise_mode_int = int(omit_pairwise_mode)
            except (TypeError, ValueError):
                raise ValueError(
                    "Invalid omit_explicit_pairwise_comparison_instruction: "
                    f"{omit_pairwise_mode}. Must be false/0 or 2."
                )
            if omit_pairwise_mode_int not in (0, 2):
                raise ValueError(
                    "Invalid omit_explicit_pairwise_comparison_instruction: "
                    f"{omit_pairwise_mode}. Must be false/0 or 2."
                )
        
        # Validate trimming settings
        if self.trimming_enabled:
            if self.trimming_cycles >= self.max_iterations:
                raise ValueError(
                    f"trimming_cycles ({self.trimming_cycles}) must be less than "
                    f"max_iterations ({self.max_iterations})"
                )
            if self.trimming_cycles < 1:
                raise ValueError("trimming_cycles must be at least 1")
        
        # Validate tuner-specific parameters if present
        if self.tuner_type == "bayesian":
            n_trials = getattr(self, 'bayesian_n_trials', None)
            if n_trials is not None and n_trials <= 0:
                raise ValueError("bayesian_n_trials must be positive if provided")
        
        if self.tuner_type == "mlos":
            max_trials = getattr(self, 'mlos_max_trials', None)
            if max_trials is not None and max_trials <= 0:
                raise ValueError("mlos_max_trials must be positive if provided")
            n_random_init = getattr(self, 'mlos_n_random_init', None)
            if n_random_init is not None and n_random_init < 0:
                raise ValueError("mlos_n_random_init must be non-negative if provided")
            max_ratio = getattr(self, 'mlos_max_ratio', None)
            if max_ratio is not None and (max_ratio < 0.0 or max_ratio > 1.0):
                raise ValueError("mlos_max_ratio must be between 0.0 and 1.0 if provided")
            n_random_probability = getattr(self, 'mlos_n_random_probability', None)
            if n_random_probability is not None and (n_random_probability < 0.0 or n_random_probability > 1.0):
                raise ValueError("mlos_n_random_probability must be between 0.0 and 1.0 if provided")
        
        if self.tuner_type in ["qlearning", "dqn"]:
            grid_points = getattr(self, f'{self.tuner_type}_grid_points', None)
            if grid_points is not None and grid_points <= 0:
                raise ValueError(f"{self.tuner_type}_grid_points must be positive if provided")
        
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")

        if self.post_tuning_windows < 0:
            raise ValueError("post_tuning_windows must be non-negative")

        if self.llm_request_max_retries <= 0:
            raise ValueError("llm_request_max_retries must be positive")
        if self.llm_final_freeze_max_retries <= 0:
            raise ValueError("llm_final_freeze_max_retries must be positive")
        if self.llm_gist_max_retries <= 0:
            raise ValueError("llm_gist_max_retries must be positive")
        if self.llm_request_retry_backoff_sec < 0:
            raise ValueError("llm_request_retry_backoff_sec must be non-negative")
        for field_name in (
            "llm_request_timeout_seconds",
            "llm_final_freeze_timeout_seconds",
            "llm_gist_timeout_seconds",
        ):
            timeout_value = getattr(self, field_name)
            if timeout_value is not None and timeout_value <= 0:
                raise ValueError(f"{field_name} must be positive when provided")
        if self.llm_openrouter_max_tokens <= 0:
            raise ValueError("llm_openrouter_max_tokens must be positive")

        if self.window_duration <= 0:
            raise ValueError("window_duration must be positive")

        if self.llm_speculator_aggregation_interval_s is not None:
            if self.llm_speculator_aggregation_interval_s <= 0:
                raise ValueError("llm_speculator_aggregation_interval_s must be positive if provided")
        
        # Validate constraint settings if provided
        if self.constraint_metric is not None:
            if self.constraint_threshold is None:
                raise ValueError("constraint_threshold must be provided when constraint_metric is set")
            if self.constraint_direction not in ["less_than", "greater_than"]:
                raise ValueError("constraint_direction must be 'less_than' or 'greater_than'")
            if self.constraint_penalty <= 0:
                raise ValueError("constraint_penalty must be positive")
        
        # Validate parameter ranges
        for param_name, param_range in self.parameter_ranges.items():
            if isinstance(param_range, tuple):
                # Continuous/discrete range [min, max]
                if len(param_range) != 2:
                    raise ValueError(f"Invalid parameter range for {param_name}: must be [min, max] tuple")
                min_val, max_val = param_range
                if min_val >= max_val:
                    raise ValueError(
                        f"Invalid parameter range for {param_name}: "
                        f"min ({min_val}) must be less than max ({max_val})"
                    )
            elif isinstance(param_range, list):
                # Categorical - list of valid values
                if len(param_range) == 0:
                    raise ValueError(f"Invalid categorical parameter range for {param_name}: must have at least one value")
            else:
                raise ValueError(
                    f"Invalid parameter range type for {param_name}: "
                    f"must be [min, max] tuple or list of valid values"
                )
        
        # Validate parameters_to_tune
        if self.parameters_to_tune is not None:
            if not isinstance(self.parameters_to_tune, list):
                raise ValueError("parameters_to_tune must be a list of parameter names")
            if len(self.parameters_to_tune) == 0:
                raise ValueError("parameters_to_tune cannot be an empty list (use None to tune all parameters)")
            # Check that all parameters in parameters_to_tune exist in parameter_ranges
            invalid_params = [p for p in self.parameters_to_tune if p not in self.parameter_ranges]
            if invalid_params:
                raise ValueError(
                    f"Invalid parameters in parameters_to_tune: {invalid_params}. "
                    f"All parameters must be in parameter_ranges: {list(self.parameter_ranges.keys())}"
                )
        
        return True
