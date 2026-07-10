#!/usr/bin/env python3
"""
Human-in-the-loop tuner implementation.

This tuner prompts the user for parameter values interactively.
"""

import logging
from typing import Dict, Optional, Any

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)


class HumanTuner(TunerInterface):
    """Human-in-the-loop tuner that prompts the user for parameter values."""
    
    def __init__(self, config):
        """Initialize the human tuner.
        
        Args:
            config: Configuration object
        """
        self.config = config
        # Filter parameter_ranges based on parameters_to_tune
        if config.parameters_to_tune is not None:
            self.parameter_ranges = {
                k: v for k, v in config.parameter_ranges.items()
                if k in config.parameters_to_tune
            }
        else:
            self.parameter_ranges = config.parameter_ranges
        self.fixed_parameters = getattr(config, 'fixed_parameters', {})
        self.optimization_metric = config.optimization_metric
        self.optimization_goal = config.optimization_goal
        
        # Convert nanosecond ranges to millisecond ranges for user display
        self.ms_ranges = {}
        for param_name, param_range in self.parameter_ranges.items():
            if isinstance(param_range, tuple):
                min_ns, max_ns = param_range
                min_ms = min_ns / 1_000_000
                max_ms = max_ns / 1_000_000
                self.ms_ranges[param_name] = (min_ms, max_ms)
            else:
                # Categorical - keep as is
                self.ms_ranges[param_name] = param_range
        
        # Track previous parameters and reward for context
        self.previous_parameters = None
        self.previous_reward = None
        self.initial_prompt_shown = False
        
        logger.info(f"Human tuner initialized for {self.optimization_goal}ing {self.optimization_metric}")
    
    def _print_initial_prompt(self):
        """Print the initial instructional prompt."""
        print(f"\n{'='*70}")
        print("HUMAN TUNER")
        print(f"{'='*70}")
        print("You are acting as a Linux kernel scheduler tuning expert.")
        print(f"TASK: Optimize OS parameters to {self.optimization_goal.upper()} {self.optimization_metric}.")
        print("\nPARAMETER RANGES:")
        for param_name, param_range in self.ms_ranges.items():
            if isinstance(param_range, tuple):
                min_ms, max_ms = param_range
                print(f"  {param_name}: {min_ms:.1f} - {max_ms:.1f} ms")
            else:
                print(f"  {param_name}: {param_range}")
        print("\nINSTRUCTIONS:")
        print("- Enter values in milliseconds for numeric parameters.")
        print("- Values will be converted to nanoseconds internally.")
        print("- Provide values for all tunable parameters when prompted.")
        print(f"{'='*70}")
    
    def _prompt_for_parameters(self, current_reward: Optional[float] = None, 
                               previous_reward: Optional[float] = None,
                               previous_parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Prompt user for parameter values."""
        if not self.initial_prompt_shown:
            self._print_initial_prompt()
            acknowledgment = input("Type YES to acknowledge and start tuning: ").strip()
            if acknowledgment != 'YES':
                raise ValueError("Acknowledgment required to proceed with human tuning.")
            self.initial_prompt_shown = True
        
        print(f"\n{'='*70}")
        print("HUMAN TUNER: Provide new parameter values")
        print(f"Goal: {self.optimization_goal.upper()} {self.optimization_metric}")
        if previous_reward is not None:
            print(f"Previous {self.optimization_metric}: {previous_reward:.4f}")
        if current_reward is not None:
            print(f"Current {self.optimization_metric}: {current_reward:.4f}")
        else:
            print(f"Current {self.optimization_metric}: (first iteration - no previous measurement)")
        print("\nPARAMETER RANGES:")
        for param_name, param_range in self.ms_ranges.items():
            if isinstance(param_range, tuple):
                min_ms, max_ms = param_range
                print(f"  {param_name}: {min_ms:.1f} - {max_ms:.1f} ms")
            else:
                print(f"  {param_name}: {param_range}")
        if previous_parameters is not None:
            print("\nPrevious configuration:")
            for param_name in self.parameter_ranges.keys():
                if param_name in previous_parameters:
                    prev_val = previous_parameters[param_name]
                    if isinstance(prev_val, (int, float)) and prev_val > 1000:
                        prev_ms = prev_val / 1_000_000
                        print(f"  {param_name}: {prev_ms:.1f} ms ({prev_val:,} ns)")
                    else:
                        print(f"  {param_name}: {prev_val}")
        print(f"{'='*70}")
        
        parameters = {}
        
        for param_name, param_range in self.ms_ranges.items():
            if isinstance(param_range, tuple):
                # Continuous range - prompt in milliseconds
                min_ms, max_ms = param_range
                prev_hint = ""
                if previous_parameters and param_name in previous_parameters:
                    prev_ns = previous_parameters[param_name]
                    if isinstance(prev_ns, (int, float)):
                        prev_ms = prev_ns / 1_000_000
                        prev_hint = f" [prev: {prev_ms:.1f}]"
                
                while True:
                    try:
                        prompt = f"Enter {param_name} (range: {min_ms:.1f} - {max_ms:.1f} ms){prev_hint}: "
                        user_input = input(prompt).strip()
                        
                        if not user_input:
                            print("Please enter a value.")
                            continue
                        
                        value_ms = float(user_input)
                        
                        if value_ms < min_ms or value_ms > max_ms:
                            print(f"Value must be between {min_ms:.1f} and {max_ms:.1f} ms")
                            continue
                        
                        # Convert to nanoseconds
                        value_ns = int(round(value_ms * 1_000_000))
                        parameters[param_name] = value_ns
                        
                        print(f"  -> {param_name} = {value_ms} ms ({value_ns:,} ns)")
                        break
                        
                    except (ValueError, EOFError, KeyboardInterrupt):
                        print("Invalid input. Please enter a numeric value.")
                        continue
            else:
                # Categorical - prompt for selection
                valid_vals = param_range
                print(f"\n{param_name} options: {valid_vals}")
                prev_hint = ""
                if previous_parameters and param_name in previous_parameters:
                    prev_hint = f" [prev: {previous_parameters[param_name]}]"
                
                while True:
                    try:
                        prompt = f"Enter {param_name}{prev_hint}: "
                        user_input = input(prompt).strip()
                        
                        if not user_input:
                            print("Please enter a value.")
                            continue
                        
                        # Try to match the input to a valid value
                        normalized = self._normalize_categorical_value(param_name, user_input)
                        if normalized in valid_vals:
                            parameters[param_name] = normalized
                            print(f"  -> {param_name} = {normalized}")
                            break
                        else:
                            print(f"Invalid value. Must be one of: {valid_vals}")
                    except (ValueError, EOFError, KeyboardInterrupt):
                        print("Invalid input.")
                        continue
        
        print(f"{'='*70}")
        return parameters
    
    def _normalize_categorical_value(self, param_name: str, value: Any) -> Any:
        """Normalize categorical value to match valid values."""
        param_range = self.parameter_ranges.get(param_name, [])
        if isinstance(param_range, list):
            # Try exact match first
            if value in param_range:
                return value
            
            # Try case-insensitive string match
            if isinstance(value, str):
                for valid_val in param_range:
                    if isinstance(valid_val, str) and valid_val.lower() == value.lower():
                        return valid_val
            
            # Try boolean conversion
            if param_name == "turbo":
                if str(value).lower() in ["true", "1", "on", "yes", "enabled"]:
                    return True
                elif str(value).lower() in ["false", "0", "off", "no", "disabled"]:
                    return False
        
        return value
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters based on user input.
        
        Args:
            metrics: Current benchmark metrics
            current_params: Current parameter values
            iteration: Current iteration number
            best_reward: Best reward seen so far
            **kwargs: Additional arguments (unused, for forward compatibility)
            
        Returns:
            TunerResponse with suggested parameters
        """
        # Get current reward
        current_reward = metrics.get_metric(self.optimization_metric)
        
        # Extract tunable parameters from current_params (parameters used for current iteration)
        # Include all parameters from parameter_ranges, using current_params if available,
        # otherwise fall back to fixed_parameters or defaults
        tunable_params = {}
        for param_name in self.parameter_ranges.keys():
            if param_name in current_params:
                tunable_params[param_name] = current_params[param_name]
            elif param_name in self.fixed_parameters:
                tunable_params[param_name] = self.fixed_parameters[param_name]
        
        # Store previous values for display
        # previous_params_for_display should be the parameters that were used for the CURRENT iteration
        # which we can get from tunable_params (extracted from current_params)
        # On first call (iteration 1), tunable_params will be from initial/default parameters
        previous_reward_for_display = self.previous_reward
        # Use tunable_params as previous_params_for_display (these are the params used for current iteration)
        previous_params_for_display = tunable_params.copy() if tunable_params else None
        
        # Store current iteration's parameters as previous for the NEXT call
        # This is the parameters that were used for the current iteration (just completed)
        self.previous_parameters = tunable_params.copy()  # Make a copy to avoid reference issues
        self.previous_reward = current_reward
        
        # Prompt user (using the previous values before we updated them)
        new_params = self._prompt_for_parameters(
            current_reward=current_reward,
            previous_reward=previous_reward_for_display,
            previous_parameters=previous_params_for_display
        )
        
        return TunerResponse(
            parameters=new_params,
            confidence=1.0,
            justification="Human-provided parameters"
        )

