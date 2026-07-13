#!/usr/bin/env python3
"""
Bayesian optimization tuner implementation using SMAC3.

This tuner uses SMAC3 for Bayesian optimization of parameters.
"""

import os
import logging
import tempfile
import shutil
from typing import Dict, Any

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)

# Try to import SMAC3 for Bayesian optimization
try:
    from ConfigSpace import ConfigurationSpace, UniformIntegerHyperparameter, CategoricalHyperparameter
    from smac import HyperparameterOptimizationFacade, Scenario
    from smac.runhistory.dataclasses import TrialValue
    SMAC3_AVAILABLE = True
except ImportError:
    SMAC3_AVAILABLE = False
    logger.warning("SMAC3 not available. Install with 'pip install smac' to use Bayesian optimization.")


class BayesianOptimizerTuner(TunerInterface):
    """Bayesian optimization tuner using SMAC3."""
    
    def __init__(self, config):
        """Initialize SMAC3 tuner.
        
        Args:
            config: Configuration object
        """
        if not SMAC3_AVAILABLE:
            raise ImportError("SMAC3 is required for Bayesian optimization. Install with: pip install smac")
        
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
        
        # SMAC3 configuration
        self.n_trials = getattr(config, 'bayesian_n_trials', 50)
        self.seed = getattr(config, 'bayesian_seed', 42)
        
        # Create configuration space
        self.configspace = self._create_configuration_space()
        
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp(prefix="smac3_tuner_")
        
        # Create scenario
        self.scenario = Scenario(
            configspace=self.configspace,
            deterministic=False,
            n_trials=self.n_trials,
            n_workers=1,
            seed=self.seed,
            output_directory=self.temp_dir
        )
        
        # Initialize SMAC3
        self.smac = None
        self.current_config = None
        self.observations = []
        
        logger.info("Bayesian optimizer (SMAC3) initialized")
    
    def _create_configuration_space(self) -> ConfigurationSpace:
        """Create SMAC3 configuration space from parameter ranges.
        
        Supports both:
        - Numerical parameters: specified as tuples (min, max)
        - Categorical parameters: specified as lists or sets of discrete values
        """
        cs = ConfigurationSpace(seed=self.seed)
        
        for param_name, param_range in self.parameter_ranges.items():
            if isinstance(param_range, tuple):
                # Numerical parameter: use UniformIntegerHyperparameter
                min_val, max_val = param_range
                # Cannot use log scale when lower bound is 0 (log(0) is undefined)
                use_log = min_val > 0
                hyperparameter = UniformIntegerHyperparameter(
                    name=param_name,
                    lower=min_val,
                    upper=max_val,
                    log=use_log  # Use log scale for scheduler parameters (but not when min=0)
                )
                cs.add_hyperparameter(hyperparameter)
            elif isinstance(param_range, (list, set)):
                # Categorical parameter: use CategoricalHyperparameter
                categories = list(param_range)
                hyperparameter = CategoricalHyperparameter(
                    name=param_name,
                    choices=categories
                )
                cs.add_hyperparameter(hyperparameter)
            else:
                logger.warning(f"Unsupported parameter range type for {param_name}: {type(param_range)}")
        
        return cs
    
    def _convert_to_config(self, parameters: Dict[str, Any]):
        """Convert parameters dict to SMAC3 Configuration.
        
        Handles both numerical (tuple) and categorical (list/set) parameters.
        """
        # Extract only parameters that are in the configuration space
        optimized_only = {
            key: parameters[key] 
            for key in self.parameter_ranges.keys() 
            if key in parameters
        }
        from ConfigSpace import Configuration
        return Configuration(self.configspace, optimized_only)
    
    def _convert_from_config(self, config) -> Dict[str, Any]:
        """Convert SMAC3 Configuration to parameters dict.
        
        Handles both numerical (tuple) and categorical (list/set) parameters.
        """
        if hasattr(config, 'config'):
            config = config.config
        
        optimized_params = {}
        for key in self.parameter_ranges.keys():
            if key in config:
                param_range = self.parameter_ranges[key]
                if isinstance(param_range, tuple):
                    # Numerical parameter - convert to int
                    optimized_params[key] = int(config[key])
                elif isinstance(param_range, (list, set)):
                    # Categorical parameter - use as is
                    optimized_params[key] = config[key]
        
        # Combine with fixed parameters
        combined_params = self.fixed_parameters.copy() if self.fixed_parameters else {}
        combined_params.update(optimized_params)
        
        return combined_params
    
    def _create_target_function(self):
        """Create dummy target function for SMAC3."""
        def dummy_target(config, seed: int = 0):
            return 0.0
        return dummy_target
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters using SMAC3."""
        # Initialize SMAC3 on first call
        if self.smac is None:
            self.target_function = self._create_target_function()
            self.smac = HyperparameterOptimizationFacade(
                scenario=self.scenario,
                target_function=self.target_function,
                overwrite=True
            )
            # Get initial configuration
            self.current_config = self.smac.ask()
            return TunerResponse(
                parameters=self._convert_from_config(self.current_config),
                confidence=1.0,
                justification="SMAC3 initial configuration"
            )
        
        # Get reward
        reward = metrics.get_metric(self.optimization_metric)
        
        # Convert reward for SMAC3 (which always minimizes)
        smac_value = reward
        if self.optimization_goal == 'maximize':
            smac_value = -reward
        
        # Tell SMAC3 about current observation
        if self.current_config is not None:
            trial_value = TrialValue(cost=smac_value, time=0.5)
            self.smac.tell(self.current_config, trial_value)
            self.observations.append((current_params, reward))
        
        # Ask for next configuration
        try:
            next_config = self.smac.ask()
            self.current_config = next_config
            new_params = self._convert_from_config(next_config)
            
            return TunerResponse(
                parameters=new_params,
                confidence=1.0,
                justification="SMAC3 Bayesian optimization"
            )
        except Exception as e:
            logger.error(f"Error getting next configuration from SMAC3: {e}")
            # Fallback to current parameters
            return TunerResponse(
                parameters=current_params,
                confidence=0.0,
                justification=f"SMAC3 error, using current parameters: {e}"
            )
    
    def __del__(self):
        """Clean up temporary directory."""
        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except:
                pass

