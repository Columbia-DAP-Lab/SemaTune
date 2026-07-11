#!/usr/bin/env python3
"""
Dual-loop optimizer for OS parameter tuning.

This module implements a dual-loop optimization process with:
1. An Actor (Reasoning Agent) that provides strategic parameter changes.
2. A Speculator (Quick Agent) that runs every window, providing immediate reactions.

Architecture:
- At each window start, BOTH Actor and Speculator requests are dispatched.
- Actor request is only sent if the previous Actor request has completed.
- Speculator request is always sent at window start.
- In "in-window" mode: parameters are applied as responses arrive during window.
- In "outside-of-window" mode: wait for BOTH responses before next window.
- Actor sets a "baseline" context when it responds.
- Speculator operates on "recent" history since the last baseline update.
"""

import os
import json
import time
import logging
import threading
import concurrent.futures
from typing import Dict, Optional, Any, List
from datetime import datetime

from .config import SimpleConfig
from .benchmark import BenchmarkInterface, BenchmarkMetrics
from .tuners.base import TunerResponse
from .tuners.llm import LLMTuner
from .parameter_manager import (
    ParameterManager,
    get_selected_default_parameters,
    get_new_parameter_names,
    reset_selected_parameters_to_defaults,
    reset_new_parameters_to_system_defaults,
)
from .token_bookkeeping import summarize_token_bookkeeping

logger = logging.getLogger(__name__)


from .tuners.llm_trimming import LLMTrimmingTuner


def _is_fatal_llm_http_error(exc: Exception) -> bool:
    """Detect fatal LLM errors without importing tuner internals."""
    fatal_names = {"LLMHTTPStatusError", "LLMTimeoutExhaustedError"}
    return any(cls.__name__ in fatal_names for cls in type(exc).mro())

