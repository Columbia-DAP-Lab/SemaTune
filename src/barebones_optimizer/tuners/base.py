#!/usr/bin/env python3
"""
Base tuner interface and response types.

This module provides the abstract base class for all tuners and the response
dataclass used to communicate parameter suggestions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Any, List

from ..benchmark import BenchmarkMetrics


@dataclass
class TunerResponse:
    """Response from a tuner indicating parameter changes."""
    parameters: Dict[str, Any]  # Parameter name -> new value (can be int, str, bool, or dict with value/cores)
    confidence: Optional[float] = None  # Optional confidence score
    justification: Optional[str] = None  # Optional 1-2 sentence justification for the parameter changes
    commands: Optional[List[str]] = None  # Optional list of shell commands to execute
    converged: Optional[bool] = None  # Optional convergence flag from LLM
    response_time: Optional[float] = None  # Optional response time in seconds (for LLM tuners)
    token_metrics: Optional[Dict[str, Any]] = None  # Optional token usage metrics (input, output, thinking)


class TunerInterface(ABC):
    """Abstract base class for all tuners."""
    
    @abstractmethod
    def suggest_parameters(self, metrics: BenchmarkMetrics,
                         current_params: Dict[str, int],
                         iteration: int,
                         best_reward: float = 0.0,
                         aggregation_interval_s: Optional[float] = None,
                         **kwargs) -> TunerResponse:
        """Suggest new parameters based on metrics.
        
        Args:
            metrics: Current benchmark metrics
            current_params: Current parameter values
            iteration: Current iteration number
            best_reward: Best reward seen so far
            aggregation_interval_s: Optional interval over which metrics were aggregated (seconds)
            **kwargs: Additional arguments for specific tuner types
            
        Returns:
            TunerResponse with suggested parameters
        """
        pass

