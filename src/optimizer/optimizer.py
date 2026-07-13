#!/usr/bin/env python3
"""
Single-loop optimizer for OS parameter tuning.

This module implements a simple sequential optimization loop where:
1. Benchmark executes
2. Metrics are collected
3. Tuner suggests new parameters
4. Parameters are applied
5. Repeat
"""

import time
import logging
import concurrent.futures
import threading
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import os
import json

from .benchmark import BenchmarkMetrics
from .parameter_manager import (
    ParameterManager,
    is_per_core_parameter,
    get_selected_default_parameters,
    get_new_parameter_names,
    reset_selected_parameters_to_defaults,
    reset_new_parameters_to_system_defaults,
)
from .tuners.base import TunerResponse

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    """Fallback JSON serializer for numpy scalars and other non-JSON types."""
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    return str(obj)


def _is_fatal_llm_http_error(exc: Exception) -> bool:
    """Detect fatal provider/response errors without importing tuner internals."""
    fatal_names = {
        "LLMHTTPStatusError",
        "LLMTimeoutExhaustedError",
        "InvalidTrimmingCandidateError",
    }
    return any(cls.__name__ in fatal_names for cls in type(exc).mro())


class SimpleOptimizer:
    """Single-loop optimizer that runs benchmarks and adjusts parameters."""
    
    def __init__(self, config, benchmark, tuner, trimming_tuner=None):
        """Initialize optimizer.
        
        Args:
            config: Configuration object
            benchmark: Benchmark instance
            tuner: Primary tuner instance
            trimming_tuner: Optional trimming tuner for search space narrowing
        """
        self.config = config
        self.benchmark = benchmark
        self.tuner = tuner
        self.trimming_tuner = trimming_tuner
        self.trimming_cycles = config.trimming_cycles if config.trimming_enabled else 0
        self._trimming_phase_complete = False
        self.param_manager = ParameterManager()
        
        # State
        self.current_parameters = {}
        self.best_parameters = {}
        self.best_reward = float('-inf') if config.optimization_goal == 'maximize' else float('inf')
        self.iteration = 0
        self.history = []
        self.start_time = time.time()
        self._history_saved = False  # Flag to prevent double-saving
        
        # Async tuner state
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.pending_tuner_future = None
        self.tuner_call_time = None
        self.tuner_target_iteration = None
        self.parameters_lock = threading.Lock()
        
        # Latest metrics for pending tuner calls
        self.latest_metrics = None
        self.latest_iteration = None
        
        # Track request timestamps for aggregation interval calculation
        self.last_tuner_request_time = None
        self.current_window_start_time = None

    def _requires_final_llm_freeze_before_stable(self) -> bool:
        """Whether a single-loop LLM must choose one final stable config."""
        return bool(
            getattr(self.config, "tuner_type", None) == "llm"
            and getattr(self.config, "llm_force_final_freeze_before_stable", False)
            and getattr(self.config, "post_tuning_windows", 0) > 0
        )

    def _attach_final_freeze_timing_to_history(self, timing: Optional[Dict[str, Any]]) -> None:
        if timing is None or not self.history:
            return
        timing_block = self.history[-1].setdefault("tuner_timing", {})
        if not isinstance(timing_block, dict):
            timing_block = {}
            self.history[-1]["tuner_timing"] = timing_block
        timing_block["final_freeze_before_stable"] = timing

    def _get_metrics_from_history(self, entry: Dict[str, Any]) -> BenchmarkMetrics:
        values = entry.get("metrics", {})
        metrics = BenchmarkMetrics(
            throughput=values.get("throughput", 0.0),
            goodput=values.get("goodput", 0.0),
            latency_avg=values.get("latency_avg", 0.0),
            latency_p95=values.get("latency_p95", 0.0),
        )
        metrics.extra_metrics.update({
            key: value for key, value in values.items()
            if key not in {"throughput", "goodput", "latency_avg", "latency_p95"}
        })
        if "system_metrics" in entry:
            metrics.extra_metrics["system_metrics"] = entry["system_metrics"]
        return metrics

    def _run_refreshed_final_freeze_before_stable(self, iteration: int) -> Dict[str, Any]:
        """Ask the single LLM for one final config after the last tuning result."""
        self._clear_pending_tuner_response()
        if not self.history:
            raise RuntimeError("Cannot issue final single-loop freeze without tuning history")

        history_snapshot = [entry.copy() for entry in self.history]
        last_entry = history_snapshot[-1]
        call_iteration = last_entry.get("iteration", 0)
        current_time = time.time()
        aggregation_interval_s = (
            current_time - self.last_tuner_request_time
            if self.last_tuner_request_time is not None
            else float(self.config.window_duration)
        )
        phase_override = (
            "PHASE POLICY: this is the final single-agent refresh after the completed last tuning window. "
            "Use the full tuning history including the latest measurement to choose the one configuration "
            "to freeze for the stable phase. Do not explore; prefer the best safe incumbent or a very "
            "nearby refinement.\n"
        )
        logger.info("Dispatching final single-loop freeze request before stable phase...")
        self.last_tuner_request_time = current_time
        response = self.tuner.suggest_parameters(
            metrics=self._get_metrics_from_history(last_entry),
            current_params=self._get_tunable_parameters(last_entry.get("parameters", self.current_parameters)),
            iteration=call_iteration,
            best_reward=self.best_reward,
            history=history_snapshot,
            baseline_index=0,
            aggregation_interval_s=aggregation_interval_s,
            phase_instruction_override=phase_override,
            final_freeze_request=True,
        )
        timing = self._apply_tuner_parameters(response, call_iteration, duration_s=time.time() - current_time) or {}
        timing["stage"] = "final_freeze_before_stable"
        timing["stable_gate_iteration"] = iteration
        return timing
    
    def _get_active_tuner(self, iteration: int):
        """Return the active tuner for the given iteration.
        
        During the trimming phase (iteration <= trimming_cycles), returns the
        trimming tuner. After that, returns the primary tuner.
        """
        if self.trimming_tuner and iteration <= self.trimming_cycles and not self._trimming_phase_complete:
            return self.trimming_tuner
        return self.tuner
    
    def run(self) -> Dict[str, Any]:
        """Run the optimization loop.
        
        Returns:
            Dictionary with optimization results
        """
        try:
            logger.info("=" * 60)
            logger.info("Starting single-loop OS parameter optimization")
            logger.info(f"Benchmark: {self.config.benchmark}")
            logger.info(f"Tuner: {self.config.tuner_type}")
            if self.trimming_tuner:
                logger.info(f"Trimming phase: {self.trimming_cycles} cycles (model: {getattr(self.trimming_tuner, 'model_name', 'N/A')})")
            logger.info(f"Optimizing for: {self.config.optimization_goal} {self.config.optimization_metric}")
            total_iterations = self.config.max_iterations + self.config.post_tuning_windows
            logger.info(f"Tuning iterations: {self.config.max_iterations}")
            logger.info(f"Post-tuning measurement windows: {self.config.post_tuning_windows}")
            logger.info(f"Total windows: {total_iterations}")
            logger.info(f"Tuning mode: {self.config.tuning_mode}")
            logger.info("=" * 60)
            
            # Pre-execute benchmark setup
            if hasattr(self.benchmark, 'pre_execute'):
                if not self.benchmark.pre_execute():
                    raise RuntimeError("Benchmark pre-execute failed")
            
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
            
            # Apply defaults only for parameters selected by this experiment.
            initial_params = get_selected_default_parameters(experiment_params)
            # Merge with fixed_parameters (fixed_parameters override defaults)
            initial_params.update(self.config.fixed_parameters)
            logger.info(f"Starting with default parameters: {initial_params}")
            if not self.param_manager.set_parameters(initial_params):
                raise RuntimeError("Failed to apply initial parameters")
            self.current_parameters = initial_params.copy()
            
            # Main optimization loop - branch based on tuning_mode
            if self.config.tuning_mode == "outside-of-window":
                return self._run_outside_window_mode()
            else:  # "outside-of-window"
                return self._run_in_window_mode()
        except KeyboardInterrupt:
            logger.info("Optimization interrupted by user")
            return self._finish("interrupted")
        except Exception as e:
            logger.error(f"Error during optimization: {e}", exc_info=True)
            return self._finish("error")

    def _total_iterations(self) -> int:
        """Total benchmark windows including post-tuning measurement windows."""
        return self.config.max_iterations + self.config.post_tuning_windows

    def _clear_pending_tuner_response(self) -> None:
        """Drop any pending tuner response so post-tuning windows remain fixed."""
        if not self.pending_tuner_future:
            return

        if not self.pending_tuner_future.done():
            logger.info("Cancelling pending tuner request before post-tuning phase")
            self.pending_tuner_future.cancel()
        else:
            logger.info("Discarding completed tuner response before post-tuning phase")

        self.pending_tuner_future = None
        self.tuner_call_time = None
        self.tuner_target_iteration = None
    
    def _run_in_window_mode(self) -> Dict[str, Any]:
        """Run optimization loop with outside-of-window tuning (async, tuner called during window execution)."""
        total_iterations = self._total_iterations()
        post_phase_start = self.config.max_iterations + 1

        # Main optimization loop
        for iteration in range(1, total_iterations + 1):
            self.iteration = iteration
            logger.info(f"=== ITERATION {iteration}/{total_iterations} ===")
            tuning_active = iteration <= self.config.max_iterations
            in_post_tuning_phase = not tuning_active

            if iteration == post_phase_start and self.config.post_tuning_windows > 0:
                if self._requires_final_llm_freeze_before_stable():
                    logger.info("Preparing stable phase: waiting for final single-loop freeze response")
                    timing = self._run_refreshed_final_freeze_before_stable(iteration)
                    self._attach_final_freeze_timing_to_history(timing)
                else:
                    self._clear_pending_tuner_response()
                logger.info(
                    "Entering post-tuning measurement phase: "
                    f"{self.config.post_tuning_windows} windows with frozen parameters"
                )
            
            # Check if there's a pending tuner response from previous iteration
            # and apply it if still valid (hasn't moved on to next iteration)
            tuner_timing_info = None
            if tuning_active:
                tuner_timing_info = self._check_and_apply_pending_tuner_response(iteration)
            
            # Execute window (perf and power/CPU metrics always collected)
            # During execution, we'll check for tuner responses periodically
            metrics = self._execute_window_with_tuner_check(
                iteration,
                allow_tuner_apply=tuning_active,
            )
            
            # Check again AFTER window execution - tuner might have responded during execution
            # This handles the case where tuner responds quickly (< window_duration)
            if tuning_active and tuner_timing_info is None:
                tuner_timing_info = self._check_and_apply_pending_tuner_response(iteration)
            
            if not metrics:
                logger.warning(f"No metrics object from iteration {iteration}")
                continue
            
            # Check if the optimization metric is available and valid
            optimization_metric_value = metrics.get_metric(self.config.optimization_metric)
            if optimization_metric_value is None or (isinstance(optimization_metric_value, float) and (optimization_metric_value != optimization_metric_value)):  # NaN check
                logger.warning(f"No valid {self.config.optimization_metric} metric from iteration {iteration} (value: {optimization_metric_value})")
                continue
            
            # Store latest metrics for potential resubmission
            self.latest_metrics = metrics
            self.latest_iteration = iteration
            
            # Calculate reward
            reward = metrics.get_metric(self.config.optimization_metric)
            
            # Log available metrics for debugging
            logger.debug(f"Available metrics: throughput={metrics.throughput}, goodput={metrics.goodput}, "
                        f"latency_avg={metrics.latency_avg}, latency_p95={metrics.latency_p95}, "
                        f"extra_metrics_keys={list(metrics.extra_metrics.keys())}")
            
            # Check if reward is valid (not None and not NaN)
            if reward is None or (isinstance(reward, float) and (reward != reward)):  # NaN check
                logger.warning(f"Invalid reward value for iteration {iteration}: {reward}. "
                             f"Metric '{self.config.optimization_metric}' may not be available in metrics.")
                reward = 0.0  # Use 0.0 as fallback
            
            # Apply multiplicative constraint penalty if constraint is violated
            # (constraint_penalty config field is kept for backward compat but ignored;
            #  a fixed 10x multiplicative factor is used instead to avoid discontinuities
            #  that break surrogate models in Bayesian optimizers)
            is_violated = False
            if self.config.constraint_metric and self.config.constraint_threshold is not None:
                constraint_value = metrics.get_metric(self.config.constraint_metric)
                constraint_direction = getattr(self.config, 'constraint_direction', 'less_than')
                
                # Check constraint violation based on direction
                if constraint_direction == "less_than":
                    is_violated = constraint_value >= self.config.constraint_threshold
                    constraint_desc = f"{constraint_value:.4f} >= {self.config.constraint_threshold:.4f}"
                else:  # greater_than
                    is_violated = constraint_value <= self.config.constraint_threshold
                    constraint_desc = f"{constraint_value:.4f} <= {self.config.constraint_threshold:.4f}"
                
                if is_violated:
                    penalty_factor = 10.0
                    original_reward = reward
                    if self.config.optimization_goal == 'minimize':
                        reward = abs(reward) * penalty_factor if reward != 0 else 1000.0
                    else:
                        reward = reward / penalty_factor if reward != 0 else -1000.0
                    logger.warning(
                        f"Constraint violated ({constraint_direction}): {self.config.constraint_metric}={constraint_desc}. "
                        f"Applied 10x multiplicative penalty: {original_reward:.4f} -> {reward:.4f}"
                    )
            
            logger.info(f"Iteration {iteration} reward: {reward:.4f} (metric: {self.config.optimization_metric}, goal: {self.config.optimization_goal})")
            
            # Update best
            is_better = (
                (self.config.optimization_goal == 'maximize' and reward > self.best_reward) or
                (self.config.optimization_goal == 'minimize' and reward < self.best_reward)
            )
            if is_better:
                old_best = self.best_reward
                self.best_reward = reward
                self.best_parameters = self.current_parameters.copy()
                logger.info(f"New best parameters: {self.best_parameters} with reward {self.best_reward:.4f} (previous best: {old_best:.4f})")
            else:
                logger.debug(f"Reward {reward:.4f} is not better than current best {self.best_reward:.4f}")
            
            # Extract system_metrics from extra_metrics if present
            system_metrics = metrics.extra_metrics.pop("system_metrics", None)
            
            # Add to history
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
                "constraint_violated": is_violated,
                "post_tuning_phase": in_post_tuning_phase,
            }
            
            # Tag trimming phase entries
            if self.trimming_tuner and iteration <= self.trimming_cycles and not self._trimming_phase_complete:
                history_entry["trimming_phase"] = True
            
            # Add system_metrics if available
            if system_metrics:
                history_entry["system_metrics"] = system_metrics
            
            # Add tuner timing info if available
            if tuner_timing_info:
                history_entry["tuner_timing"] = tuner_timing_info
                # Also expose justification at top level for easier access
                if tuner_timing_info.get("justification"):
                    history_entry["llm_justification"] = tuner_timing_info["justification"]
                # Also expose converged at top level for easier access
                if tuner_timing_info.get("converged") is not None:
                    history_entry["converged"] = tuner_timing_info["converged"]
                # Also expose token_metrics at top level for easier access
                if tuner_timing_info.get("token_metrics"):
                    history_entry["token_metrics"] = tuner_timing_info["token_metrics"]
            
            # Start async tuner call for next iteration
            # Behavior depends on continuous_apply setting:
            # - If continuous_apply=True: Always submit new request (previous will be handled by resubmit)
            # - If continuous_apply=False: Only submit if previous one completed (once per window)
            tuner_request_skipped = False
            if tuning_active and iteration < self.config.max_iterations:
                # Check if trimming phase just ended
                if (self.trimming_tuner and iteration == self.trimming_cycles
                        and not self._trimming_phase_complete):
                    self._apply_trimmed_ranges()
                
                if self.config.continuous_apply:
                    # Continuous mode: always submit new request
                    # If previous is still pending, it will be cancelled
                    self._start_async_tuner_call(metrics, iteration)
                else:
                    # One-per-window mode: only submit if previous one completed
                    if self.pending_tuner_future is None or self.pending_tuner_future.done():
                        self._start_async_tuner_call(metrics, iteration)
                    else:
                        # Previous request still pending - don't submit new one
                        # Store latest metrics for when previous completes (will be used in next window)
                        logger.info(f"Previous tuner call still pending (for iteration {self.tuner_target_iteration}). "
                                   f"Not submitting new request (continuous_apply=False). Will wait for it to complete.")
                        tuner_request_skipped = True
                        # Latest metrics already stored above
            
            # Add skipped flag to history entry if request was skipped
            if tuner_request_skipped:
                history_entry["tuner_request_skipped"] = True
                history_entry["tuner_skip_reason"] = f"Previous tuner call for iteration {self.tuner_target_iteration} still pending (model didn't respond on time)"
            
            self.history.append(history_entry)
            
            # Wait for any pending tuner response before finishing
            if tuning_active and self.pending_tuner_future:
                logger.info("Waiting for final tuner response to complete...")
                try:
                    self.pending_tuner_future.result(timeout=60)
                except Exception as e:
                    if _is_fatal_llm_http_error(e):
                        raise
                    logger.warning(f"Final tuner call failed or timed out: {e}")
        
        return self._finish("complete")
    
    def _run_outside_window_mode(self) -> Dict[str, Any]:
        """Run optimization loop with outside-of-window tuning (synchronous, tuner called after window completes)."""
        try:
            total_iterations = self._total_iterations()
            post_phase_start = self.config.max_iterations + 1

            # Main optimization loop
            for iteration in range(1, total_iterations + 1):
                self.iteration = iteration
                logger.info(f"=== ITERATION {iteration}/{total_iterations} ===")
                tuning_active = iteration <= self.config.max_iterations
                in_post_tuning_phase = not tuning_active

                if iteration == post_phase_start and self.config.post_tuning_windows > 0:
                    if self._requires_final_llm_freeze_before_stable():
                        logger.info("Preparing stable phase: waiting for final single-loop freeze response")
                        timing = self._run_refreshed_final_freeze_before_stable(iteration)
                        self._attach_final_freeze_timing_to_history(timing)
                    else:
                        self._clear_pending_tuner_response()
                    logger.info(
                        "Entering post-tuning measurement phase: "
                        f"{self.config.post_tuning_windows} windows with frozen parameters"
                    )
                
                # Execute window (synchronously, no tuner checks during execution)
                if self.benchmark and hasattr(self.benchmark, 'update_workload'):
                    self.benchmark.update_workload(iteration)
                
                metrics = self.benchmark.execute_window(
                    window_number=iteration,
                    duration=self.config.window_duration
                )
                
                if not metrics:
                    logger.warning(f"No metrics object from iteration {iteration}")
                    # Still need to get tuner response for next iteration
                    if tuning_active and iteration < self.config.max_iterations:
                        tuner_response, duration = self._get_tuner_response_sync(metrics, iteration)
                        if tuner_response:
                            self._apply_tuner_parameters(tuner_response, iteration, duration_s=duration)
                    continue
                
                # Check if the optimization metric is available and valid
                optimization_metric_value = metrics.get_metric(self.config.optimization_metric)
                if optimization_metric_value is None or (isinstance(optimization_metric_value, float) and (optimization_metric_value != optimization_metric_value)):  # NaN check
                    logger.warning(f"No valid {self.config.optimization_metric} metric from iteration {iteration} (value: {optimization_metric_value})")
                    # Still need to get tuner response for next iteration
                    if tuning_active and iteration < self.config.max_iterations:
                        tuner_response, duration = self._get_tuner_response_sync(metrics, iteration)
                        if tuner_response:
                            self._apply_tuner_parameters(tuner_response, iteration, duration_s=duration)
                    continue
                
                # Calculate reward
                reward = metrics.get_metric(self.config.optimization_metric)
                
                # Log available metrics for debugging
                logger.debug(f"Available metrics: throughput={metrics.throughput}, goodput={metrics.goodput}, "
                            f"latency_avg={metrics.latency_avg}, latency_p95={metrics.latency_p95}, "
                            f"extra_metrics_keys={list(metrics.extra_metrics.keys())}")
                
                # Check if reward is valid (not None and not NaN)
                if reward is None or (isinstance(reward, float) and (reward != reward)):  # NaN check
                    logger.warning(f"Invalid reward value for iteration {iteration}: {reward}. "
                                 f"Metric '{self.config.optimization_metric}' may not be available in metrics.")
                    reward = 0.0  # Use 0.0 as fallback
                
                # Apply multiplicative constraint penalty if constraint is violated
                # (constraint_penalty config field is kept for backward compat but ignored;
                #  a fixed 10x multiplicative factor is used instead to avoid discontinuities
                #  that break surrogate models in Bayesian optimizers)
                is_violated = False
                if self.config.constraint_metric and self.config.constraint_threshold is not None:
                    constraint_value = metrics.get_metric(self.config.constraint_metric)
                    constraint_direction = getattr(self.config, 'constraint_direction', 'less_than')
                    
                    # Check constraint violation based on direction
                    if constraint_direction == "less_than":
                        is_violated = constraint_value >= self.config.constraint_threshold
                        constraint_desc = f"{constraint_value:.4f} >= {self.config.constraint_threshold:.4f}"
                    else:  # greater_than
                        is_violated = constraint_value <= self.config.constraint_threshold
                        constraint_desc = f"{constraint_value:.4f} <= {self.config.constraint_threshold:.4f}"
                    
                    if is_violated:
                        # Apply multiplicative penalty for non-LLM tuners;
                        # LLM tuners handle constraints semantically via prompt
                        if self.tuner.__class__.__name__ != 'LLMTuner':
                            penalty_factor = 10.0
                            original_reward = reward
                            if self.config.optimization_goal == 'minimize':
                                reward = abs(reward) * penalty_factor if reward != 0 else 1000.0
                            else:
                                reward = reward / penalty_factor if reward != 0 else -1000.0
                            logger.warning(
                                f"Constraint violated ({constraint_direction}): {self.config.constraint_metric}={constraint_desc}. "
                                f"Applied 10x multiplicative penalty: {original_reward:.4f} -> {reward:.4f}"
                            )
                        else:
                            logger.warning(
                                f"Constraint violated ({constraint_direction}): {self.config.constraint_metric}={constraint_desc}. "
                                f"(LLM tuner — handled semantically via prompt)"
                            )
                
                logger.info(f"Iteration {iteration} reward: {reward:.4f} (metric: {self.config.optimization_metric}, goal: {self.config.optimization_goal})")
                
                # Update best
                is_better = (
                    (self.config.optimization_goal == 'maximize' and reward > self.best_reward) or
                    (self.config.optimization_goal == 'minimize' and reward < self.best_reward)
                )
                if is_better:
                    old_best = self.best_reward
                    self.best_reward = reward
                    self.best_parameters = self.current_parameters.copy()
                    logger.info(f"New best parameters: {self.best_parameters} with reward {self.best_reward:.4f} (previous best: {old_best:.4f})")
                else:
                    logger.debug(f"Reward {reward:.4f} is not better than current best {self.best_reward:.4f}")
                
                # Extract system_metrics from extra_metrics if present
                system_metrics = metrics.extra_metrics.pop("system_metrics", None)
                
                # Add to history
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
                    "constraint_violated": is_violated,
                    "post_tuning_phase": in_post_tuning_phase,
                }
                
                # Tag trimming phase entries
                if self.trimming_tuner and iteration <= self.trimming_cycles and not self._trimming_phase_complete:
                    history_entry["trimming_phase"] = True
                
                # Add system_metrics if available
                if system_metrics:
                    history_entry["system_metrics"] = system_metrics
                
                self.history.append(history_entry)
                
                # Get tuner response for next iteration (synchronously)
                # Only do this if we have more iterations to go
                if tuning_active and iteration < self.config.max_iterations:
                    tuner_response, duration = self._get_tuner_response_sync(metrics, iteration)
                    if tuner_response:
                        tuner_timing_info = self._apply_tuner_parameters(tuner_response, iteration, duration_s=duration)
                        # Add tuner timing info to history entry
                        if tuner_timing_info:
                            history_entry["tuner_timing"] = tuner_timing_info
                            # Also expose justification at top level for easier access
                            if tuner_timing_info.get("justification"):
                                history_entry["llm_justification"] = tuner_timing_info["justification"]
                            # Also expose converged at top level for easier access
                            if tuner_timing_info.get("converged") is not None:
                                history_entry["converged"] = tuner_timing_info["converged"]
                            # Also expose token_metrics at top level for easier access
                            if tuner_timing_info.get("token_metrics"):
                                history_entry["token_metrics"] = tuner_timing_info["token_metrics"]
                    
                    # Check if trimming phase just ended
                    if (self.trimming_tuner and iteration == self.trimming_cycles
                            and not self._trimming_phase_complete):
                        self._apply_trimmed_ranges()
            
            return self._finish("complete")
            
        except KeyboardInterrupt:
            logger.info("Optimization interrupted by user")
            return self._finish("interrupted")
        except Exception as e:
            logger.error(f"Error during optimization: {e}", exc_info=True)
            return self._finish("error")
    
    def _execute_window_with_tuner_check(self, iteration: int, allow_tuner_apply: bool = True) -> BenchmarkMetrics:
        """Execute window and check for tuner responses periodically during execution.
        
        This method runs the benchmark window and periodically checks for tuner responses.
        If a response arrives during execution, parameters are applied immediately.
        In continuous_apply mode, new tuner requests are submitted immediately when
        the previous response arrives.
        
        Args:
            iteration: Current iteration number
            allow_tuner_apply: If False, ignore pending tuner responses and keep parameters fixed.
            
        Returns:
            BenchmarkMetrics from window execution
        """
        window_duration = self.config.window_duration
        check_interval = 0.1  # Check every 0.1 seconds
        
        # Start window execution in a separate thread so we can check for responses
        import threading
        metrics_result = [None]
        exception_result = [None]
        
        def run_window():
            try:
                metrics_result[0] = self.benchmark.execute_window(
                    window_number=iteration,
                    duration=window_duration
                )
            except Exception as e:
                exception_result[0] = e
        
        window_thread = threading.Thread(target=run_window, daemon=True)
        window_thread.start()
        
        # Track window start time for aggregation interval calculation
        window_start = time.time()
        self.current_window_start_time = window_start
        
        # Periodically check for tuner responses during window execution
        while window_thread.is_alive():
            if allow_tuner_apply:
                # Check if tuner responded
                if self.pending_tuner_future and self.pending_tuner_future.done():
                    response_time = time.time()
                    
                    # Response arrived! Apply it immediately
                    self._check_and_apply_pending_tuner_response(iteration)
                    
                    # In continuous_apply mode, immediately start next tuner request
                    if self.config.continuous_apply and self.latest_metrics is not None:
                        # Calculate aggregation interval based on time since last request
                        agg_interval = response_time - self.tuner_call_time if self.tuner_call_time else None
                        logger.info(f"continuous_apply=True: Immediately starting next tuner request (agg_interval={agg_interval:.2f}s)")
                        self._start_async_tuner_call(self.latest_metrics, self.latest_iteration, aggregation_interval_s=agg_interval)
            
            # Wait a bit before next check
            time.sleep(check_interval)
            
            # Break if window thread finished
            if not window_thread.is_alive():
                break
        
        # Wait for window thread to complete
        window_thread.join(timeout=window_duration + 10)
        
        if exception_result[0]:
            raise exception_result[0]
        
        if metrics_result[0] is None:
            logger.error(f"Window execution did not complete for iteration {iteration}")
            return BenchmarkMetrics()
        
        return metrics_result[0]
    
    def _start_async_tuner_call(self, metrics: BenchmarkMetrics, iteration: int, aggregation_interval_s: Optional[float] = None) -> None:
        """Start an asynchronous tuner call.
        
        Args:
            metrics: Metrics from the current iteration
            iteration: Current iteration number (tuner is called for iteration+1)
            aggregation_interval_s: Optional aggregation interval in seconds
        """
        # Cancel any pending tuner call if it's still running
        # Only cancel in continuous_apply mode (in one-per-window mode, we shouldn't reach here with pending)
        if self.pending_tuner_future and not self.pending_tuner_future.done():
            if self.config.continuous_apply:
                logger.info(f"Cancelling pending tuner call for iteration {self.tuner_target_iteration} (continuous_apply=True)")
                self.pending_tuner_future.cancel()
            else:
                logger.warning(f"Attempted to start new tuner call while previous still pending (continuous_apply=False). This shouldn't happen.")
                return
        
        # Start new async tuner call
        current_time = time.time()
        self.tuner_call_time = current_time
        self.tuner_target_iteration = iteration + 1
        
        # Calculate aggregation interval if not provided
        if aggregation_interval_s is None:
            if self.last_tuner_request_time is not None:
                # Time since last request (for continuous_apply=True)
                aggregation_interval_s = current_time - self.last_tuner_request_time
            elif self.current_window_start_time is not None:
                # Time since window start (fallback)
                aggregation_interval_s = current_time - self.current_window_start_time
            else:
                # Use window_duration as default (first request)
                aggregation_interval_s = float(self.config.window_duration)
        
        # Update last request time
        self.last_tuner_request_time = current_time
        
        logger.info(f"Starting async tuner call for iteration {self.tuner_target_iteration} (agg_interval={aggregation_interval_s:.2f}s)")
        
        # Filter to only tunable parameters (those in parameters_to_tune)
        tunable_params = self._get_tunable_parameters(self.current_parameters)
        fixed_params = self._get_fixed_parameters(self.current_parameters)
        
        logger.info(f"Passing to {self.config.tuner_type} tuner - tunable_parameters: {json.dumps(tunable_params, default=_json_default)}, "
                   f"fixed_parameters: {json.dumps(fixed_params, default=_json_default)}, "
                   f"best_reward: {self.best_reward:.4f}, optimization_metric: {self.config.optimization_metric}")
        
        self.pending_tuner_future = self.executor.submit(
            self._get_active_tuner(iteration + 1).suggest_parameters,
            metrics=metrics,
            current_params=tunable_params,
            iteration=iteration,
            best_reward=self.best_reward,
            aggregation_interval_s=aggregation_interval_s
        )
    
    def _resubmit_tuner_with_latest_metrics(self) -> None:
        """Resubmit tuner call with latest metrics if previous one completed.
        
        This is called when a pending tuner response arrives and we want to
        submit a new request with the latest metrics.
        
        Only resubmits if continuous_apply is enabled in config.
        """
        # Only resubmit automatically if continuous_apply is enabled
        if not self.config.continuous_apply:
            logger.debug("continuous_apply is False, not automatically resubmitting tuner request")
            return
        
        if self.latest_metrics is None or self.latest_iteration is None:
            return
        
        # Only resubmit if no pending call
        if self.pending_tuner_future is None or self.pending_tuner_future.done():
            logger.info(f"Resubmitting tuner call with latest metrics from iteration {self.latest_iteration} (continuous_apply=True)")
            self._start_async_tuner_call(self.latest_metrics, self.latest_iteration)
    
    def _check_and_apply_pending_tuner_response(self, current_iteration: int) -> Optional[Dict[str, Any]]:
        """Check for pending tuner response and apply it if valid.
        
        This method checks if a tuner response is available and applies it immediately.
        
        Args:
            current_iteration: Current iteration number
            
        Returns:
            Dictionary with tuner timing information if parameters were applied, None otherwise
        """
        if not self.pending_tuner_future:
            return None
        
        # Check if this response is for the current iteration or previous iteration
        # (Previous iteration responses are accepted if they came during execution)
        effective_iteration = current_iteration
        if self.tuner_target_iteration != current_iteration:
            if self.tuner_target_iteration == current_iteration - 1:
                # Response is for previous iteration - accept it since it came during/after previous window execution
                # Apply it to current iteration
                effective_iteration = self.tuner_target_iteration
                logger.info(f"Tuner response is for iteration {self.tuner_target_iteration}, but we're at {current_iteration}. "
                           f"Accepting it since it came during/after previous window execution.")
            elif self.tuner_target_iteration < current_iteration - 1:
                # Response is too old - ignore it
                logger.warning(f"Tuner response is for iteration {self.tuner_target_iteration}, but we're at {current_iteration}. Too old, ignoring.")
                self.pending_tuner_future = None
                return None
            else:
                # Response is for a future iteration - shouldn't happen, but ignore
                logger.warning(f"Tuner response is for future iteration {self.tuner_target_iteration}, but we're at {current_iteration}. Ignoring.")
                self.pending_tuner_future = None
                return None
        
        # Check if tuner has responded (non-blocking check)
        if not self.pending_tuner_future.done():
            return None
        
        # Get the response
        try:
            tuner_response = self.pending_tuner_future.result()
            response_time = time.time()
            duration = response_time - self.tuner_call_time if self.tuner_call_time else 0
            
            logger.info(f"Tuner responded for iteration {effective_iteration} (took {duration:.2f}s)")
            
            # Clear the pending future
            self.pending_tuner_future = None
            target_iter = self.tuner_target_iteration
            call_time = self.tuner_call_time
            self.tuner_call_time = None
            self.tuner_target_iteration = None
            
            if tuner_response.parameters:
                # Merge with fixed parameters
                # Preserve omitted values, including parameters eliminated by the
                # trimming model and therefore absent from later candidates.
                new_params = self.current_parameters.copy()
                new_params.update(self.config.fixed_parameters)
                
                # Merge tuner parameters, handling per-core parameters
                for param_name, param_value in tuner_response.parameters.items():
                    # Check if this is a per-core parameter
                    from .parameter_manager import is_per_core_parameter
                    bind_cores = is_per_core_parameter(param_name) and self.config.pin_to_cores
                    # Do not rebind NIC IRQs while applying network parameters.
                    # IRQ migration can dominate short in-window measurements.
                    if param_name in {"busy_poll", "napi_busy_poll", "busy_read", "netdev_budget", "netdev_budget_usecs"}:
                        bind_cores = False
                    if bind_cores:
                        # Merge with pin_to_cores
                        new_params[param_name] = {
                            "value": param_value,
                            "cores": self.config.pin_to_cores
                        }
                    else:
                        # Regular parameter
                        new_params[param_name] = param_value
                
                # Apply new parameters IMMEDIATELY
                with self.parameters_lock:
                    if self.param_manager.set_parameters(new_params):
                        self.current_parameters = new_params
                        logger.info(f"Applied tuner parameters IMMEDIATELY (originated from iteration {effective_iteration}, applied to iteration {current_iteration}): {json.dumps(tuner_response.parameters, default=_json_default)}")
                        if tuner_response.justification:
                            logger.info(f"Tuner justification: {tuner_response.justification}")
                        
                # Return timing information
                        timing_info = {
                            "tuner_call_time": call_time,
                            "tuner_response_time": response_time,
                            "tuner_duration": duration,
                            "tuner_response_time_ms": duration * 1000,
                            "parameters_applied": True,
                            "proposed_parameters": tuner_response.parameters,
                            "required_parameters": getattr(tuner_response, "required_parameters", None),
                            "target_iteration": effective_iteration,
                            "applied_to_iteration": current_iteration,
                            "justification": tuner_response.justification,
                            "converged": tuner_response.converged,
                            "token_metrics": tuner_response.token_metrics
                        }
                        
                        # After applying, if we have latest metrics, resubmit with them
                        # This handles the case where previous request was late and we need to catch up
                        self._resubmit_tuner_with_latest_metrics()
                        
                        return timing_info
                    else:
                        logger.error("Failed to apply new parameters")
                        # Still return timing info even if apply failed
                        return {
                            "tuner_call_time": call_time,
                            "tuner_response_time": response_time,
                            "tuner_duration": duration,
                            "tuner_response_time_ms": duration * 1000,
                            "parameters_applied": False,
                            "proposed_parameters": tuner_response.parameters,
                            "required_parameters": getattr(tuner_response, "required_parameters", None),
                            "apply_failed": True,
                            "target_iteration": effective_iteration,
                            "applied_to_iteration": current_iteration,
                            "justification": tuner_response.justification,
                            "converged": tuner_response.converged,
                            "token_metrics": tuner_response.token_metrics
                        }
            else:
                logger.info("Tuner returned no parameter changes")
                # Return timing info even when no parameters changed
                timing_info = {
                    "tuner_call_time": call_time,
                    "tuner_response_time": response_time,
                    "tuner_duration": duration,
                    "tuner_response_time_ms": duration * 1000,
                    "parameters_applied": False,
                    "proposed_parameters": tuner_response.parameters if tuner_response else {},
                    "required_parameters": getattr(tuner_response, "required_parameters", None),
                    "target_iteration": effective_iteration,
                    "applied_to_iteration": current_iteration,
                    "justification": tuner_response.justification if tuner_response else None,
                    "converged": tuner_response.converged if tuner_response else None,
                    "token_metrics": tuner_response.token_metrics if tuner_response else None
                }
                # Even if no parameters, resubmit with latest metrics if continuous_apply is enabled
                if self.config.continuous_apply:
                    self._resubmit_tuner_with_latest_metrics()
                return timing_info
            
        except Exception as e:
            if _is_fatal_llm_http_error(e):
                raise
            logger.error(f"Error getting tuner response: {e}")
            self.pending_tuner_future = None
            self.tuner_call_time = None
            self.tuner_target_iteration = None
        
        return None
    
    def _get_tuner_response_sync(self, metrics: BenchmarkMetrics, iteration: int, aggregation_interval_s: Optional[float] = None) -> Tuple[Optional['TunerResponse'], float]:
        """Get tuner response synchronously after window execution.
        
        This method calls the tuner and waits for the response before returning.
        
        Args:
            metrics: Metrics from the current iteration
            iteration: Current iteration number
            aggregation_interval_s: Optional aggregation interval in seconds
            
        Returns:
            TunerResponse if successful, None otherwise
        """
        logger.info(f"Requesting tuner response for iteration {iteration + 1}...")
        
        # Filter to only tunable parameters (those in parameters_to_tune)
        tunable_params = self._get_tunable_parameters(self.current_parameters)
        fixed_params = self._get_fixed_parameters(self.current_parameters)
        
        logger.info(f"Passing to {self.config.tuner_type} tuner - tunable_parameters: {json.dumps(tunable_params, default=_json_default)}, "
                   f"fixed_parameters: {json.dumps(fixed_params, default=_json_default)}, "
                   f"best_reward: {self.best_reward:.4f}, optimization_metric: {self.config.optimization_metric}")
        
        current_time = time.time()
        tuner_call_time = current_time
        
        # Calculate aggregation interval if not provided
        # For outside-of-window mode (continuous_apply=False), use window_duration
        if aggregation_interval_s is None:
            if not self.config.continuous_apply:
                # In outside-of-window mode, aggregation is over the full window
                aggregation_interval_s = float(self.config.window_duration)
            elif self.last_tuner_request_time is not None:
                # Time since last request
                aggregation_interval_s = current_time - self.last_tuner_request_time
            else:
                # First request, use window_duration
                aggregation_interval_s = float(self.config.window_duration)
        
        # Update last request time
        self.last_tuner_request_time = current_time
        
        try:
            tuner_response = self._get_active_tuner(iteration + 1).suggest_parameters(
                metrics=metrics,
                current_params=tunable_params,
                iteration=iteration,
                best_reward=self.best_reward,
                aggregation_interval_s=aggregation_interval_s
            )
            
            response_time = time.time()
            duration = response_time - tuner_call_time
            
            logger.info(f"Tuner responded (took {duration:.2f}s)")
            return tuner_response, duration
            
        except Exception as e:
            if _is_fatal_llm_http_error(e):
                raise
            logger.error(f"Error getting tuner response: {e}", exc_info=True)
            return None, 0.0
    
    def _apply_trimmed_ranges(self) -> None:
        """Finalize trimming phase: update parameter ranges and re-create primary tuner.
        
        Called after the last trimming cycle completes. Gets the effective ranges
        from the trimming tuner, updates config.parameter_ranges, and re-creates
        the primary tuner with the new ranges. Eliminated parameters are moved
        to fixed_parameters.
        """
        if self._trimming_phase_complete:
            return
        
        self._trimming_phase_complete = True
        
        # Get effective ranges from trimming tuner
        effective_ranges = self.trimming_tuner.get_effective_ranges()
        eliminated_params = self.trimming_tuner.get_eliminated_params()
        
        # Log the transition
        logger.info("=" * 60)
        logger.info("TRIMMING PHASE COMPLETE — Transitioning to primary tuner")
        logger.info("=" * 60)
        
        # Log trimming summary
        if hasattr(self.trimming_tuner, 'get_trimming_summary'):
            logger.info(self.trimming_tuner.get_trimming_summary())
        
        # Log range comparison
        original_ranges = self.config.parameter_ranges
        changes = 0
        for param_name in effective_ranges:
            old = original_ranges.get(param_name)
            new = effective_ranges[param_name]
            if old != new:
                changes += 1
                logger.info(f"  Range changed [{param_name}]: {old} -> {new}")
        
        if changes == 0:
            logger.info("  No parameter ranges were changed during trimming")
        else:
            logger.info(f"  Total: {changes} parameter range(s) adjusted")
        
        # Handle eliminated parameters
        if eliminated_params:
            logger.info(f"  Eliminated {len(eliminated_params)} parameter(s):")
            if not hasattr(self.config, 'fixed_parameters') or self.config.fixed_parameters is None:
                self.config.fixed_parameters = {}
            for param_name, fixed_value in eliminated_params.items():
                logger.info(f"    {param_name} = {fixed_value} (moved to fixed_parameters)")
                # Move to fixed_parameters
                self.config.fixed_parameters[param_name] = fixed_value
                # Remove from parameters_to_tune if present
                if hasattr(self.config, 'parameters_to_tune') and self.config.parameters_to_tune:
                    if param_name in self.config.parameters_to_tune:
                        self.config.parameters_to_tune.remove(param_name)
                # Remove from parameter_ranges if still present
                if param_name in effective_ranges:
                    del effective_ranges[param_name]
        
        # Update config with effective ranges
        self.config.parameter_ranges = effective_ranges
        
        # Re-create the primary tuner with updated config
        # Import here to avoid circular imports
        from optimizer.main_helpers import create_tuner_from_config
        try:
            self.tuner = create_tuner_from_config(self.config)
            logger.info(f"Primary tuner re-created: {self.config.tuner_type}")
        except Exception as e:
            logger.error(f"Failed to re-create primary tuner: {e}. Keeping original tuner.")
        
        logger.info("=" * 60)
    
    def _apply_tuner_parameters(self, tuner_response: 'TunerResponse', iteration: int, duration_s: float = 0.0) -> Optional[Dict[str, Any]]:
        """Apply tuner parameters and return timing information.
        
        Args:
            tuner_response: Response from tuner
            iteration: Current iteration number (parameters are for next iteration)
            
        Returns:
            Dictionary with timing information (always returned, even if no parameters changed)
        """
        if not tuner_response or (not tuner_response.parameters and not getattr(tuner_response, 'commands', None)):
            logger.info("Tuner returned no parameter changes and no commands")
            # Still return timing info even when no parameters changed
            timing_info = {
                "parameters_applied": False,
                "proposed_parameters": tuner_response.parameters if tuner_response else {},
                "required_parameters": getattr(tuner_response, "required_parameters", None),
                "target_iteration": iteration + 1,
                "justification": tuner_response.justification if tuner_response else None,
                "converged": tuner_response.converged if tuner_response else None,
                "tuner_duration": duration_s,
                "tuner_response_time_ms": duration_s * 1000
            }
            return timing_info
        
        # Only apply tunable parameters that the tuner suggests
        # DO NOT apply fixed parameters - they were set once at the start and should never change
        # Note: wakeup_granularity_ns will be auto-synced when min_granularity_ns is set
        
        command_results = []
        if tuner_response.commands:
            logger.info(f"Executing {len(tuner_response.commands)} commands from tuner...")
            import subprocess
            
            for cmd in tuner_response.commands:
                # Auto-prepend sudo if not present
                if not cmd.strip().startswith("sudo"):
                    cmd = f"sudo {cmd}"
                
                logger.info(f"Executing command: {cmd}")
                try:
                    # Run command
                    # We run as root presumably, or rely on sudo in command if needed?
                    # The prompt instruction says "You are a Linux performance engineering expert with root access."
                    # But the user might not be running as root. 
                    # Ideally we should use sudo if not root, or assume the user runs the script with sudo.
                    # Or rely on the LLM to include 'sudo'.
                    # Let's run it as is.
                    
                    # Security note: This allows arbitrary command execution.
                    result = subprocess.run(
                        cmd, 
                        shell=True, 
                        capture_output=True, 
                        text=True,
                        timeout=30 # Safety timeout
                    )
                    
                    cmd_res = {
                        "command": cmd,
                        "returncode": result.returncode,
                        "stdout": result.stdout[:1000], # Truncate for history
                        "stderr": result.stderr[:1000]
                    }
                    command_results.append(cmd_res)
                    
                    if result.returncode != 0:
                        logger.warning(f"Command failed (rc={result.returncode}): {cmd}\nstderr: {result.stderr}")
                    else:
                        logger.info(f"Command success: {cmd}")
                        
                except Exception as e:
                    logger.error(f"Error executing command '{cmd}': {e}")
                    command_results.append({
                        "command": cmd,
                        "returncode": -1,
                        "stderr": str(e),
                        "stdout": ""
                    })

        # Feed command results back to the tuner so it can include them in history
        if command_results and hasattr(self.tuner, '_last_command_results'):
            self.tuner._last_command_results = command_results

        # Return timing info (now including command results)
        timing_info = {
            "parameters_applied": bool(tuner_response.parameters), # True if params were formatted
            "proposed_parameters": tuner_response.parameters,
            "required_parameters": getattr(tuner_response, "required_parameters", None),
            "target_iteration": iteration + 1,
            "justification": tuner_response.justification if tuner_response else None,
            "converged": tuner_response.converged if tuner_response else None,
            "tuner_duration": duration_s,
            "tuner_response_time_ms": duration_s * 1000,
            "command_results": command_results, # Include command results
            "token_metrics": tuner_response.token_metrics
        }

        tunable_params_to_apply = {}
        
        # Process tuner parameters (only tunable parameters are in tuner_response.parameters)
        for param_name, param_value in tuner_response.parameters.items():
            # Check if this is a per-core parameter
            from .parameter_manager import is_per_core_parameter
            bind_cores = is_per_core_parameter(param_name) and self.config.pin_to_cores
            # Do not rebind NIC IRQs while applying network parameters.
            # IRQ migration can dominate short in-window measurements.
            if param_name in {"busy_poll", "napi_busy_poll", "busy_read", "netdev_budget", "netdev_budget_usecs"}:
                bind_cores = False
            if bind_cores:
                # Merge with pin_to_cores
                tunable_params_to_apply[param_name] = {
                    "value": param_value,
                    "cores": self.config.pin_to_cores
                }
            else:
                # Regular parameter
                tunable_params_to_apply[param_name] = param_value
        
        # Apply only the tunable parameters (not fixed ones)
        # wakeup_granularity_ns will be auto-synced if min_granularity_ns is being set
        with self.parameters_lock:
            if self.param_manager.set_parameters(tunable_params_to_apply):
                # Update current_parameters to reflect the changes
                # Preserve fixed parameters, only update tunable ones
                for param_name, param_value in tunable_params_to_apply.items():
                    if isinstance(param_value, dict) and "value" in param_value:
                        self.current_parameters[param_name] = param_value["value"]
                    else:
                        self.current_parameters[param_name] = param_value
                
                # If min_granularity_ns was set, wakeup_granularity_ns was auto-synced
                # Update current_parameters to reflect the synced value
                if "min_granularity_ns" in tunable_params_to_apply:
                    min_gran_value = tunable_params_to_apply["min_granularity_ns"]
                    if isinstance(min_gran_value, dict) and "value" in min_gran_value:
                        min_gran_value = min_gran_value["value"]
                    self.current_parameters["wakeup_granularity_ns"] = min_gran_value
                logger.info(
                    f"Applied {self.config.tuner_type} tuner parameters for iteration {iteration + 1}: "
                    f"{json.dumps(tuner_response.parameters, default=_json_default)}"
                )
                logger.info(
                    "Full parameter set after applying tuner parameters: "
                    f"{json.dumps(self.current_parameters, default=_json_default)}"
                )
                if tuner_response.justification:
                    logger.info(f"Tuner justification: {tuner_response.justification}")
                
                # Return timing information
                timing_info = {
                    "parameters_applied": True,
                    "parameters_applied_timestamp": time.time(),
                    "proposed_parameters": tuner_response.parameters,
                    "required_parameters": getattr(tuner_response, "required_parameters", None),
                    "target_iteration": iteration + 1,
                    "justification": tuner_response.justification,
                    "converged": tuner_response.converged,
                    "tuner_duration": duration_s,
                    "tuner_response_time_ms": duration_s * 1000,
                    "command_results": command_results,
                    "token_metrics": tuner_response.token_metrics
                }
                
                return timing_info
            else:
                logger.error("Failed to apply new parameters")
                return {
                    "parameters_applied": False,
                    "apply_failed": True,
                    "proposed_parameters": tuner_response.parameters,
                    "required_parameters": getattr(tuner_response, "required_parameters", None),
                    "target_iteration": iteration + 1,
                    "justification": tuner_response.justification,
                    "converged": tuner_response.converged,
                    "tuner_duration": duration_s,
                    "tuner_response_time_ms": duration_s * 1000,
                    "command_results": command_results if command_results else None,
                    "token_metrics": tuner_response.token_metrics
                }
    
    def _finish(self, reason: str) -> Dict[str, Any]:
        """Finish optimization and save results.
        
        This method ensures history is saved even if cleanup fails.
        
        Args:
            reason: Reason for termination
            
        Returns:
            Results dictionary
        """
        total_time = time.time() - self.start_time
        
        # Save history FIRST before any cleanup (to ensure it's saved even if cleanup fails)
        # Only save if not already saved (prevents double-saving from cleanup handlers)
        history_file = ""
        if not self._history_saved:
            try:
                history_file = self._save_history(reason)
                if history_file:
                    logger.info(f"History saved to {history_file}")
            except Exception as e:
                logger.error(f"CRITICAL: Failed to save history: {e}", exc_info=True)
                # Try one more time with a simpler approach
                try:
                    import json
                    simple_file = os.path.join(
                        self.config.results_dir,
                        f"optimization_history_emergency_{int(time.time())}.json"
                    )
                    os.makedirs(os.path.dirname(simple_file), exist_ok=True)
                    with open(simple_file, 'w') as f:
                        json.dump({
                            "history": self.history,
                            "reason": reason,
                            "iterations": self.iteration,
                            "best_parameters": self.best_parameters,
                            "best_reward": self.best_reward
                        }, f, indent=2, default=_json_default)
                    logger.info(f"Emergency history save to {simple_file}")
                    history_file = simple_file
                except Exception as e2:
                    logger.error(f"CRITICAL: Emergency save also failed: {e2}")
        
        # Cleanup benchmark first (kill running processes)
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
                logger.warning(f"Error during benchmark cleanup: {e}")
        
        # Shutdown thread pool
        if self.config.tuning_mode == "outside-of-window":
            try:
                logger.info("Shutting down executor threads...")
                self.executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:
                logger.warning(f"Error shutting down executor: {e}")
        
        # Reset parameters to defaults (this can be interrupted)
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
            
            # For new parameters, reset only those that were part of this experiment.
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
            logger.warning(f"Error resetting parameters to defaults: {e}")
        
        results = {
            "best_parameters": self.best_parameters,
            "best_reward": self.best_reward,
            "iterations": self.iteration,
            "total_time": total_time,
            "terminated_reason": reason,
            "history_file": history_file
        }
        
        logger.info(f"Optimization complete: {self.iteration} iterations in {total_time:.2f}s")
        logger.info(f"Best parameters: {self.best_parameters}")
        logger.info(f"Best reward: {self.best_reward}")
        
        return results
    
    def _get_tunable_parameters(self, all_parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Filter parameters to only include tunable ones (those in parameters_to_tune).
        
        Args:
            all_parameters: All current parameters
            
        Returns:
            Dictionary with only tunable parameters
        """
        if self.config.parameters_to_tune is None:
            # If parameters_to_tune is None, all parameters in parameter_ranges are tunable
            # Return all parameters that are in parameter_ranges
            return {
                k: v for k, v in all_parameters.items()
                if k in self.config.parameter_ranges
            }
        else:
            # Only return parameters that are in parameters_to_tune
            return {
                k: v for k, v in all_parameters.items()
                if k in self.config.parameters_to_tune
            }
    
    def _get_fixed_parameters(self, all_parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Get fixed parameters (those not in parameters_to_tune).
        
        This includes:
        1. Parameters explicitly in config.fixed_parameters
        2. Parameters in all_parameters that are NOT in parameters_to_tune
        
        Args:
            all_parameters: All current parameters
            
        Returns:
            Dictionary with fixed parameters (not being tuned)
        """
        fixed = {}
        
        # Add explicitly fixed parameters from config
        fixed.update(self.config.fixed_parameters)
        
        # Add parameters that are in all_parameters but not tunable
        if self.config.parameters_to_tune is None:
            # If parameters_to_tune is None, fixed params are those NOT in parameter_ranges
            for k, v in all_parameters.items():
                if k not in self.config.parameter_ranges:
                    fixed[k] = v
        else:
            # Fixed params are those NOT in parameters_to_tune
            for k, v in all_parameters.items():
                if k not in self.config.parameters_to_tune:
                    fixed[k] = v
        
        return fixed
    
    def _save_history(self, reason: str) -> str:
        """Save optimization history to file.
        
        Args:
            reason: Termination reason
            
        Returns:
            Path to history file
        """
        # Prevent double-saving
        if self._history_saved:
            logger.debug("History already saved, skipping duplicate save")
            return ""
        
        try:
            os.makedirs(self.config.results_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            benchmark_name = self.config.benchmark
            
            # Include model name in filename for LLM tuner
            model_suffix = ""
            if self.config.tuner_type == "llm" and hasattr(self.tuner, 'model_name'):
                # Sanitize model name for filename (remove special chars, replace spaces/colons with dashes)
                model_name = self.tuner.model_name.replace(" ", "-").replace(":", "-").replace("/", "-")
                model_suffix = f"_{model_name}"
            
            history_file = os.path.join(
                self.config.results_dir,
                f"optimization_history_{benchmark_name}{model_suffix}_{timestamp}.json"
            )
            
            history_data = {
                "config": self.config.to_dict(),
                "best_parameters": self.best_parameters,
                "best_reward": self.best_reward,
                "iterations": self.iteration,
                "total_time": time.time() - self.start_time,
                "timestamp": timestamp,
                "history": self.history,
                "terminated_reason": reason
            }
            
            # Add trimming phase data if trimming was used
            if self.trimming_tuner and self._trimming_phase_complete:
                trimming_data = {
                    "trimming_cycles": self.trimming_cycles,
                    "trimming_model": getattr(self.trimming_tuner, 'model_name', None),
                    "original_ranges": {k: list(v) if isinstance(v, tuple) else v
                                         for k, v in self.trimming_tuner._original_ranges.items()},
                    "effective_ranges": {k: list(v) if isinstance(v, tuple) else v
                                          for k, v in self.trimming_tuner.effective_ranges.items()},
                    "eliminated_params": self.trimming_tuner.get_eliminated_params(),
                    "trimming_actions": [
                        {
                            "cycle": a["cycle"],
                            "param": a["param"],
                            "old_range": list(a["old_range"]) if isinstance(a["old_range"], tuple) else a["old_range"],
                            "new_range": list(a["new_range"]) if isinstance(a["new_range"], tuple) else a["new_range"]
                        }
                        for a in self.trimming_tuner._trimming_actions
                    ]
                }
                history_data["trimming"] = trimming_data

            # Generate and add gist if supported by tuner
            if hasattr(self.tuner, 'generate_gist'):
                try:
                    logger.info("Generating optimization gist...")
                    gist_result = self.tuner.generate_gist(self.history)
                    
                    gist_text = None
                    gist_raw = None
                    
                    if isinstance(gist_result, tuple) and len(gist_result) == 2:
                        gist_text, gist_raw = gist_result
                    else:
                        gist_text = gist_result
                    
                    if gist_text:
                        history_data["optimizer_gist"] = gist_text
                        logger.info(f"OPTIMIZATION GIST: {gist_text}")
                    
                    if gist_raw:
                        # Convert to dict if possible for JSON serialization
                        if hasattr(gist_raw, 'to_dict'):
                            history_data["optimizer_gist_raw"] = gist_raw.to_dict()
                        elif isinstance(gist_raw, (dict, list, str, int, float, bool)):
                            history_data["optimizer_gist_raw"] = gist_raw
                        else:
                            history_data["optimizer_gist_raw"] = str(gist_raw)
                            
                except Exception as e:
                    logger.error(f"Failed to generate gist: {e}")
            
            with open(history_file, 'w') as f:
                json.dump(history_data, f, indent=2, default=_json_default)
            
            self._history_saved = True  # Mark as saved to prevent duplicates
            logger.info(f"Saved optimization history to {history_file}")
            return history_file
            
        except Exception as e:
            logger.error(f"Error saving history: {e}")
            return ""
