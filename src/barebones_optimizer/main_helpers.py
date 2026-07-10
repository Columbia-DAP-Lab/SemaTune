#!/usr/bin/env python3
"""
Helper functions extracted from main.py to allow use by optimizer.py
without circular imports.
"""

from barebones_optimizer.config import SimpleConfig


def create_tuner_from_config(config: SimpleConfig):
    """Create tuner instance based on config.
    
    This is extracted from main.py so optimizer.py can re-create the primary
    tuner after the trimming phase without circular imports.
    
    Args:
        config: Configuration object
        
    Returns:
        Tuner instance
    """
    from barebones_optimizer.tuners import (
        FixedTuner, LLMTuner, HumanTuner, QLearningTuner,
        BayesianOptimizerTuner, DQNTuner, MLOSTuner, SimpleSMACTuner,
        LLMCommandTuner
    )
    
    if config.tuner_type == "fixed":
        return FixedTuner(config)
    elif config.tuner_type == "llm":
        return LLMTuner(config, agent_type="single")
    elif config.tuner_type == "human":
        return HumanTuner(config)
    elif config.tuner_type == "qlearning":
        return QLearningTuner(config)
    elif config.tuner_type == "bayesian":
        return BayesianOptimizerTuner(config)
    elif config.tuner_type == "dqn":
        return DQNTuner(config)
    elif config.tuner_type == "mlos":
        return MLOSTuner(config)
    elif config.tuner_type == "simple_smac":
        return SimpleSMACTuner(config)
    elif config.tuner_type == "llm_command":
        return LLMCommandTuner(config, agent_type="single")
    else:
        raise ValueError(f"Unknown tuner type: {config.tuner_type}")
