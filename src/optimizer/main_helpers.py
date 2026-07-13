#!/usr/bin/env python3
"""
Helper functions extracted from main.py to allow use by optimizer.py
without circular imports.
"""

from optimizer.config import SimpleConfig


def create_tuner_from_config(config: SimpleConfig):
    """Create tuner instance based on config.
    
    This is extracted from main.py so optimizer.py can re-create the primary
    tuner after the trimming phase without circular imports.
    
    Args:
        config: Configuration object
        
    Returns:
        Tuner instance
    """
    if config.tuner_type == "fixed":
        from optimizer.tuners.fixed import FixedTuner
        return FixedTuner(config)
    elif config.tuner_type == "llm":
        from optimizer.tuners.llm import LLMTuner
        return LLMTuner(config, agent_type="single")
    elif config.tuner_type == "qlearning":
        from optimizer.tuners.qlearning import QLearningTuner
        return QLearningTuner(config)
    elif config.tuner_type == "bayesian":
        from optimizer.tuners.bayesian import BayesianOptimizerTuner
        return BayesianOptimizerTuner(config)
    elif config.tuner_type == "dqn":
        from optimizer.tuners.dqn import DQNTuner
        return DQNTuner(config)
    elif config.tuner_type == "mlos":
        from optimizer.tuners.mlos_tuner import MLOSTuner
        return MLOSTuner(config)
    else:
        raise ValueError(f"Unknown tuner type: {config.tuner_type}")
