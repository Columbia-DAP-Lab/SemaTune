#!/usr/bin/env python3
"""
Q-Learning based parameter tuner implementation.

This tuner uses Q-Learning with a discretized action space to optimize parameters.
"""

import logging
import random
import numpy as np
from typing import Dict, Any

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)


class QLearningTuner(TunerInterface):
    """Q-Learning based parameter tuner with discretized action space."""
    
    def __init__(self, config):
        """Initialize Q-Learning tuner.
        
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
        
        # Q-Learning configuration
        self.grid_points = getattr(config, 'qlearning_grid_points', 10)
        self.max_actions = getattr(config, 'qlearning_max_actions', 1000)
        self.learning_rate = getattr(config, 'qlearning_learning_rate', 0.1)
        self.epsilon_start = getattr(config, 'qlearning_epsilon_start', 1.0)
        self.epsilon_end = getattr(config, 'qlearning_epsilon_end', 0.1)
        self.epsilon_decay = getattr(config, 'qlearning_epsilon_decay', 0.995)
        self.gamma = getattr(config, 'qlearning_gamma', 0.99)
        
        # Cap grid points to keep the action space manageable
        continuous_params = [k for k, v in self.parameter_ranges.items() if isinstance(v, tuple)]
        categorical_sizes = [
            len(v) for v in self.parameter_ranges.values() if not isinstance(v, tuple)
        ]
        base_actions = int(np.prod(categorical_sizes)) if categorical_sizes else 1
        if base_actions > self.max_actions:
            raise ValueError(
                f"Q-learning action space too large for categorical parameters "
                f"({base_actions} > {self.max_actions}). Reduce categorical options or "
                f"increase qlearning_max_actions."
            )
        if continuous_params:
            max_grid = int((self.max_actions / base_actions) ** (1 / len(continuous_params)))
            max_grid = max(1, max_grid)
            if self.grid_points > max_grid:
                logger.warning(
                    "Q-learning grid_points reduced from %d to %d to cap action space at %d",
                    self.grid_points, max_grid, self.max_actions
                )
                self.grid_points = max_grid

        # Create discretized parameter space
        self.param_names = list(self.parameter_ranges.keys())
        self.discretized_ranges = self._create_discretized_ranges()
        self.action_space_size = int(np.prod([len(ranges) for ranges in self.discretized_ranges.values()]))
        self.state_space_size = self.action_space_size
        if self.action_space_size > self.max_actions:
            raise ValueError(
                f"Q-learning action space too large ({self.action_space_size} > {self.max_actions}). "
                "Reduce qlearning_grid_points, tune fewer parameters, or increase qlearning_max_actions."
            )
        
        # Initialize Q-table
        self.q_table = np.zeros((self.state_space_size, self.action_space_size))
        
        # Training state
        self.epsilon = self.epsilon_start
        self.current_state = None
        self.last_action = None
        self.current_iteration = 0
        self.rewards_history = []
        self.last_change_iteration = 0
        
        logger.info(f"Q-Learning tuner initialized with {self.action_space_size} discrete actions")
        logger.info(f"Grid points per parameter: {self.grid_points}")
    
    def _create_discretized_ranges(self) -> Dict[str, list]:
        """Create discretized parameter ranges."""
        discretized = {}
        for param_name, param_range in self.parameter_ranges.items():
            if isinstance(param_range, tuple):
                # Continuous range - discretize
                min_val, max_val = param_range
                if min_val > 0:
                    log_min = np.log10(min_val)
                    log_max = np.log10(max_val)
                    log_values = np.linspace(log_min, log_max, self.grid_points)
                    values = [int(10**log_val) for log_val in log_values]
                else:
                    values = [int(v) for v in np.linspace(min_val, max_val, self.grid_points)]
                values = sorted(list(set(values)))
                discretized[param_name] = values
            else:
                # Categorical - use as is
                discretized[param_name] = list(param_range)
        
        return discretized
    
    def _action_to_parameters(self, action: int) -> Dict[str, Any]:
        """Convert action index to parameter dictionary."""
        params = {}
        remaining = action
        
        for param_name in reversed(self.param_names):
            param_values = self.discretized_ranges[param_name]
            idx = remaining % len(param_values)
            params[param_name] = param_values[idx]
            remaining //= len(param_values)
        
        return params
    
    def _parameters_to_action(self, parameters: Dict[str, Any]) -> int:
        """Convert parameter dictionary to action index."""
        action = 0
        multiplier = 1
        
        for param_name in reversed(self.param_names):
            param_values = self.discretized_ranges[param_name]
            # Get value from parameters, or use first value from range as default
            if param_name in parameters:
                value = parameters[param_name]
            else:
                value = param_values[0] if param_values else 0
            
            # Find closest value in discretized range
            closest_idx = min(range(len(param_values)), 
                             key=lambda i: abs(param_values[i] - value) if isinstance(value, (int, float)) else 0)
            
            action += closest_idx * multiplier
            multiplier *= len(param_values)
        
        return action
    
    def _select_action(self, state: int) -> int:
        """Select action using epsilon-greedy policy."""
        if random.random() < self.epsilon:
            action = random.randint(0, self.action_space_size - 1)
        else:
            action = int(np.argmax(self.q_table[state]))
        
        return action
    
    def _update_q_table(self, state: int, action: int, reward: float, next_state: int):
        """Update Q-table using Q-learning update rule."""
        current_q = self.q_table[state, action]
        max_next_q = np.max(self.q_table[next_state])
        new_q = current_q + self.learning_rate * (reward + self.gamma * max_next_q - current_q)
        self.q_table[state, action] = new_q
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters using Q-Learning."""
        self.current_iteration = iteration
        
        # Get reward
        reward = metrics.get_metric(self.optimization_metric)
        
        # Convert reward for Q-learning (normalize)
        if self.optimization_goal == "minimize":
            normalized_reward = -reward  # Negate for minimization
        else:
            normalized_reward = reward
        
        # Convert current parameters to state
        # Include all parameters from parameter_ranges, using current_params if available, otherwise fixed_parameters
        tunable_params = {}
        for param_name in self.parameter_ranges.keys():
            if param_name in current_params:
                tunable_params[param_name] = current_params[param_name]
            elif param_name in self.fixed_parameters:
                tunable_params[param_name] = self.fixed_parameters[param_name]
            else:
                # Use first value from discretized range as default
                tunable_params[param_name] = self.discretized_ranges[param_name][0]
        current_state = self._parameters_to_action(tunable_params)
        
        # Update Q-table if we have previous state
        if self.current_state is not None and self.last_action is not None:
            self._update_q_table(self.current_state, self.last_action, normalized_reward, current_state)
        
        # Update epsilon
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)
        
        # Select next action
        next_action = self._select_action(current_state)
        new_params = self._action_to_parameters(next_action)
        
        # Update state
        self.current_state = current_state
        self.last_action = next_action
        self.rewards_history.append(reward)
        
        logger.info(f"Q-Learning iteration {iteration}: epsilon={self.epsilon:.3f}, reward={reward:.4f}")
        
        return TunerResponse(
            parameters=new_params,
            confidence=1.0 - self.epsilon,  # Confidence increases as epsilon decreases
            justification=f"Q-Learning (epsilon={self.epsilon:.3f})"
        )

