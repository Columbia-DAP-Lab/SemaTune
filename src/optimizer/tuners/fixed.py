#!/usr/bin/env python3
"""
Fixed tuner implementation.

This tuner returns fixed parameter values that don't change during optimization.
"""

import logging
from typing import Dict

from ..benchmark import BenchmarkMetrics
from .base import TunerInterface, TunerResponse

logger = logging.getLogger(__name__)


class FixedTuner(TunerInterface):
    """Fixed tuner that returns fixed parameter values."""
    
    def __init__(self, config):
        """Initialize fixed tuner.
        
        Args:
            config: Configuration object with fixed_parameters
        """
        self.config = config
        self.fixed_parameters = getattr(config, 'fixed_parameters', {})
        # Note: FixedTuner uses config.parameter_ranges directly in suggest_parameters
        # to filter fixed_parameters, so we don't need to filter here
    
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, int],
                         iteration: int,
                         best_reward: float = 0.0,
                         **kwargs) -> TunerResponse:
        """Return fixed parameters (no changes).
        
        Args:
            metrics: Current benchmark metrics (unused)
            current_params: Current parameter values (unused)
            iteration: Current iteration number (unused)
            best_reward: Best reward seen so far (unused)
            **kwargs: Additional arguments (unused, for forward compatibility)
            
        Returns:
            TunerResponse with fixed parameters
        """
        # Return fixed parameters, but only include tunable ones
        # Filter based on parameters_to_tune if specified
        if self.config.parameters_to_tune is not None:
            tunable_params = {
                k: v for k, v in self.fixed_parameters.items()
                if k in self.config.parameters_to_tune and k in self.config.parameter_ranges
            }
        else:
            tunable_params = {
                k: v for k, v in self.fixed_parameters.items()
                if k in self.config.parameter_ranges
            }
        return TunerResponse(parameters=tunable_params, confidence=1.0, justification=None)

