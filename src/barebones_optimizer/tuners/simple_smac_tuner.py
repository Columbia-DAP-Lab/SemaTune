#!/usr/bin/env python3
"""
Simple SMAC3 tuner implementation - direct SMAC3 usage with ask/tell interface.

This tuner uses SMAC3's HyperparameterOptimizationFacade directly with ask/tell
for Bayesian optimization on small discrete search spaces.
"""

import logging
from typing import Dict, Any, Optional
from pathlib import Path
from tempfile import TemporaryDirectory

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)

# Try to import SMAC3
try:
    from ConfigSpace import (
        ConfigurationSpace,
        UniformIntegerHyperparameter,
        CategoricalHyperparameter,
    )
    from smac import HyperparameterOptimizationFacade as HPOFacade
    from smac import Scenario
    from smac.runhistory import TrialValue, StatusType
    SMAC_AVAILABLE = True
except ImportError as e:
    SMAC_AVAILABLE = False
    logger.warning(f"SMAC3 not available. Install with 'pip install smac' to use Simple SMAC optimizer. Error: {e}")


class SimpleSMACTuner(TunerInterface):
    """Simple SMAC3 tuner using ask/tell Bayesian optimization."""
    
    def __init__(self, config):
        """Initialize Simple SMAC tuner.
        
        Args:
            config: Configuration object
        """
        if not SMAC_AVAILABLE:
            raise ImportError("SMAC3 is required. Install with: pip install smac")
        
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
        
        # Get parameter types from config (if specified)
        self.parameter_types = getattr(config, 'parameter_types', None) or {}
        
        # Set random seed
        self.seed = getattr(config, 'mlos_seed', 42)
        
        # Create configuration space
        self.configspace = self._create_configuration_space()
        
        # SMAC config
        self.n_random_init = getattr(config, 'mlos_n_random_init', 10)
        self.max_trials = getattr(config, 'max_iterations', 100)
        
        # Create temporary output directory
        self.temp_dir = TemporaryDirectory()
        
        # Create SMAC scenario
        self.scenario = Scenario(
            configspace=self.configspace,
            objectives=[self.optimization_metric],
            n_trials=self.max_trials,
            seed=self.seed,
            output_directory=Path(self.temp_dir.name),
            deterministic=True,
            n_workers=1,
        )
        
        # Initialize SMAC optimizer
        self.smac = HPOFacade(
            scenario=self.scenario,
            target_function=self._dummy_target,  # Required but not used with ask/tell
            overwrite=True,
        )
        
        self.iteration_count = 0
        
        logger.info(f"Simple SMAC tuner initialized with ask/tell interface")
        logger.info(f"  n_random_init: {self.n_random_init}")
        logger.info(f"  max_trials: {self.max_trials}")
    
    def __del__(self):
        """Cleanup temporary directory."""
        if hasattr(self, 'temp_dir'):
            self.temp_dir.cleanup()
    
    def _dummy_target(self, config, seed=0):
        """Dummy target function (required by SMAC but not used with ask/tell)."""
        raise RuntimeError("This should never be called with ask/tell interface")
    
    def _create_configuration_space(self) -> ConfigurationSpace:
        """Create SMAC ConfigurationSpace from parameter ranges."""
        cs = ConfigurationSpace(seed=self.seed)
        
        logger.info("=" * 80)
        logger.info("Simple SMAC Configuration Space Setup")
        logger.info("=" * 80)
        
        for param_name, param_range in self.parameter_ranges.items():
            explicit_type = self.parameter_types.get(param_name)
            
            if isinstance(param_range, tuple):
                # Continuous/discrete range [min, max]
                min_val, max_val = param_range
                
                # Determine type
                if explicit_type == "integer" or (isinstance(min_val, int) and isinstance(max_val, int)):
                    hyperparameter = UniformIntegerHyperparameter(
                        name=param_name,
                        lower=min_val,
                        upper=max_val,
                        log=False
                    )
                    num_values = max_val - min_val + 1
                    logger.info(f"  {param_name}: UniformInteger [{min_val}, {max_val}] ({num_values} values)")
                else:
                    logger.warning(f"Skipping float parameter {param_name} - not supported in simple mode")
                    continue
                
                cs.add_hyperparameter(hyperparameter)
                
            elif isinstance(param_range, list):
                # Categorical
                choices = [str(v) for v in param_range]
                hyperparameter = CategoricalHyperparameter(
                    name=param_name,
                    choices=choices
                )
                cs.add_hyperparameter(hyperparameter)
                logger.info(f"  {param_name}: Categorical {choices} ({len(choices)} choices)")
            else:
                logger.warning(f"Unknown parameter range type for {param_name}: {type(param_range)}. Skipping.")
        
        # Calculate total search space
        total_space = 1
        for hp in cs.get_hyperparameters():
            if isinstance(hp, UniformIntegerHyperparameter):
                total_space *= (hp.upper - hp.lower + 1)
            elif isinstance(hp, CategoricalHyperparameter):
                total_space *= len(hp.choices)
        
        logger.info(f"\nTotal search space: {total_space:,} discrete configurations")
        logger.info("=" * 80)
        
        return cs
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters using SMAC's ask/tell interface."""
        
        # Register previous observation if not first iteration
        if iteration > 1 and hasattr(self, '_last_trial_info'):
            try:
                # Get the metric value
                metric_value = metrics.get_metric(self.optimization_metric)
                
                # Create TrialValue
                trial_value = TrialValue(
                    cost=[metric_value],  # SMAC expects list of costs
                    time=0.0,
                    status=StatusType.SUCCESS
                )
                
                # Tell SMAC about the result
                self.smac.tell(self._last_trial_info, trial_value)
                logger.info(f"Registered result: {self.optimization_metric}={metric_value:.4f}")
                
            except Exception as e:
                logger.error(f"Error registering observation with SMAC: {e}", exc_info=True)
        
        # Ask SMAC for next configuration
        try:
            logger.info(f"Calling SMAC ask() for iteration {iteration}")
            trial_info = self.smac.ask()
            self._last_trial_info = trial_info
            
            config = trial_info.config
            
            # Convert to parameters dict
            suggested_params = {}
            for param_name in self.parameter_ranges.keys():
                if param_name in config:
                    value = config[param_name]
                    param_range = self.parameter_ranges[param_name]
                    
                    # Convert back to original type
                    if isinstance(param_range, list):
                        # Categorical - convert from string back to original type
                        str_value = str(value)
                        for orig_val in param_range:
                            if str(orig_val) == str_value:
                                suggested_params[param_name] = orig_val
                                break
                        else:
                            suggested_params[param_name] = value
                    elif isinstance(param_range, tuple):
                        # Integer
                        min_val, max_val = param_range
                        if isinstance(min_val, int) and isinstance(max_val, int):
                            suggested_params[param_name] = int(value)
                        else:
                            suggested_params[param_name] = float(value)
                    else:
                        suggested_params[param_name] = value
            
            logger.info(f"SMAC suggested parameters: {suggested_params}")
            
            self.iteration_count += 1
            
            # Determine if this is random init or Bayesian
            if self.iteration_count <= self.n_random_init:
                justification = f"Random initialization ({self.iteration_count}/{self.n_random_init})"
            else:
                justification = f"Bayesian optimization (iteration {self.iteration_count})"
            
            return TunerResponse(
                parameters=suggested_params,
                confidence=0.8,
                justification=justification
            )
            
        except StopIteration:
            logger.warning(f"SMAC exhausted search space. Using last known config.")
            # Return current parameters
            tunable_params = {k: v for k, v in current_params.items() if k in self.parameter_ranges}
            return TunerResponse(
                parameters=tunable_params,
                confidence=0.0,
                justification="SMAC exhausted - using current config"
            )
        except Exception as e:
            logger.error(f"Error getting suggestion from SMAC: {e}", exc_info=True)
            # Fallback to current parameters
            tunable_params = {k: v for k, v in current_params.items() if k in self.parameter_ranges}
            return TunerResponse(
                parameters=tunable_params,
                confidence=0.0,
                justification=f"SMAC error: {e}"
            )
