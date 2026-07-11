#!/usr/bin/env python3
"""
Deep Q-Network (DQN) based parameter tuner implementation.

This tuner uses a Deep Q-Network with PyTorch to optimize parameters.
"""

import logging
import random
import numpy as np
from typing import Dict, Any
from collections import deque, namedtuple

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse, unwrap_parameter_value

logger = logging.getLogger(__name__)

# Try to import PyTorch for DQN
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import torch.nn.functional as F
    DQN_AVAILABLE = True
    
    class DQNNetwork(nn.Module):
        """Deep Q-Network for parameter tuning."""
        
        def __init__(self, state_size: int, action_size: int, hidden_size: int = 128):
            """Initialize DQN network."""
            super(DQNNetwork, self).__init__()
            self.fc1 = nn.Linear(state_size, hidden_size)
            self.fc2 = nn.Linear(hidden_size, hidden_size)
            self.fc3 = nn.Linear(hidden_size, hidden_size)
            self.fc4 = nn.Linear(hidden_size, action_size)
            self.dropout = nn.Dropout(0.1)
        
        def forward(self, x):
            """Forward pass."""
            x = F.relu(self.fc1(x))
            x = self.dropout(x)
            x = F.relu(self.fc2(x))
            x = self.dropout(x)
            x = F.relu(self.fc3(x))
            x = self.fc4(x)
            return x
except ImportError:
    DQN_AVAILABLE = False
    DQNNetwork = None
    logger.warning("PyTorch not available. Install with 'pip install torch' to use DQN tuner.")


