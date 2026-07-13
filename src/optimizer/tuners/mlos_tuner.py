#!/usr/bin/env python3
"""
MLOS optimizer tuner implementation using MLOS SmacOptimizer.

This tuner uses MLOS's SmacOptimizer wrapper for Bayesian optimization.
MLOS provides a more sophisticated interface with better support for
multi-objective optimization, space adapters, and advanced features.
"""

import os
import logging
import pandas as pd
from typing import Dict, Any, Optional, Union, List

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)

# Try to import MLOS optimizer
try:
    from ConfigSpace import (
        ConfigurationSpace,
        UniformIntegerHyperparameter,
        UniformFloatHyperparameter,
        CategoricalHyperparameter,
    )
    from mlos_core.optimizers.bayesian_optimizers.smac_optimizer import SmacOptimizer
    from mlos_core.data_classes import Observation, Observations
    MLOS_AVAILABLE = True
except ImportError as e:
    MLOS_AVAILABLE = False
    logger.warning(f"MLOS not available. Install with 'pip install mlos-core' to use MLOS optimizer. Error: {e}")


class MLOSTuner(TunerInterface):
    """MLOS optimizer tuner using SmacOptimizer."""
    
    def __init__(self, config):
        """Initialize MLOS tuner.
        
        Args:
            config: Configuration object
        """
        if not MLOS_AVAILABLE:
            raise ImportError("MLOS is required. Install with: pip install mlos-core")
        
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
        
        # MLOS configuration
        self.max_trials = getattr(config, 'mlos_max_trials', 100)
        # n_random_init MUST be >= 1 so SMAC has initial data before fitting
        # its Bayesian model.  Without initial random samples, SMAC's ask()
        # hangs when the ConfigSpace mixes categorical + continuous parameters.
        self.n_random_init = getattr(config, 'mlos_n_random_init', 3)
        self.max_ratio = getattr(config, 'mlos_max_ratio', None)
        self.use_default_config = getattr(config, 'mlos_use_default_config', False)
        self.n_random_probability = getattr(config, 'mlos_n_random_probability', 0.1)
        self.seed = getattr(config, 'mlos_seed', 42)
        self.run_name = getattr(config, 'mlos_run_name', None)
        self.output_directory = getattr(config, 'mlos_output_directory', None)
        
        # Objective weights for multi-objective (if needed in future)
        self.objective_weights = getattr(config, 'mlos_objective_weights', None)
        
        # Create configuration space
        self.configspace = self._create_configuration_space()
        
        # Initialize MLOS optimizer
        self.mlos_optimizer = None
        self.iteration_count = 0
        
        logger.info("MLOS optimizer tuner initialized")
    
    def _create_configuration_space(self) -> ConfigurationSpace:
        """Create MLOS ConfigurationSpace from parameter ranges.
        
        Supports both continuous (tuple) and categorical (list) parameters.
        Uses explicit parameter_types from config if available.
        """
        cs = ConfigurationSpace(seed=self.seed)
        
        # Get parameter types from config (if specified)
        parameter_types = getattr(self.config, 'parameter_types', None) or {}
        
        logger.info("=" * 80)
        logger.info("MLOS Configuration Space Setup")
        logger.info("=" * 80)
        
        for param_name, param_range in self.parameter_ranges.items():
            # Get explicit type if specified
            explicit_type = parameter_types.get(param_name)
            
            if isinstance(param_range, tuple):
                # Continuous/discrete range [min, max]
                min_val, max_val = param_range
                
                # Determine type: explicit or inferred
                if explicit_type == "integer":
                    param_type = "integer"
                elif explicit_type == "float":
                    param_type = "float"
                elif isinstance(min_val, int) and isinstance(max_val, int):
                    param_type = "integer"
                else:
                    param_type = "float"
                
                # Create hyperparameter
                if param_type == "integer":
                    hyperparameter = UniformIntegerHyperparameter(
                        name=param_name,
                        lower=min_val,
                        upper=max_val,
                        log=False  # Use uniform scale for faster sampling
                    )
                    num_values = max_val - min_val + 1
                    logger.info(f"  {param_name}: UniformInteger [{min_val}, {max_val}] ({num_values} discrete values)")
                else:
                    # Float range
                    hyperparameter = UniformFloatHyperparameter(
                        name=param_name,
                        lower=float(min_val),
                        upper=float(max_val),
                        log=False  # Use uniform scale for faster sampling
                    )
                    logger.info(f"  {param_name}: UniformFloat [{min_val}, {max_val}] (continuous)")
                
                cs.add_hyperparameter(hyperparameter)
                
            elif isinstance(param_range, list):
                # Categorical - list of valid values
                # Convert all values to strings for ConfigSpace (it handles conversion back)
                choices = [str(v) for v in param_range]
                hyperparameter = CategoricalHyperparameter(
                    name=param_name,
                    choices=choices
                )
                cs.add_hyperparameter(hyperparameter)
                logger.info(f"  {param_name}: Categorical {choices} ({len(choices)} choices)")
            else:
                logger.warning(f"Unknown parameter range type for {param_name}: {type(param_range)}. Skipping.")
        
        # Calculate total search space size
        total_space = 1
        for hp in cs.get_hyperparameters():
            if isinstance(hp, UniformIntegerHyperparameter):
                total_space *= (hp.upper - hp.lower + 1)
            elif isinstance(hp, CategoricalHyperparameter):
                total_space *= len(hp.choices)
            elif isinstance(hp, UniformFloatHyperparameter):
                total_space = float('inf')  # Continuous space
                break
        
        if total_space == float('inf'):
            logger.info(f"\nTotal search space: CONTINUOUS (infinite)")
        else:
            logger.info(f"\nTotal search space: {total_space:,} discrete configurations")
        
        logger.info("=" * 80)
        
        return cs
    
    def _dict_to_pandas_series(self, params: Dict[str, Any]) -> pd.Series:
        """Convert parameters dict to pandas Series for MLOS."""
        # Filter to only tunable parameters and ensure all values are hashable
        tunable_params = {}
        for k, v in params.items():
            if k not in self.parameter_ranges:
                continue
            
            # Extract value if it's a dict with 'value' key (for per-core parameters)
            if isinstance(v, dict):
                if 'value' in v:
                    v = v['value']
                else:
                    logger.warning(f"Parameter {k} is a dict but has no 'value' key: {v}")
                    continue
            
            # Ensure value is hashable (string for categoricals, number for continuous)
            param_range = self.parameter_ranges[k]
            if isinstance(param_range, list):
                # Categorical - ensure it's a string
                tunable_params[k] = str(v)
            elif isinstance(param_range, tuple):
                # Continuous - ensure it's a number
                if isinstance(v, (int, float)):
                    tunable_params[k] = v
                else:
                    # Try to convert
                    try:
                        min_val, max_val = param_range
                        if isinstance(min_val, int) and isinstance(max_val, int):
                            tunable_params[k] = int(v)
                        else:
                            tunable_params[k] = float(v)
                    except (ValueError, TypeError):
                        logger.warning(f"Could not convert {k}={v} to number, using as-is")
                        tunable_params[k] = v
            else:
                # Unknown type - convert to string as fallback
                tunable_params[k] = str(v) if not isinstance(v, (int, float, str)) else v
        
        return pd.Series(tunable_params, dtype=object)
    
    def _pandas_series_to_dict(self, series: pd.Series) -> Dict[str, Any]:
        """Convert pandas Series to parameters dict."""
        params = {}
        for param_name in self.parameter_ranges.keys():
            if param_name in series.index:
                value = series[param_name]
                # Convert back from string if it was categorical
                param_range = self.parameter_ranges[param_name]
                if isinstance(param_range, list):
                    # Categorical - try to convert back to original type
                    # Find original value in list
                    str_value = str(value)
                    for orig_val in param_range:
                        if str(orig_val) == str_value:
                            params[param_name] = orig_val
                            break
                    else:
                        # Fallback: use as-is
                        params[param_name] = value
                elif isinstance(param_range, tuple):
                    # Continuous - convert to appropriate type
                    min_val, max_val = param_range
                    if isinstance(min_val, int) and isinstance(max_val, int):
                        params[param_name] = int(value)
                    else:
                        params[param_name] = float(value)
                else:
                    params[param_name] = value
        
        # Combine with fixed parameters
        combined_params = self.fixed_parameters.copy() if self.fixed_parameters else {}
        combined_params.update(params)
        
        return combined_params
    
    def _initialize_optimizer(self):
        """Initialize MLOS optimizer on first use."""
        if self.mlos_optimizer is not None:
            return
        
        logger.info("Initializing MLOS SmacOptimizer...")
        
        # Create optimization targets list
        optimization_targets = [self.optimization_metric]
        
        self.mlos_optimizer = SmacOptimizer(
            parameter_space=self.configspace,
            optimization_targets=optimization_targets,
            objective_weights=self.objective_weights,
            space_adapter=None,  # Can be extended to support space adapters
            seed=self.seed if self.seed is not None else 0,
            run_name=self.run_name,
            output_directory=self.output_directory,
            max_trials=self.max_trials,
            n_random_init=self.n_random_init if self.n_random_init is not None else 3,
            max_ratio=self.max_ratio if self.max_ratio is not None else 0.1,
            use_default_config=self.use_default_config,
            n_random_probability=self.n_random_probability,
        )
        
        logger.info("MLOS SmacOptimizer initialized")
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, Any],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters using MLOS optimizer or manual random search."""
        try:
            # Determine if we should use manual random search or MLOS
            # Handle None case for n_random_init (default to 3 random samples if None)
            n_random = self.n_random_init if self.n_random_init is not None else 3
            use_manual_random = self.iteration_count < n_random
            
            if use_manual_random:
                logger.info(f"Using manual random search ({self.iteration_count + 1}/{n_random})")
                return self._suggest_random()
            
            # After random init, use MLOS Bayesian optimization
            logger.info(f"Using MLOS Bayesian optimization (iteration {self.iteration_count + 1})")
            
            # Initialize optimizer on first MLOS call
            self._initialize_optimizer()
            
            # Get reward/metric value
            reward = metrics.get_metric(self.optimization_metric)
            
            # Register current observation (if we have parameters to register)
            # Skip registration on very first call (iteration_count == 0) since we haven't evaluated anything yet
            if self.iteration_count > 0 and current_params:
                # Convert current_params to pandas Series
                params_series = self._dict_to_pandas_series(current_params)
                
                # Create context (empty for now, can be extended)
                context = None
                
                # Format score - use negative for maximize, positive for minimize
                if self.optimization_goal == "maximize":
                    score = -reward  # MLOS minimizes by default
                else:
                    score = reward
                
                # NOTE: Constraint penalties are applied upstream in optimizer.py
                # (multiplicative penalty on the reward) before it reaches this tuner.
                # Do NOT apply a second penalty here — it would double-punish violations
                # and create discontinuities that break the surrogate model.
                
                score_series = pd.Series({self.optimization_metric: score})
                
                logger.info(f"MLOS Registering observation - Config: {params_series.to_dict()}")
                
                # Create observation object
                observation = Observation(
                    config=params_series,
                    score=score_series,
                    context=context,
                    metadata=None
                )

                logger.info(f"MLOS Registering observation - Config: {observation}")
                
                try:
                    self.mlos_optimizer.register(Observations(observations=[observation]))
                    logger.info(f"MLOS Registered observation - Score: {score_series.to_dict()}")
                except AssertionError as e:
                    # SMAC internal assertion error during save - harmless, can be ignored
                    # This happens when SMAC's trial bookkeeping gets out of sync with ask/tell
                    logger.warning(f"SMAC internal assertion during save (harmless): {e}")
                    logger.info(f"Observation registered despite assertion - Score: {score_series.to_dict()}")
                except Exception as e:
                    logger.error(f"Error registering observation: {e}", exc_info=True)
                    # Try to debug the parameter types
                    for key, value in params_series.items():
                        try:
                            # Check if value is hashable
                            hash(value)
                        except TypeError:
                            logger.error(f"Unhashable value found: {key}={value} (type: {type(value)})")
            
            # Get new suggestion without timeout
            try:
                # Direct call to suggest() without timeout wrapper
                import time
                start_time = time.time()
                logger.info(f"Calling MLOS suggest() (blocking)")
                suggestion = self.mlos_optimizer.suggest()
                elapsed_time = time.time() - start_time
                logger.info(f"MLOS suggest() completed in {elapsed_time:.2f}s")
                
                # Convert suggestion to parameters dict
                suggested_params = self._pandas_series_to_dict(suggestion.config)
                logger.info(f"MLOS Suggested parameters: {suggested_params}")
                
                # Remove fixed parameters from suggested_params (they're already in fixed_parameters)
                # Only return tunable parameters
                tunable_params = {
                    k: v for k, v in suggested_params.items()
                    if k in self.parameter_ranges
                }
                
                self.iteration_count += 1
                
                return TunerResponse(
                    parameters=tunable_params,
                    confidence=1.0,
                    justification=f"MLOS SmacOptimizer suggestion (iteration {self.iteration_count})"
                )
            except StopIteration:
                # SMAC exhausted the search space or couldn't find new configs
                # Return current parameters unchanged (no further adjustments)
                logger.warning(f"MLOS suggest() exhausted search space. Returning current parameters unchanged.")
                
                # Extract only tunable parameters from current_params
                tunable_params = {
                    k: v for k, v in current_params.items()
                    if k in self.parameter_ranges
                }
                
                self.iteration_count += 1
                
                return TunerResponse(
                    parameters=tunable_params,
                    confidence=1.0,
                    justification=f"MLOS exhausted - no parameter changes (iteration {self.iteration_count})"
                )
        except Exception as e:
            logger.error(f"Error getting suggestion from MLOS optimizer: {e}", exc_info=True)
            # Fallback to current parameters
            return TunerResponse(
                parameters=current_params,
                confidence=0.0,
                justification=f"MLOS error, using current parameters: {e}"
            )
    
    def _suggest_random(self) -> TunerResponse:
        """Manually sample a random configuration from the search space."""
        try:
            # Sample random configuration from ConfigSpace
            random_config = self.configspace.sample_configuration()
            suggested_params = {}
            
            for param_name in self.parameter_ranges.keys():
                if param_name in random_config:
                    value = random_config[param_name]
                    param_range = self.parameter_ranges[param_name]
                    
                    # Convert back to original type
                    if isinstance(param_range, list):
                        # Categorical
                        str_value = str(value)
                        for orig_val in param_range:
                            if str(orig_val) == str_value:
                                suggested_params[param_name] = orig_val
                                break
                        else:
                            suggested_params[param_name] = value
                    elif isinstance(param_range, tuple):
                        # Integer/Float
                        min_val, max_val = param_range
                        if isinstance(min_val, int) and isinstance(max_val, int):
                            suggested_params[param_name] = int(value)
                        else:
                            suggested_params[param_name] = float(value)
                    else:
                        suggested_params[param_name] = value
            
            logger.info(f"Manual random suggested parameters: {suggested_params}")
            
            self.iteration_count += 1
            
            return TunerResponse(
                parameters=suggested_params,
                confidence=0.5,
                justification=f"Manual random initialization ({self.iteration_count}/{self.n_random_init})"
            )
            
        except Exception as e:
            logger.error(f"Error in manual random sampling: {e}", exc_info=True)
            # Fallback to first valid config
            fallback_params = {}
            for param_name, param_range in self.parameter_ranges.items():
                if isinstance(param_range, tuple):
                    fallback_params[param_name] = param_range[0]
                elif isinstance(param_range, list):
                    fallback_params[param_name] = param_range[0]
            
            self.iteration_count += 1
            
            return TunerResponse(
                parameters=fallback_params,
                confidence=0.0,
                justification=f"Error in random sampling, using fallback config: {e}"
            )
    
    def __del__(self):
        """Clean up MLOS optimizer."""
        if hasattr(self, 'mlos_optimizer') and self.mlos_optimizer is not None:
            try:
                self.mlos_optimizer.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up MLOS optimizer: {e}")

