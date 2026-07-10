#!/usr/bin/env python3
"""
Tuner implementations for OS parameter optimization.

This package provides various tuner implementations including:
- FixedTuner: Returns fixed parameter values
- LLMTuner: Uses LLM (Gemini) for parameter suggestions
- HumanTuner: Interactive tuner that prompts user for values
- QLearningTuner: Q-Learning based tuner with discretized action space
- BayesianOptimizerTuner: Bayesian optimization using SMAC3
- DQNTuner: Deep Q-Network based tuner
- MLOSTuner: MLOS SmacOptimizer wrapper with advanced features
"""

from barebones_optimizer.tuners.base import TunerInterface, TunerResponse
from barebones_optimizer.tuners.fixed import FixedTuner
from barebones_optimizer.tuners.llm import LLMTuner
from barebones_optimizer.tuners.human import HumanTuner
from barebones_optimizer.tuners.qlearning import QLearningTuner
from barebones_optimizer.tuners.bayesian import BayesianOptimizerTuner
from barebones_optimizer.tuners.dqn import DQNTuner
from barebones_optimizer.tuners.mlos_tuner import MLOSTuner
from barebones_optimizer.tuners.simple_smac_tuner import SimpleSMACTuner
from barebones_optimizer.tuners.llm_command import LLMCommandTuner
from barebones_optimizer.tuners.llm_trimming import LLMTrimmingTuner

__all__ = [
    'TunerInterface',
    'TunerResponse',
    'FixedTuner',
    'LLMTuner',
    'HumanTuner',
    'QLearningTuner',
    'BayesianOptimizerTuner',
    'DQNTuner',
    'MLOSTuner',
    'SimpleSMACTuner',
    'LLMCommandTuner',
    'LLMTrimmingTuner',
]