class DQNTuner(TunerInterface):
    """Deep Q-Network based parameter tuner with discretized action space."""
    
    def __init__(self, config):
        """Initialize DQN tuner.
        
        Args:
            config: Configuration object
        """
        if not DQN_AVAILABLE:
            raise ImportError("PyTorch is required for DQN tuner. Install with: pip install torch")
        
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
        
        # DQN configuration
        self.grid_points = getattr(config, 'dqn_grid_points', 10)
        self.max_actions = getattr(config, 'dqn_max_actions', 1000)
        self.learning_rate = getattr(config, 'dqn_learning_rate', 0.001)
        self.epsilon_start = getattr(config, 'dqn_epsilon_start', 1.0)
        self.epsilon_end = getattr(config, 'dqn_epsilon_end', 0.1)
        self.epsilon_decay = getattr(config, 'dqn_epsilon_decay', 0.995)
        self.batch_size = getattr(config, 'dqn_batch_size', 32)
        self.memory_size = getattr(config, 'dqn_memory_size', 1000)
        self.target_update_freq = getattr(config, 'dqn_target_update_freq', 10)
        self.hidden_size = getattr(config, 'dqn_hidden_size', 128)
        self.gamma = getattr(config, 'dqn_gamma', 0.99)
        
        # Bound the joint action space before allocating the network output
        # layer. With eight paper knobs, the legacy default of ten points per
        # numeric dimension would otherwise create hundreds of millions of
        # actions and exhaust memory before the first benchmark window.
        continuous_params = [k for k, v in self.parameter_ranges.items() if isinstance(v, tuple)]
        categorical_sizes = [
            len(v) for v in self.parameter_ranges.values() if not isinstance(v, tuple)
        ]
        base_actions = int(np.prod(categorical_sizes)) if categorical_sizes else 1
        if base_actions > self.max_actions:
            raise ValueError(
                f"DQN categorical action space is too large ({base_actions} > {self.max_actions})"
            )
        if continuous_params:
            max_grid = int((self.max_actions / base_actions) ** (1 / len(continuous_params)))
            max_grid = max(1, max_grid)
            if self.grid_points > max_grid:
                logger.warning(
                    "DQN grid_points reduced from %d to %d to cap action space at %d",
                    self.grid_points, max_grid, self.max_actions,
                )
                self.grid_points = max_grid

        # Create discretized parameter space
        self.param_names = list(self.parameter_ranges.keys())
        self.discretized_ranges = self._create_discretized_ranges()
        self.action_space_size = int(np.prod([len(ranges) for ranges in self.discretized_ranges.values()]))
        if self.action_space_size > self.max_actions:
            raise ValueError(
                f"DQN action space is too large ({self.action_space_size} > {self.max_actions})"
            )
        self.state_size = len(self.param_names) + 3  # parameters + reward + iteration + time_since_change
        
        # Initialize DQN components
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.q_network = DQNNetwork(self.state_size, self.action_space_size, self.hidden_size).to(self.device)
        self.target_network = DQNNetwork(self.state_size, self.action_space_size, self.hidden_size).to(self.device)
        self.optimizer = optim.Adam(self.q_network.parameters(), lr=self.learning_rate)
        self.target_network.load_state_dict(self.q_network.state_dict())
        
        # Experience replay buffer
        self.Experience = namedtuple('Experience', ['state', 'action', 'reward', 'next_state', 'done'])
        self.memory = deque(maxlen=self.memory_size)
        
        # Training state
        self.epsilon = self.epsilon_start
        self.current_state = None
        self.last_action = None
        self.current_iteration = 0
        self.steps_since_target_update = 0
        self.rewards_history = []
        self.last_change_iteration = 0
        
        logger.info(f"DQN tuner initialized with {self.action_space_size} discrete actions")
        logger.info(f"Using device: {self.device}")
    
    def _create_discretized_ranges(self) -> Dict[str, list]:
        """Create discretized parameter ranges."""
        discretized = {}
        for param_name, param_range in self.parameter_ranges.items():
            if isinstance(param_range, tuple):
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
                value = unwrap_parameter_value(parameters[param_name])
            else:
                value = param_values[0] if param_values else 0
            
            closest_idx = min(range(len(param_values)), 
                             key=lambda i: abs(param_values[i] - value) if isinstance(value, (int, float)) else 0)
            
            action += closest_idx * multiplier
            multiplier *= len(param_values)
        
        return action
    
    def _create_state(self, parameters: Dict[str, Any], reward: float = 0.0) -> np.ndarray:
        """Create state representation for DQN."""
        state = []
        
        # Normalize parameter values
        for param_name in self.param_names:
            # Get value from parameters, or use default
            if param_name not in parameters:
                # Use first value from discretized range or fixed parameter as default
                if param_name in self.fixed_parameters:
                    param_value = self.fixed_parameters[param_name]
                else:
                    param_values = self.discretized_ranges.get(param_name, [0])
                    param_value = param_values[0] if param_values else 0
            else:
                param_value = unwrap_parameter_value(parameters[param_name])
            
            if isinstance(self.parameter_ranges[param_name], tuple):
                min_val, max_val = self.parameter_ranges[param_name]
                normalized_val = (param_value - min_val) / (max_val - min_val) if max_val > min_val else 0.5
                state.append(normalized_val)
            else:
                # Categorical - encode as index
                param_values = self.discretized_ranges[param_name]
                if param_value in param_values:
                    idx = param_values.index(param_value)
                    normalized_val = idx / len(param_values) if len(param_values) > 0 else 0.5
                else:
                    normalized_val = 0.5
                state.append(normalized_val)
        
        # Add normalized reward
        if self.optimization_goal == "minimize":
            normalized_reward = max(0, min(1, (10000 - reward) / 10000))
        else:
            normalized_reward = max(0, min(1, reward / 10000))
        state.append(normalized_reward)
        
        # Add normalized iteration
        normalized_iteration = min(1.0, self.current_iteration / 100)
        state.append(normalized_iteration)
        
        # Add time since last parameter change
        time_since_change = min(1.0, (self.current_iteration - self.last_change_iteration) / 10)
        state.append(time_since_change)
        
        return np.array(state, dtype=np.float32)
    
    def _select_action(self, state: np.ndarray) -> int:
        """Select action using epsilon-greedy policy."""
        if random.random() < self.epsilon:
            action = random.randint(0, self.action_space_size - 1)
        else:
            with torch.no_grad():
                state_tensor = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                q_values = self.q_network(state_tensor)
                action = q_values.argmax().item()
        
        return action
    
    def _store_experience(self, state: np.ndarray, action: int, reward: float, 
                         next_state: np.ndarray, done: bool = False):
        """Store experience in replay buffer."""
        experience = self.Experience(state, action, reward, next_state, done)
        self.memory.append(experience)
    
    def _replay_training(self):
        """Train DQN on a batch of experiences."""
        if len(self.memory) < self.batch_size:
            return
        
        batch = random.sample(self.memory, self.batch_size)
        states = torch.FloatTensor([e.state for e in batch]).to(self.device)
        actions = torch.LongTensor([e.action for e in batch]).to(self.device)
        rewards = torch.FloatTensor([e.reward for e in batch]).to(self.device)
        next_states = torch.FloatTensor([e.next_state for e in batch]).to(self.device)
        dones = torch.BoolTensor([e.done for e in batch]).to(self.device)
        
        current_q_values = self.q_network(states).gather(1, actions.unsqueeze(1))
        next_q_values = self.target_network(next_states).max(1)[0].detach()
        target_q_values = rewards + (self.gamma * next_q_values * ~dones)
        
        loss = F.mse_loss(current_q_values.squeeze(), target_q_values)
        
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
    
    def _update_target_network(self):
        """Update target network."""
        self.target_network.load_state_dict(self.q_network.state_dict())
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters using DQN."""
        self.current_iteration = iteration
        
        # Get reward
        reward = metrics.get_metric(self.optimization_metric)
        
        # Convert reward for DQN
        if self.optimization_goal == "minimize":
            dqn_reward = -reward
        else:
            dqn_reward = reward
        
        # Create current state
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
        current_state = self._create_state(tunable_params, reward)
        
        # Store experience and train if we have previous state
        if self.current_state is not None and self.last_action is not None:
            self._store_experience(self.current_state, self.last_action, dqn_reward, 
                                 current_state, done=False)
            self._replay_training()
            
            # Update target network periodically
            self.steps_since_target_update += 1
            if self.steps_since_target_update >= self.target_update_freq:
                self._update_target_network()
                self.steps_since_target_update = 0
        
        # Update epsilon
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)
        
        # Select next action
        action = self._select_action(current_state)
        new_params = self._action_to_parameters(action)
        
        # Check if parameters changed
        if new_params != tunable_params:
            self.last_change_iteration = self.current_iteration
        
        # Update state
        self.current_state = current_state
        self.last_action = action
        self.rewards_history.append(reward)
        
        logger.info(f"DQN iteration {iteration}: epsilon={self.epsilon:.3f}, reward={reward:.4f}")
        
        return TunerResponse(
            parameters=new_params,
            confidence=1.0 - self.epsilon,
            justification=f"DQN (epsilon={self.epsilon:.3f})"
        )