class SimpleDualLoopOptimizer:
    """Dual-loop optimizer with Actor (Reasoning) and Speculator (Quick) agents."""
    
    def __init__(self, config: SimpleConfig, benchmark: BenchmarkInterface):
        """Initialize dual-loop optimizer.
        
        Args:
            config: Configuration object
            benchmark: Benchmark implementation
        """
        self.config = config
        self.benchmark = benchmark
        self.param_manager = ParameterManager()
        
        # Trimming support
        self.trimming_cycles = config.trimming_cycles if config.trimming_enabled else 0
        self.trimming_tuner: Optional[LLMTrimmingTuner] = None
        self._trimming_phase_complete = False
        if config.trimming_enabled:
            # Determine agent type based on strategy
            # single_loop (default): Actor (Reasoning) performs trimming
            # dual_loop: Speculator (Quick) performs trimming exploration
            trim_strategy = getattr(config, 'trimming_strategy', 'single_loop')
            trim_agent_type = "quick" if trim_strategy == "dual_loop" else "reasoning"
            
            logger.info(f"Initializing Trimming Tuner with strategy='{trim_strategy}' using agent='{trim_agent_type}'")
            self.trimming_tuner = LLMTrimmingTuner(config, agent_type=trim_agent_type)
        
        # Create two LLM tuners
        # Speculator (Quick) - runs every window
        self.quick_tuner = LLMTuner(config, agent_type="quick")
        # Actor (Reasoning) - runs when previous request completed
        self.reasoning_tuner = LLMTuner(config, agent_type="reasoning")
        
        # Executors for async tuner calls
        #
        # Speculator requests should not serialize behind stale/slow quick calls.
        # We allow multiple in-flight quick calls and only honor the newest one;
        # older late responses are ignored.
        self.quick_executor = concurrent.futures.ThreadPoolExecutor(max_workers=32)
        self.reasoning_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        
        # State tracking
        self.iteration = 0
        self.history = []  # List of all iteration results
        self.baseline_index = 0  # Index separating "compressed" from "recent" history
        
        self.best_reward = float('-inf') if config.optimization_goal == 'maximize' else float('inf')
        self.best_parameters: Optional[Dict[str, int]] = None
        self.current_parameters: Optional[Dict[str, int]] = None
        self.start_time = time.time()
        
        # Synchronization
        self.parameters_lock = threading.Lock()
        self.history_lock = threading.Lock()
        self.running = True
        self._history_saved = False
        
        # Pending futures for async tuner calls
        self.pending_quick_future = None
        self.pending_reasoning_future = None
        self.stale_quick_futures: List[concurrent.futures.Future] = []
        
        # Tuner timing tracking
        self.quick_tuner_call_time = None
        self.reasoning_tuner_call_time = None
        self.reasoning_tuner_start_iteration = None
        
        # Track previous request timestamps for aggregation interval calculation
        self.last_quick_request_time = None
        self.last_reasoning_request_time = None
        
        # Window timing for aggregation
        self.current_window_start_time = None
        self.final_actor_required = bool(getattr(config, 'dual_loop_force_final_actor_before_stable', False))
        self.final_actor_request_active = False
        self.final_actor_timing: Optional[Dict[str, Any]] = None

    def _total_iterations(self) -> int:
        """Total benchmark windows including post-tuning measurement windows."""
        return self.config.max_iterations + self.config.post_tuning_windows

    def _should_measure_default_baseline(self) -> bool:
        """Whether to collect a pre-tuning default window for dual-loop LLM runs."""
        return bool(getattr(self.config, "llm_measure_default_before_tuning", False))

    def _record_default_baseline(self, metrics: BenchmarkMetrics, parameters: Dict[str, Any]) -> None:
        """Record a pre-tuning Initial/OS Default measurement in dual-loop history."""
        if metrics is None:
            return

        metrics_payload = dict(metrics.extra_metrics or {})
        system_metrics = metrics_payload.pop("system_metrics", None)
        reward = metrics.get_metric(self.config.optimization_metric)
        history_entry = {
            "iteration": 0,
            "timestamp": time.time(),
            "parameters": parameters.copy(),
            "metrics": {
                "throughput": metrics.throughput,
                "goodput": metrics.goodput,
                "latency_avg": metrics.latency_avg,
                "latency_p95": metrics.latency_p95,
                **metrics_payload,
            },
            "reward": reward,
            "tuner_timing": {},
            "post_tuning_phase": False,
            "pre_tuning_default_config": True,
        }
        if system_metrics:
            history_entry["system_metrics"] = system_metrics
        self._record_trial(history_entry)

        if reward is not None:
            self.best_reward = reward
            self.best_parameters = parameters.copy()

    def _measure_default_baseline_window(self) -> None:
        """Execute one fixed/default pre-tuning window and add it to dual-loop context."""
        if not self._should_measure_default_baseline():
            return

        logger.info("Collecting pre-tuning baseline window on Initial/OS Default config...")
        metrics = self._execute_window_simple(0)
        if not metrics:
            logger.warning("Pre-tuning baseline window produced no metrics")
            return
        self._record_default_baseline(metrics, self.current_parameters)
        reward = metrics.get_metric(self.config.optimization_metric)
        if reward is not None:
            logger.info(
                "Recorded Initial/OS Default baseline: %s=%.4f",
                self.config.optimization_metric,
                reward,
            )

    def _clear_pending_quick_request(self) -> None:
        """Cancel/drop any in-flight Speculator request."""
        if self.pending_quick_future:
            if not self.pending_quick_future.done():
                logger.info("Cancelling pending Speculator request")
                self.pending_quick_future.cancel()
            self.pending_quick_future = None
        for future in self.stale_quick_futures:
            if not future.done():
                future.cancel()
        self.stale_quick_futures = []
        self.quick_tuner_call_time = None

    def _reap_stale_quick_futures(self) -> None:
        """Drop bookkeeping references for stale quick requests that have finished."""
        if not self.stale_quick_futures:
            return
        self.stale_quick_futures = [future for future in self.stale_quick_futures if not future.done()]

    def _attach_final_actor_timing_to_history(self, timing: Optional[Dict[str, Any]]) -> None:
        """Attach the stable-gating final Actor timing block to the latest history entry."""
        if not timing or not self.history:
            return
        last_entry = self.history[-1]
        if not isinstance(last_entry.get("tuner_timing"), dict):
            last_entry["tuner_timing"] = {}
        last_entry["tuner_timing"]["reasoning_final_before_stable"] = timing

    def _mark_final_actor_timing(self, timing: Optional[Dict[str, Any]]) -> None:
        """Record completion of the required final Actor request."""
        if timing is None or not self.final_actor_request_active:
            return
        self.final_actor_timing = timing
        self.final_actor_request_active = False

    def _clear_pending_tuner_requests(self) -> None:
        """Cancel/drop any in-flight tuner requests before post-tuning measurements."""
        self._clear_pending_quick_request()

        if self.pending_reasoning_future:
            if not self.pending_reasoning_future.done():
                logger.info("Cancelling pending Actor request before post-tuning phase")
                self.pending_reasoning_future.cancel()
            self.pending_reasoning_future = None

        self.reasoning_tuner_call_time = None
        self.reasoning_tuner_start_iteration = None
        self.final_actor_request_active = False
    
    def run(self) -> Dict[str, Any]:
        """Run the dual-loop optimization process.
        
        Flow:
        - (Optional) Trimming Phase: narrow search space using trimming tuner
        - Main Phase:
            - At each window start: send Speculator request, send Actor request (if previous completed)
            - In-window mode: apply parameters as responses arrive during window
            - Outside-of-window mode: wait for BOTH responses before starting next window
        
        Returns:
            Dictionary with optimization results
        """
        logger.info("=" * 60)
        logger.info("Starting Actor-Speculator Dual-Loop Optimization")
        logger.info(f"Benchmark: {self.config.benchmark}")
        spec_model = getattr(self.config, 'llm_speculator_model', None) or self.config.llm_secondary_model
        actor_model = getattr(self.config, 'llm_actor_model', None) or self.config.llm_model_name
        logger.info(f"Speculator (Quick): {spec_model}")
        logger.info(f"Actor (Reasoning): {actor_model}")
        
        if self.trimming_tuner:
            logger.info(f"Trimming phase enabled: {self.trimming_cycles} cycles (model: {spec_model})")
            
        logger.info(f"Optimizing for: {self.config.optimization_goal} {self.config.optimization_metric}")
        total_iterations = self._total_iterations()
        logger.info(f"Tuning iterations: {self.config.max_iterations}")
        logger.info(f"Post-tuning measurement windows: {self.config.post_tuning_windows}")
        logger.info(f"Total windows: {total_iterations}")
        logger.info(f"Tuning mode: {self.config.tuning_mode}")
        actor_only_final = bool(getattr(self.config, 'dual_loop_actor_only_final', False))
        if actor_only_final:
            logger.info("Actor dispatch mode: final-only (one Actor call after all windows)")
        if self.final_actor_required:
            logger.info("Stable-phase gate: final Actor response is required before stable measurements begin")
        logger.info("=" * 60)
        
        try:
            # Pre-execute benchmark setup
            if hasattr(self.benchmark, 'pre_execute'):
                if not self.benchmark.pre_execute():
                    raise RuntimeError("Benchmark pre-execution failed")
            
            experiment_params = (
                set(self.config.parameter_ranges.keys())
                | set(self.config.fixed_parameters.keys())
            )

            # For new parameters, reset only those selected by this experiment.
            new_params_to_reset = experiment_params & get_new_parameter_names()
            if new_params_to_reset:
                logger.info(
                    "Resetting selected new parameters to system defaults at run start: %s",
                    sorted(new_params_to_reset),
                )
                reset_new_parameters_to_system_defaults(
                    self.param_manager,
                    parameters_to_reset=new_params_to_reset,
                    refresh_snapshot=True,
                )
            
            # Initialize only the parameters selected by this experiment.
            initial_params = get_selected_default_parameters(experiment_params)
            initial_params.update(self.config.fixed_parameters)
            self.current_parameters = initial_params.copy()
            logger.info(f"Starting with parameters: {self.current_parameters}")
            
            # Apply initial parameters
            if not self.param_manager.set_parameters(self.current_parameters):
                raise RuntimeError("Failed to apply initial parameters")
            
            self.running = True
            
            # --- Trimming Phase ---
            self._measure_default_baseline_window()
            if self.trimming_tuner:
                self._run_trimming_phase()
            
            # --- Main Loop ---
            post_phase_start = self.config.max_iterations + 1
            for iteration in range(1, total_iterations + 1):
                self.iteration = iteration
                logger.info(f"=== ITERATION {iteration}/{total_iterations} ===")
                tuning_active = iteration <= self.config.max_iterations
                in_post_tuning_phase = not tuning_active
                
                if iteration == post_phase_start and self.config.post_tuning_windows > 0:
                    if self.final_actor_required:
                        logger.info(
                            "Preparing post-tuning measurement phase: waiting for the final Actor response "
                            "before frozen measurements begin"
                        )
                        self._clear_pending_quick_request()
                        final_reasoning_timing = self._ensure_final_actor_before_stable(post_phase_start)
                        self._attach_final_actor_timing_to_history(final_reasoning_timing)
                    logger.info(
                        "Entering post-tuning measurement phase: "
                        f"{self.config.post_tuning_windows} windows with frozen parameters"
                    )
                    self._clear_pending_quick_request()

                if tuning_active:
                    # 1. At window start: dispatch BOTH requests
                    # Speculator: always dispatch
                    initial_agg_interval = float(self.config.window_duration) if iteration == 1 or self.last_quick_request_time is None else None
                    self._start_quick_tuner_request(iteration, aggregation_interval_s=initial_agg_interval)
                    
                    # Actor: dispatch ONLY if previous Actor request has completed
                    # Optional mode: skip Actor during windows and call once at the end.
                    if not actor_only_final:
                        if self.final_actor_required and iteration == self.config.max_iterations:
                            self._force_start_reasoning_tuner_request(iteration)
                        else:
                            self._maybe_start_reasoning_tuner_request(iteration)
                
                # 2. Execute Window & Monitor for responses
                if tuning_active and self.config.tuning_mode == "in-window":
                    # In-window mode: apply parameters as they arrive during window
                    metrics = self._execute_window_and_monitor(iteration)
                elif tuning_active:
                    # Outside-of-window mode: just run the window, wait for responses after
                    metrics = self._execute_window_simple(iteration)
                    # Wait for BOTH responses before proceeding
                    oow_timing = self._wait_for_both_responses(iteration)
                    # Store timing info in metrics (since _execute_window_simple doesn't have it)
                    if metrics and oow_timing:
                        if not hasattr(metrics, 'extra_metrics'):
                            metrics.extra_metrics = {}
                        if oow_timing.get('quick_tuner_timing'):
                            metrics.extra_metrics['quick_tuner_timing'] = oow_timing['quick_tuner_timing']
                        if oow_timing.get('reasoning_tuner_timing'):
                            metrics.extra_metrics['reasoning_tuner_timing'] = oow_timing['reasoning_tuner_timing']
                else:
                    # Post-tuning phase: no LLM calls, only measure with frozen parameters.
                    metrics = self._execute_window_simple(iteration)
                
                if not metrics:
                    logger.warning(f"No metrics from iteration {iteration}")
                    continue
                
                # 3. Process Results
                reward = metrics.get_metric(self.config.optimization_metric)
                if reward is not None:
                    logger.info(f"Iteration {iteration} reward: {reward:.4f}")
                else:
                    logger.warning(f"Iteration {iteration} reward: None")
                    reward = 0.0
                
                # Update best
                is_better = (
                    (self.config.optimization_goal == 'maximize' and reward > self.best_reward) or
                    (self.config.optimization_goal == 'minimize' and reward < self.best_reward)
                )
                if is_better:
                    self.best_reward = reward
                    self.best_parameters = self.current_parameters.copy()
                    logger.info(f"New best parameters: {self.best_parameters} with reward {self.best_reward:.4f}")
                
                # 4. Add to History
                system_metrics = metrics.extra_metrics.pop("system_metrics", None)
                quick_tuner_timing = metrics.extra_metrics.pop("quick_tuner_timing", None)
                reasoning_tuner_timing = metrics.extra_metrics.pop("reasoning_tuner_timing", None)
                
                history_entry = {
                    "iteration": iteration,
                    "timestamp": time.time(),
                    "parameters": self.current_parameters.copy(),
                    "metrics": {
                        "throughput": metrics.throughput,
                        "goodput": metrics.goodput,
                        "latency_avg": metrics.latency_avg,
                        "latency_p95": metrics.latency_p95,
                        **metrics.extra_metrics
                    },
                    "reward": reward,
                    "tuner_timing": {},
                    "post_tuning_phase": in_post_tuning_phase,
                }
                
                if system_metrics:
                    history_entry["system_metrics"] = system_metrics
                if quick_tuner_timing:
                    history_entry["tuner_timing"]["quick"] = quick_tuner_timing
                if reasoning_tuner_timing:
                    history_entry["tuner_timing"]["reasoning"] = reasoning_tuner_timing
                
                self._record_trial(history_entry)

                if history_entry["metrics"].get("dcperf_online_completed_early"):
                    logger.warning(
                        "Benchmark completed early at iteration %s; finishing dual-loop run with final "
                        "aggregate metrics from this window",
                        iteration,
                    )
                    return self._finish("completed_early")
                
            # Optional end-of-run Actor call (advisory; no further window executes).
            if actor_only_final and self.config.post_tuning_windows == 0:
                final_reasoning_timing = self._run_final_actor_request()
                if final_reasoning_timing and self.history:
                    last_entry = self.history[-1]
                    if not isinstance(last_entry.get("tuner_timing"), dict):
                        last_entry["tuner_timing"] = {}
                    last_entry["tuner_timing"]["reasoning_final"] = final_reasoning_timing
            elif actor_only_final and self.config.post_tuning_windows > 0:
                logger.info("Skipping final-only Actor advisory call because post-tuning phase disables all tuner requests")
            
            return self._finish("completed")
            
        except KeyboardInterrupt:
            logger.info("Optimization interrupted by user")
            return self._finish("interrupted")
        except Exception as e:
            logger.error(f"Error during optimization: {e}", exc_info=True)
            return self._finish("error")

    def _record_trial(self, history_entry: Dict[str, Any]):
        """Record a trial to history."""
        self.history.append(history_entry)

    def _run_trimming_phase(self):
        """Execute the initial trimming phase to narrow parameter ranges."""
        logger.info(f"Starting trimming phase for {self.trimming_cycles} cycles...")
        
        for i in range(1, self.trimming_cycles + 1):
            logger.info(f"--- Trimming Cycle {i}/{self.trimming_cycles} ---")
            
            # 1. Get suggestion
            # Strategy "single_loop": Use trimming_tuner (Actor) directly
            # Strategy "dual_loop": Use quick_tuner (Speculator) to valid/explore, record to trimming_tuner
            
            suggestion = None
            used_tuner = "trimming_tuner"
            
            if getattr(self.config, 'trimming_strategy', 'single_loop') == 'dual_loop':
                # Use Speculator for suggestions during trimming
                # We use quick_tuner but formatted as synchronous request here
                logger.info("Trimming Strategy: Dual Loop (Speculator exploring)")
                used_tuner = "quick_tuner"
                
                # We need to construct a prompt for the quick tuner
                # Quick tuner usually runs async and assumes recent history
                # Here we just want a suggestion based on current state
                suggestion, _ = self.quick_tuner.get_suggestion(
                    history=self.history + [self._create_pending_entry(i)], # Add pending entry for context
                    current_params=self.current_parameters,
                    iteration=i
                )
            else:
                # Default: Single Loop (Actor trimming)
                used_tuner = "trimming_tuner"
                suggestion = self.trimming_tuner.suggest_parameters(
                    metrics=self.latest_metrics if hasattr(self, 'latest_metrics') and self.latest_metrics else None,
                    current_params=self.current_parameters,
                    iteration=i,
                    best_reward=self.best_reward
                )
            
            if not suggestion:
                logger.warning(f"Trimming: No suggestion from {used_tuner}")
                # Fallback to random or just continue with current
                pass
            
            # 2. Apply parameters
            if suggestion and suggestion.param_changes:
                logger.info(f"Trimming suggestion ({used_tuner}): {suggestion.param_changes}")
                # Filter out params not in current ranges (e.g. eliminated ones)
                # But typically tuner handles this. Just strictly update.
                for k, v in suggestion.param_changes.items():
                    if k in self.config.fixed_parameters:
                        continue # Should typically not happen if tuner respects it
                    self.current_parameters[k] = v
                
                self.param_manager.set_parameters(self.current_parameters)
            else:
                logger.info(f"Trimming: no parameter changes suggested this cycle ({used_tuner})")
                
            # 3. Execute window (synchronously for trimming)
            # Call workload update if available
            if self.benchmark and hasattr(self.benchmark, 'update_workload'):
                self.benchmark.update_workload(i)
                
            metrics = self._execute_window_simple(i, is_trimming=True)
            if not metrics:
                logger.warning(f"Trimming: no metrics from cycle {i}")
                continue
                
            # 4. Record trial
            reward = metrics.get_metric(self.config.optimization_metric) or 0.0
            
            # Update best
            is_better = (
                (self.config.optimization_goal == 'maximize' and reward > self.best_reward) or
                (self.config.optimization_goal == 'minimize' and reward < self.best_reward)
            )
            if is_better:
                self.best_reward = reward
                self.best_parameters = self.current_parameters.copy()
            
            # Add to history with trimming_phase tag
            history_entry = {
                "iteration": i,
                "timestamp": time.time(),
                "parameters": self.current_parameters.copy(),
                "metrics": {
                    "throughput": metrics.throughput,
                    "goodput": metrics.goodput,
                    "latency_avg": metrics.latency_avg,
                    "latency_p95": metrics.latency_p95,
                    **metrics.extra_metrics
                },
                "reward": reward,
                "trimming_phase": True,
                "llm_justification": suggestion.justification if suggestion else None,
                "tuner_type": used_tuner
            }
            
            # Record to MAIN history
            self._record_trial(history_entry)
            
            # Record to Trimming Tuner history (Crucial for it to learn)
            # Even if Speculator generated the suggestion, the Actor/Trimming tuner needs to see the result
            self.trimming_tuner.record_result(history_entry)
            
            # Keep track of latest metrics for next suggestion
            self.latest_metrics = metrics
            
        # End of trimming loop
        self._apply_trimmed_ranges()

    def _apply_trimmed_ranges(self):
        """Finalize trimming phase: and re-create primary tuners."""
        if self._trimming_phase_complete:
            return
        
        self._trimming_phase_complete = True
        
        # Get ranges
        effective_ranges = self.trimming_tuner.get_effective_ranges()
        eliminated_params = self.trimming_tuner.get_eliminated_params()
        
        logger.info("=" * 60)
        logger.info("TRIMMING COMPLETED — Transitioning to Dual Loop")
        logger.info("=" * 60)
        if hasattr(self.trimming_tuner, 'get_trimming_summary'):
            logger.info(self.trimming_tuner.get_trimming_summary())
            
        # Handle eliminated parameters
        if eliminated_params:
            if not getattr(self.config, 'fixed_parameters', None):
                self.config.fixed_parameters = {}
            for p, v in eliminated_params.items():
                self.config.fixed_parameters[p] = v
                # Update current parameters to match fixed value
                self.current_parameters[p] = v
                # Remove from tuning list if present
                if hasattr(self.config, 'parameters_to_tune') and self.config.parameters_to_tune:
                    if p in self.config.parameters_to_tune:
                        self.config.parameters_to_tune.remove(p)
        
        # Update config
        self.config.parameter_ranges = effective_ranges
        
        # Re-create Actor and Speculator with new ranges/fixed params
        logger.info("Re-initializing Actor and Speculator with trimmed ranges...")
        self.quick_tuner = LLMTuner(self.config, agent_type="quick")
        self.reasoning_tuner = LLMTuner(self.config, agent_type="reasoning")


    # ... keep existing methods ...





    def _get_metrics_from_history(self, history_entry: Dict[str, Any]) -> BenchmarkMetrics:
        """Reconstruct BenchmarkMetrics from history entry dict."""
        m_dict = history_entry.get('metrics', {})
        metrics = BenchmarkMetrics(
            throughput=m_dict.get('throughput', 0.0),
            goodput=m_dict.get('goodput', 0.0),
            latency_avg=m_dict.get('latency_avg', 0.0),
            latency_p95=m_dict.get('latency_p95', 0.0)
        )
        # Add other metrics to extra_metrics
        for k, v in m_dict.items():
            if k not in ['throughput', 'goodput', 'latency_avg', 'latency_p95']:
                metrics.extra_metrics[k] = v
        return metrics
    
    def _log_metrics_summary(self, agent_name: str, iteration: int, call_iteration: int,
                              metrics: BenchmarkMetrics, params: Dict[str, Any],
                              aggregation_interval_s: Optional[float] = None,
                              baseline: int = 0, history_len: int = 0):
        """Log a summary of metrics before dispatching a tuner request.
        
        Args:
            agent_name: "Speculator" or "Actor"
            iteration: Current window iteration
            call_iteration: Iteration number metrics are from (for tuner context)
            metrics: Benchmark metrics being sent
            params: Current tunable parameters
            aggregation_interval_s: Aggregation interval if available
            baseline: Baseline index (for Actor)
            history_len: History length (for Actor)
        """
        # Build metrics summary
        opt_metric = self.config.optimization_metric
        opt_value = metrics.get_metric(opt_metric)
        constraint_metric = self.config.constraint_metric
        constraint_value = metrics.get_metric(constraint_metric) if constraint_metric else None
        
        # Format key metrics
        summary_parts = [f"Dispatching {agent_name} for iter {iteration} (based on #{call_iteration})"]
        
        if aggregation_interval_s:
            summary_parts.append(f"agg_interval={aggregation_interval_s:.2f}s")
        
        if agent_name == "Actor":
            summary_parts.append(f"baseline={baseline}, history_len={history_len}")
        
        logger.info(" | ".join(summary_parts))
        
        # Log metrics being sent
        metrics_info = []
        metrics_info.append(f"  {opt_metric}={opt_value}" if opt_value is not None else f"  {opt_metric}=None")
        if constraint_metric:
            metrics_info.append(f"{constraint_metric}={constraint_value}" if constraint_value is not None else f"{constraint_metric}=None")
        
        # Add other key metrics
        if metrics.throughput:
            metrics_info.append(f"throughput={metrics.throughput:.2f}")
        if metrics.latency_avg:
            metrics_info.append(f"latency_avg={metrics.latency_avg:.2f}")
        if metrics.latency_p95:
            metrics_info.append(f"latency_p95={metrics.latency_p95:.2f}")
        
        # Power metrics if available
        if metrics.power_socket0_watts is not None:
            metrics_info.append(f"power={metrics.power_socket0_watts:.2f}W")
        
        # Extra metrics (latency_p99, etc.)
        if 'latency_p99' in metrics.extra_metrics:
            metrics_info.append(f"latency_p99={metrics.extra_metrics['latency_p99']:.2f}")
        
        logger.info(f"  Metrics: {', '.join(metrics_info)}")
        logger.info(f"  Params: {json.dumps(params)}")

    def _start_quick_tuner_request(self, iteration: int, aggregation_interval_s: Optional[float] = None):
        """Start the Speculator (Quick Agent) request.
        
        Args:
            iteration: Current iteration number
            aggregation_interval_s: Optional aggregation interval in seconds for metrics context
        """
        self._reap_stale_quick_futures()

        # Speculator requests must not block future windows. If an older quick
        # request is still in flight, drop it from the active slot, dispatch a
        # fresh quick request, and ignore the older response if it arrives late.
        if self.pending_quick_future is not None:
            if not self.pending_quick_future.done():
                logger.info(
                    f"Speculator still processing previous request; "
                    f"dispatching a fresh quick request for iteration {iteration} and ignoring the older late response"
                )
                self.pending_quick_future.cancel()
                self.stale_quick_futures.append(self.pending_quick_future)
            else:
                logger.info(
                    f"Dropping completed stale Speculator response before dispatching iteration {iteration}; "
                    "the late quick response will be ignored"
                )
            self.pending_quick_future = None
            self.quick_tuner_call_time = None
        
        # Prepare context
        with self.history_lock:
            # Copy history to avoid race conditions during JSON serialization in tuner
            history_snapshot = [h.copy() for h in self.history]
            current_baseline = self.baseline_index
        
        # Determine metrics and params from PREVIOUS iteration (to prompt for NEXT)
        if history_snapshot:
            last_entry = history_snapshot[-1]
            last_metrics = self._get_metrics_from_history(last_entry)
            last_params = last_entry.get('parameters', self.current_parameters)
            call_iteration = last_entry.get('iteration', 0)
        else:
            # First iteration, no history
            last_metrics = BenchmarkMetrics()
            last_params = self.current_parameters
            call_iteration = 0
            
        # Filter tunable parameters
        tunable_params = {k: v for k, v in last_params.items() 
                         if k in self.config.parameter_ranges}
        
        # Calculate aggregation interval if not provided
        current_time = time.time()
        if aggregation_interval_s is None:
            if self.last_quick_request_time is not None:
                # Use time since last request for continuous apply
                aggregation_interval_s = current_time - self.last_quick_request_time
            elif self.current_window_start_time is not None:
                # Use time since window start
                aggregation_interval_s = current_time - self.current_window_start_time

        # Optional per-config override for speculator sampling/aggregation hint
        spec_interval_override = getattr(self.config, 'llm_speculator_aggregation_interval_s', None)
        if spec_interval_override is not None:
            try:
                spec_interval_override = float(spec_interval_override)
                if spec_interval_override > 0:
                    aggregation_interval_s = spec_interval_override
            except (TypeError, ValueError):
                logger.warning(
                    f"Invalid llm_speculator_aggregation_interval_s={spec_interval_override}; "
                    "using computed aggregation interval instead"
                )
        
        # Log metrics summary before dispatch
        self._log_metrics_summary("Speculator", iteration, call_iteration, last_metrics, tunable_params, aggregation_interval_s)
        
        # Track when quick tuner was called
        self.quick_tuner_call_time = current_time
        self.last_quick_request_time = current_time
        
        self.pending_quick_future = self.quick_executor.submit(
            self.quick_tuner.suggest_parameters,
            metrics=last_metrics,
            current_params=tunable_params,
            iteration=call_iteration, # Tuner will ask for call_iteration + 1
            best_reward=self.best_reward,
            history=history_snapshot,
            baseline_index=current_baseline,
            aggregation_interval_s=aggregation_interval_s
        )

    def _execute_window_and_monitor(self, iteration: int) -> Optional[BenchmarkMetrics]:
        """Execute benchmark window while monitoring for BOTH Speculator and Actor responses.
        
        outside-of-window mode: Apply parameters as responses arrive during window execution.
        When continuous_apply is enabled, sends next Speculator request immediately 
        when previous one arrives, allowing multiple requests per window.
        """
        window_duration = self.config.window_duration
        check_interval = 0.1
        
        # Track window start time for aggregation interval calculation
        window_start_time = time.time()
        self.current_window_start_time = window_start_time
        
        # Start benchmark in thread
        metrics_result = [None]
        error_result = [None]
        
        def run_benchmark():
            try:
                if self.benchmark and hasattr(self.benchmark, 'update_workload'):
                    self.benchmark.update_workload(iteration)
                    
                metrics_result[0] = self.benchmark.execute_window(
                    iteration, 
                    self.config.window_duration
                )
            except Exception as e:
                error_result[0] = e
        
        bench_thread = threading.Thread(target=run_benchmark, daemon=True)
        bench_thread.start()
        
        # Monitor loop - track responses from BOTH agents
        quick_response_count = 0
        all_quick_timings = []
        reasoning_timing = None
        continuous_apply = getattr(self.config, 'continuous_apply', False)
        
        while bench_thread.is_alive():
            # Check Speculator (Quick Agent)
            if self.pending_quick_future and self.pending_quick_future.done():
                try:
                    response = self.pending_quick_future.result(timeout=0)
                    response_time = time.time()
                    duration = response_time - self.quick_tuner_call_time if self.quick_tuner_call_time else 0
                    
                    quick_tuner_timing = {
                        "tuner_call_time": self.quick_tuner_call_time,
                        "tuner_response_time": response_time,
                        "tuner_duration": duration,
                        "tuner_response_time_ms": duration * 1000,
                        "tuner_type": "speculator_quick",
                        "parameters_applied": bool(response and response.parameters),
                        "parameters_applied_timestamp": None,
                        "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                        "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                        "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                        "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
                        "response_number": quick_response_count + 1
                    }
                    all_quick_timings.append(quick_tuner_timing)
                    
                    applied_ts = self._apply_tuner_response(response, "Speculator")
                    quick_tuner_timing["parameters_applied_timestamp"] = applied_ts
                    quick_response_count += 1
                    
                    # Clear the pending future
                    self.pending_quick_future = None
                    
                    # If continuous_apply is enabled, immediately send next request
                    if continuous_apply and bench_thread.is_alive():
                        # Calculate aggregation interval: time since last request
                        agg_interval = response_time - self.quick_tuner_call_time if self.quick_tuner_call_time else duration
                        logger.info(f"Continuous apply: Immediately dispatching next Speculator request (response #{quick_response_count} took {duration:.2f}s)")
                        self._start_quick_tuner_request(iteration, aggregation_interval_s=agg_interval)
                        
                except Exception as e:
                    if _is_fatal_llm_http_error(e):
                        raise
                    logger.error(f"Speculator error: {e}")
                    self.pending_quick_future = None  # Clear to prevent infinite retry
            
            # Check Actor (Reasoning Agent)
            if self.pending_reasoning_future and self.pending_reasoning_future.done():
                try:
                    response = self.pending_reasoning_future.result(timeout=0)
                    response_time = time.time()
                    duration = response_time - self.reasoning_tuner_call_time if self.reasoning_tuner_call_time else 0
                    
                    reasoning_timing = {
                        "tuner_call_time": self.reasoning_tuner_call_time,
                        "tuner_start_iteration": getattr(self, 'reasoning_tuner_start_iteration', None),
                        "tuner_response_time": response_time,
                        "tuner_duration": duration,
                        "tuner_response_time_ms": duration * 1000,
                        "tuner_type": "actor_reasoning",
                        "parameters_applied": bool(response and response.parameters),
                        "parameters_applied_timestamp": None,
                        "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                        "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                        "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                        "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
                    }
                    
                    applied_ts = self._apply_tuner_response(response, "Actor")
                    reasoning_timing["parameters_applied_timestamp"] = applied_ts
                    
                    # Update baseline to commit history when Actor responds
                    self._commit_baseline_on_actor_response()
                    self._mark_final_actor_timing(reasoning_timing)
                    
                    # Clear the pending future (Actor can be dispatched again next window start)
                    self.pending_reasoning_future = None
                    
                except Exception as e:
                    if _is_fatal_llm_http_error(e):
                        raise
                    logger.error(f"Actor error: {e}")
                    self.pending_reasoning_future = None
            
            time.sleep(check_interval)
        
        bench_thread.join()
        
        if error_result[0]:
            raise error_result[0]
        
        # Store timing info in metrics extra_metrics
        if metrics_result[0]:
            if not hasattr(metrics_result[0], 'extra_metrics'):
                metrics_result[0].extra_metrics = {}
            # Store the last Speculator timing as primary, but also include all timings
            metrics_result[0].extra_metrics['quick_tuner_timing'] = all_quick_timings[-1] if all_quick_timings else None
            metrics_result[0].extra_metrics['all_quick_tuner_timings'] = all_quick_timings
            metrics_result[0].extra_metrics['quick_response_count'] = quick_response_count
            # Store Actor timing if it responded during this window
            if reasoning_timing:
                metrics_result[0].extra_metrics['reasoning_tuner_timing'] = reasoning_timing
            
        return metrics_result[0]
    
    def _execute_window_simple(self, iteration: int) -> Optional[BenchmarkMetrics]:
        """Execute benchmark window without monitoring for responses (outside-of-window mode).
        
        Just runs the benchmark and returns metrics. Responses are handled separately.
        """
        window_duration = self.config.window_duration
        
        # Track window start time
        self.current_window_start_time = time.time()
        
        try:
            if self.benchmark and hasattr(self.benchmark, 'update_workload'):
                self.benchmark.update_workload(iteration)
            
            metrics = self.benchmark.execute_window(
                window_number=iteration,
                duration=window_duration
            )
            return metrics
        except Exception as e:
            logger.error(f"Benchmark error in window {iteration}: {e}")
            return None
    
    def _create_pending_entry(self, iteration: int) -> Dict[str, Any]:
        """Create a pending history entry for context."""
        return {
            "iteration": iteration,
            "timestamp": time.time(),
            "parameters": self.current_parameters.copy(),
            "metrics": {},
            "reward": 0.0
        }

    def _wait_for_both_responses(self, iteration: int) -> Dict[str, Any]:
        """Wait for BOTH Speculator and Actor responses before proceeding (outside-of-window mode).
        
        Applies parameters after both responses arrive.
        
        Returns:
            Dictionary with 'quick_tuner_timing' and 'reasoning_tuner_timing' keys.
        """
        logger.info(f"Waiting for tuner responses (outside-of-window mode)...")
        
        quick_timing = None
        reasoning_timing = None
        
        # Wait for Speculator
        if self.pending_quick_future:
            try:
                response = self.pending_quick_future.result(timeout=300)  # 5 min timeout
                response_time = time.time()
                duration = response_time - self.quick_tuner_call_time if self.quick_tuner_call_time else 0
                
                quick_timing = {
                    "tuner_call_time": self.quick_tuner_call_time,
                    "tuner_response_time": response_time,
                    "tuner_duration": duration,
                    "tuner_response_time_ms": duration * 1000,
                    "tuner_type": "speculator_quick",
                    "parameters_applied": bool(response and response.parameters),
                    "parameters_applied_timestamp": None,
                    "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                    "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                    "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                    "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
                }
                
                logger.info(f"Speculator responded (took {duration:.2f}s)")
                applied_ts = self._apply_tuner_response(response, "Speculator")
                quick_timing["parameters_applied_timestamp"] = applied_ts
                self.pending_quick_future = None
                
            except concurrent.futures.TimeoutError:
                logger.error("Speculator timed out")
                self.pending_quick_future = None
            except Exception as e:
                if _is_fatal_llm_http_error(e):
                    raise
                logger.error(f"Speculator error: {e}")
                self.pending_quick_future = None
        
        # Wait for Actor (if there's a pending request)
        if self.pending_reasoning_future:
            try:
                response = self.pending_reasoning_future.result(timeout=300)  # 5 min timeout
                response_time = time.time()
                duration = response_time - self.reasoning_tuner_call_time if self.reasoning_tuner_call_time else 0
                
                reasoning_timing = {
                    "tuner_call_time": self.reasoning_tuner_call_time,
                    "tuner_start_iteration": getattr(self, 'reasoning_tuner_start_iteration', None),
                    "tuner_response_time": response_time,
                    "tuner_duration": duration,
                    "tuner_response_time_ms": duration * 1000,
                    "tuner_type": "actor_reasoning",
                    "parameters_applied": bool(response and response.parameters),
                    "parameters_applied_timestamp": None,
                    "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                    "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                    "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                    "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
                }
                
                logger.info(f"Actor responded (took {duration:.2f}s)")
                applied_ts = self._apply_tuner_response(response, "Actor")
                reasoning_timing["parameters_applied_timestamp"] = applied_ts
                
                # Update baseline to commit history when Actor responds
                self._commit_baseline_on_actor_response()
                self._mark_final_actor_timing(reasoning_timing)
                
                self.pending_reasoning_future = None
                
            except concurrent.futures.TimeoutError:
                logger.warning("Actor timed out - will try again next window")
                # Don't clear the future, let it complete eventually
            except Exception as e:
                if _is_fatal_llm_http_error(e):
                    raise
                logger.error(f"Actor error: {e}")
                self.pending_reasoning_future = None
        
        logger.info("All available responses processed")
        
        return {
            'quick_tuner_timing': quick_timing,
            'reasoning_tuner_timing': reasoning_timing,
        }

    def _run_final_actor_request(self) -> Optional[Dict[str, Any]]:
        """
        Run one final Actor request after all benchmark windows complete.

        This is used for configurations that request Actor-at-end behavior.
        The response is recorded for analysis but not applied, since no further
        benchmark window will execute.
        """
        logger.info("Dispatching final Actor request...")
        started = self._maybe_start_reasoning_tuner_request(self.iteration + 1)
        if not started:
            logger.warning("Final Actor request was not started (request still pending)")
            return None

        if not self.pending_reasoning_future:
            logger.warning("Final Actor request missing future handle")
            return None

        try:
            final_actor_timeout = 300.0
            if hasattr(self.reasoning_tuner, "get_final_freeze_wait_timeout_seconds"):
                final_actor_timeout = self.reasoning_tuner.get_final_freeze_wait_timeout_seconds()
            response = self.pending_reasoning_future.result(timeout=final_actor_timeout)
            response_time = time.time()
            duration = response_time - self.reasoning_tuner_call_time if self.reasoning_tuner_call_time else 0.0

            reasoning_timing = {
                "tuner_call_time": self.reasoning_tuner_call_time,
                "tuner_start_iteration": getattr(self, 'reasoning_tuner_start_iteration', None),
                "tuner_response_time": response_time,
                "tuner_duration": duration,
                "tuner_response_time_ms": duration * 1000,
                "tuner_type": "actor_reasoning",
                "parameters_applied": False,
                "parameters_applied_timestamp": None,
                "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
            }

            logger.info(f"Final Actor responded (took {duration:.2f}s)")
            self.pending_reasoning_future = None
            return reasoning_timing
        except concurrent.futures.TimeoutError:
            logger.warning("Final Actor request timed out")
            self.pending_reasoning_future = None
            return None
        except Exception as e:
            if _is_fatal_llm_http_error(e):
                raise
            logger.error(f"Final Actor error: {e}")
            self.pending_reasoning_future = None
            return None

    def _force_start_reasoning_tuner_request(self, iteration: int) -> bool:
        """Force dispatch of the final Actor request for the last tuning iteration."""
        if self.pending_reasoning_future and not self.pending_reasoning_future.done():
            logger.info(
                "Cancelling older Actor request so the final tuning iteration always receives a fresh Actor call"
            )
            self.pending_reasoning_future.cancel()
            self.pending_reasoning_future = None

        started = self._maybe_start_reasoning_tuner_request(iteration)
        if started:
            self.final_actor_request_active = True
        return started

    def _start_refreshed_final_actor_request(self, iteration: int) -> None:
        """Dispatch a final Actor refresh using the completed last tuning window context."""
        if self.pending_reasoning_future:
            if not self.pending_reasoning_future.done():
                logger.info(
                    "Cancelling in-flight Actor request so the final stable-gating Actor uses the completed last tuning window"
                )
                self.pending_reasoning_future.cancel()
            self.pending_reasoning_future = None

        with self.history_lock:
            history_snapshot = [h.copy() for h in self.history]
            current_baseline = self.baseline_index

        if not history_snapshot:
            raise RuntimeError("Cannot issue final Actor refresh without completed tuning history")

        last_entry = history_snapshot[-1]
        last_metrics = self._get_metrics_from_history(last_entry)
        last_params = last_entry.get('parameters', self.current_parameters)
        call_iteration = last_entry.get('iteration', 0)
        tunable_params = {k: v for k, v in last_params.items() if k in self.config.parameter_ranges}

        current_time = time.time()
        if self.last_reasoning_request_time is not None:
            aggregation_interval_s = current_time - self.last_reasoning_request_time
        else:
            aggregation_interval_s = float(self.config.window_duration)

        phase_override = (
            "PHASE POLICY: this is the final Actor refresh after the completed last tuning window. "
            "Use the full tuning history including the latest completed measurement to choose the one "
            "configuration to freeze for the stable phase. Do not explore; prefer the best safe incumbent "
            "or a very nearby refinement.\n"
        )

        self._log_metrics_summary(
            "Actor-Final-Refresh",
            iteration,
            call_iteration,
            last_metrics,
            tunable_params,
            aggregation_interval_s,
            baseline=current_baseline,
            history_len=len(history_snapshot),
        )

        self.reasoning_tuner_call_time = current_time
        self.reasoning_tuner_start_iteration = iteration
        self.last_reasoning_request_time = current_time
        self.final_actor_timing = None
        self.final_actor_request_active = True
        self.pending_reasoning_future = self.reasoning_executor.submit(
            self.reasoning_tuner.suggest_parameters,
            metrics=last_metrics,
            current_params=tunable_params,
            iteration=call_iteration,
            best_reward=self.best_reward,
            history=history_snapshot,
            baseline_index=current_baseline,
            aggregation_interval_s=aggregation_interval_s,
            phase_instruction_override=phase_override,
            final_freeze_request=True,
        )

    def _ensure_final_actor_before_stable(self, iteration: int) -> Optional[Dict[str, Any]]:
        """Wait for the required final Actor reply before stable measurement."""
        # A response dispatched during the last tuning window only sees history
        # through the preceding window.  When the config requests the explicit
        # stable gate, always refresh after the completed last measurement;
        # otherwise replay timing (or an unusually fast provider response) can
        # incorrectly promote that stale response to the final decision.
        if getattr(self.config, "dual_loop_force_final_actor_before_stable", False):
            self._start_refreshed_final_actor_request(iteration)
        elif self.pending_reasoning_future and not self.pending_reasoning_future.done():
            self._start_refreshed_final_actor_request(iteration)
        elif self.final_actor_timing is not None:
            return self.final_actor_timing
        elif not self.pending_reasoning_future:
            raise RuntimeError("Stable phase reached without a usable final Actor response")

        try:
            final_actor_timeout = 300.0
            if hasattr(self.reasoning_tuner, "get_final_freeze_wait_timeout_seconds"):
                final_actor_timeout = self.reasoning_tuner.get_final_freeze_wait_timeout_seconds()
            response = self.pending_reasoning_future.result(timeout=final_actor_timeout)
            response_time = time.time()
            duration = response_time - self.reasoning_tuner_call_time if self.reasoning_tuner_call_time else 0

            reasoning_timing = {
                "tuner_call_time": self.reasoning_tuner_call_time,
                "tuner_start_iteration": getattr(self, 'reasoning_tuner_start_iteration', None),
                "tuner_response_time": response_time,
                "tuner_duration": duration,
                "tuner_response_time_ms": duration * 1000,
                "tuner_type": "actor_reasoning",
                "parameters_applied": bool(response and response.parameters),
                "parameters_applied_timestamp": None,
                "proposed_parameters": dict(response.parameters) if (response and response.parameters) else None,
                "justification": response.justification if (response and hasattr(response, 'justification')) else None,
                "converged": response.converged if (response and hasattr(response, 'converged')) else None,
                "token_metrics": response.token_metrics if (response and hasattr(response, 'token_metrics')) else None,
            }

            logger.info("Final Actor gating response received (took %.2fs)", duration)
            applied_ts = self._apply_tuner_response(response, "Actor")
            reasoning_timing["parameters_applied_timestamp"] = applied_ts
            self._commit_baseline_on_actor_response()

            self.pending_reasoning_future = None
            self._mark_final_actor_timing(reasoning_timing)
            return reasoning_timing
        except concurrent.futures.TimeoutError as exc:
            self.pending_reasoning_future = None
            self.final_actor_request_active = False
            raise RuntimeError("Timed out waiting for the required final Actor response before stable phase") from exc
        except Exception as e:
            if _is_fatal_llm_http_error(e):
                raise
            self.pending_reasoning_future = None
            self.final_actor_request_active = False
            raise RuntimeError(f"Final Actor failed before stable phase: {e}") from e

    def _maybe_start_reasoning_tuner_request(self, iteration: int) -> bool:
        """Start Actor (Reasoning) request ONLY if previous request has completed.
        
        Args:
            iteration: Current iteration number
            
        Returns:
            True if a new request was started, False if previous still pending
        """
        # Only dispatch if no pending Actor request
        if self.pending_reasoning_future and not self.pending_reasoning_future.done():
            logger.info(f"Actor still processing previous request, skipping dispatch for iteration {iteration}")
            return False
        
        # Prepare context
        with self.history_lock:
            history_snapshot = [h.copy() for h in self.history]
            current_baseline = self.baseline_index
        
        # Don't dispatch if no history yet (first iteration - wait for at least one result)
        # Actually, for first iteration we should still send Actor request with empty history
        if history_snapshot:
            last_entry = history_snapshot[-1]
            last_metrics = self._get_metrics_from_history(last_entry)
            last_params = last_entry.get('parameters', self.current_parameters)
            call_iteration = last_entry.get('iteration', 0)
        else:
            # First iteration, no history yet
            last_metrics = BenchmarkMetrics()
            last_params = self.current_parameters
            call_iteration = 0
        
        # Filter tunable parameters
        tunable_params = {k: v for k, v in last_params.items() 
                         if k in self.config.parameter_ranges}
        
        # Calculate aggregation interval for Actor
        current_time = time.time()
        if self.last_reasoning_request_time is not None:
            aggregation_interval_s = current_time - self.last_reasoning_request_time
        else:
            # First request - use window duration as estimate
            aggregation_interval_s = float(self.config.window_duration)
        
        # Log metrics summary before dispatch
        self._log_metrics_summary("Actor", iteration, call_iteration, last_metrics, tunable_params, aggregation_interval_s, baseline=current_baseline, history_len=len(history_snapshot))
        
        # Track timing
        self.reasoning_tuner_call_time = current_time
        self.reasoning_tuner_start_iteration = iteration
        self.last_reasoning_request_time = current_time
        
        # Submit async request
        self.pending_reasoning_future = self.reasoning_executor.submit(
            self.reasoning_tuner.suggest_parameters,
            metrics=last_metrics,
            current_params=tunable_params,
            iteration=call_iteration,
            best_reward=self.best_reward,
            history=history_snapshot,
            baseline_index=current_baseline,
            aggregation_interval_s=aggregation_interval_s
        )
        
        return True
    
    def _commit_baseline_on_actor_response(self) -> None:
        """Advance baseline index when Actor responds, committing recent history."""
        with self.history_lock:
            self.baseline_index = len(self.history)
            logger.info(f"Actor response committed. Advanced baseline_index to {self.baseline_index}")

    def _apply_tuner_response(self, response: TunerResponse, agent_name: str) -> Optional[float]:
        """Apply parameters from a tuner response safely.

        Returns:
            Unix timestamp (float) when parameters were successfully applied, else None.
        """
        if not response or not response.parameters:
            return None

        with self.parameters_lock:
            # Merge with existing fixed parameters
            new_params = self.config.fixed_parameters.copy()
            
            # We want to keep current tunable parameters that AREN'T in the response?
            # Or does the response imply a delta? 
            # Usually LLM gives the specific changes. 
            # We should overlay response on current_parameters.
            current_tunables = self.current_parameters.copy()
            
            changes_made = False
            for k, v in response.parameters.items():
                if k in self.config.parameter_ranges:
                    # Handle per-core
                    from .parameter_manager import is_per_core_parameter
                    bind_cores = is_per_core_parameter(k) and self.config.pin_to_cores
                    if k in {"busy_poll", "napi_busy_poll", "busy_read", "netdev_budget", "netdev_budget_usecs"}:
                        bind_cores = bind_cores and getattr(self.config, "bind_network_irqs", True)
                    if bind_cores:
                        val_obj = {"value": v, "cores": self.config.pin_to_cores}
                        current_tunables[k] = val_obj
                    else:
                        current_tunables[k] = v
                    changes_made = True
            
            if changes_made:
                if self.param_manager.set_parameters(current_tunables):
                    applied_ts = time.time()
                    self.current_parameters = current_tunables
                    logger.info(f"[{agent_name}] Applied updates: {json.dumps(response.parameters)}")
                    if response.justification:
                        logger.info(f"[{agent_name}] Justification: {response.justification}")
                    return applied_ts
                else:
                    logger.error(f"[{agent_name}] Failed to apply parameters")
                    return None
        return None

    def _finish(self, reason: str) -> Dict[str, Any]:
        """Finish optimization and save results."""
        total_time = time.time() - self.start_time
        
        # Stop optimization loop
        self.running = False
        
        # Generate Gist using Reasoning Agent
        gist = None
        gist_raw = None
        if hasattr(self.reasoning_tuner, 'generate_gist'):
            logger.info("Generating optimization gist (Reasoning Agent)...")
            try:
                # Use full history including trimming for better context
                # Now returns tuple: (summary_text, full_response_object)
                gist_result = self.reasoning_tuner.generate_gist(self.history)
                if isinstance(gist_result, tuple) and len(gist_result) == 2:
                    gist, gist_raw = gist_result
                else:
                    # Fallback for other tuners or old signature
                    gist = gist_result
                
                logger.info(f"OPTIMIZATION GIST: {gist}")
            except Exception as e:
                logger.error(f"Failed to generate gist: {e}")
        
        # Save history
        history_file = ""
        if not self._history_saved:
            try:
                # Pass gist to save history
                additional_data = {}
                if gist:
                    additional_data["optimizer_gist"] = gist
                if gist_raw:
                    additional_data["optimizer_gist_raw"] = gist_raw
                
                history_file = self._save_history(reason, additional_data=additional_data if additional_data else None)
            except Exception as e:
                logger.error(f"Failed to save history: {e}")
        
        # Cleanup benchmark first (kill any running processes)
        if hasattr(self.benchmark, 'cleanup'):
            try:
                logger.info("Cleaning up benchmark processes...")
                self.benchmark.cleanup()
            except KeyboardInterrupt:
                logger.warning("Interrupted during benchmark cleanup, forcing cleanup...")
                try:
                    self.benchmark.cleanup()
                except Exception:
                    pass
                raise  # Re-raise to exit without reset
            except Exception as e:
                logger.error(f"Error during benchmark cleanup: {e}")
        
        # Shutdown executors (cancel any pending LLM requests)
        try:
            logger.info("Shutting down executor threads...")
            self.quick_executor.shutdown(wait=False)
            self.reasoning_executor.shutdown(wait=False)
        except Exception as e:
            logger.error(f"Error shutting down executors: {e}")
        
        # Reset parameters (this can be interrupted)
        try:
            experiment_params = (
                set(self.config.parameter_ranges.keys())
                | set(self.config.fixed_parameters.keys())
            )
            base_params_to_reset = experiment_params - get_new_parameter_names()
            logger.info(
                "Resetting selected base parameters to defaults: %s",
                sorted(base_params_to_reset),
            )
            reset_selected_parameters_to_defaults(
                self.param_manager,
                base_params_to_reset,
            )
            
            new_param_names = get_new_parameter_names()
            new_params_to_reset = experiment_params & new_param_names
            if new_params_to_reset:
                logger.info(
                    f"Resetting tuned new parameters to system defaults: {sorted(new_params_to_reset)}"
                )
                reset_new_parameters_to_system_defaults(
                    self.param_manager,
                    parameters_to_reset=new_params_to_reset,
                )
            else:
                logger.info("No new parameters were tuned/fixed in this run; skipping new-parameter reset")
            logger.info("Parameters reset complete")
        except KeyboardInterrupt:
            logger.warning("Interrupted during parameter reset - exiting without completing reset")
            logger.warning("Some parameters may not be at default values")
            raise  # Re-raise to exit early
        except Exception as e:
            logger.error(f"Error resetting parameters: {e}")
        
        return {
            "best_parameters": self.best_parameters,
            "best_reward": self.best_reward,
            "iterations": self.iteration,
            "total_time": total_time,
            "terminated_reason": reason,
            "history_file": history_file,
            "optimizer_gist": gist
        }

    def _save_history(self, reason: str, additional_data: Optional[Dict[str, Any]] = None) -> str:
        """Save history to file."""
        os.makedirs(self.config.results_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dual_loop_actor_speculator_{self.config.benchmark}_{timestamp}.json"
        filepath = os.path.join(self.config.results_dir, filename)
        
        data = {
            "config": self.config.to_dict(),
            "best_parameters": self.best_parameters,
            "best_reward": self.best_reward,
            "iterations": self.iteration,
            "total_time": time.time() - self.start_time,
            "history": self.history,
            "token_bookkeeping": summarize_token_bookkeeping(
                self.history,
                int(getattr(self.config, "max_iterations", 0) or 0),
            ),
            "reason": reason,
            "mode": "actor-speculator"
        }
        
        if additional_data:
            data.update(additional_data)
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            
        self._history_saved = True
        logger.info(f"History saved to {filepath}")
        return filepath
